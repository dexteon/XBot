"""Core bot engine — pipeline orchestration with full error handling."""

import time
import random
import logging
from datetime import datetime
from dataclasses import dataclass
from typing import Callable, Optional
from enum import Enum

from config import Config
from database import Database, TweetScore
from classifier import Classifier, ClassifierError, ClassifierTimeout, ClassifierParseError
from xactions_cli import XActions, Tweet, XActionsAuthError, XActionsError

log = logging.getLogger("xbot.engine")


class RunStatus(Enum):
    OK = "ok"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class RunResult:
    status: RunStatus
    fetched: int = 0
    new_tweets: int = 0
    scored: int = 0
    engaged: int = 0
    skipped: int = 0
    errors: int = 0
    message: str = ""


class BotEngine:
    """Orchestrates the full pipeline: fetch → dedup → score → act → log."""

    def __init__(self, config: Config, log_callback: Optional[Callable] = None):
        self.config = config
        self.log_callback = log_callback or print
        self.db = Database()
        self.classifier = Classifier()
        self.xactions = XActions()
        self._running = False
        self._dry_run = False

    def _log(self, msg: str, level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        full = f"{ts}  {level:<5}  {msg}"
        self.log_callback(full)
        if level == "ERROR":
            log.error(msg)
        elif level == "WARN":
            log.warning(msg)
        else:
            log.info(msg)

    def stop(self):
        self._running = False

    def set_dry_run(self, val: bool):
        self._dry_run = val

    # ── Health Checks ──────────────────────────────────────────────

    def _health_check(self) -> tuple[bool, str]:
        """Verify prerequisites before running."""
        # Ollama
        if not self.classifier.health_check(self.config.model.name):
            return False, f"Ollama down or model '{self.config.model.name}' not available"
        # XActions auth
        try:
            if not self.xactions.check_auth():
                return False, "XActions auth expired — re-login needed (xactions login)"
        except XActionsError:
            return False, "XActions not accessible"
        # Active hours
        now = datetime.now()
        start = self._parse_time(self.config.schedule.active_hours_start)
        end = self._parse_time(self.config.schedule.active_hours_end)
        current_min = now.hour * 60 + now.minute
        if not (start <= current_min <= end):
            return False, f"Outside active hours ({self.config.schedule.active_hours_start}-{self.config.schedule.active_hours_end})"

        return True, ""

    @staticmethod
    def _parse_time(s: str) -> int:
        h, m = s.split(":")
        return int(h) * 60 + int(m)

    # ── Main Pipeline ──────────────────────────────────────────────

    def run_once(self) -> RunResult:
        """Execute one full pipeline run."""
        self._running = True
        self._dry_run = False
        return self._execute()

    def run_dry(self) -> RunResult:
        """Score everything but don't execute actions."""
        self._running = True
        self._dry_run = True
        return self._execute()

    def _execute(self) -> RunResult:
        result = RunResult(status=RunStatus.OK)

        # 1. Health check
        ok, msg = self._health_check()
        if not ok:
            self._log(msg, "ERROR")
            result.status = RunStatus.SKIPPED
            result.message = msg
            return result

        # 2. Fetch tweets
        self._log("Fetching tweets...")
        tweets = self._fetch_tweets()
        result.fetched = len(tweets)
        if not tweets:
            self._log("No tweets fetched", "WARN")
            result.message = "No tweets fetched"
            return result
        self._log(f"Fetched {len(tweets)} tweets")

        # 3. Dedup
        tweet_ids = [t.tweet_id for t in tweets if t.tweet_id]
        if tweet_ids:
            new_ids = self.db.filter_unseen(tweet_ids)
            tweets = [t for t in tweets if t.tweet_id in new_ids or not t.tweet_id]
        result.new_tweets = len(tweets)
        removed = result.fetched - result.new_tweets
        if removed > 0:
            self._log(f"Dedup: {removed} already seen, {result.new_tweets} new")
        else:
            self._log(f"{result.new_tweets} new tweets to score")

        if not tweets:
            self._log("Nothing new to process")
            result.message = "All caught up"
            return result

        # 4. Score each tweet
        for tweet in tweets:
            if not self._running:
                self._log("Bot stopped", "WARN")
                break

            # Pre-filter: excluded terms
            if self._has_excluded_terms(tweet.content):
                self.db.mark_seen(tweet.tweet_id, tweet.username, tweet.content, tweet.media_type)
                self.db.log_action("skip", tweet.tweet_id, tweet.username, "skip", "excluded term")
                self._log(f"SKIP   @{tweet.username} — excluded term")
                result.skipped += 1
                continue

            # Pre-filter: engagement minimums
            if tweet.likes < self.config.filters.min_likes or tweet.retweets < self.config.filters.min_retweets:
                self.db.mark_seen(tweet.tweet_id, tweet.username, tweet.content, tweet.media_type)
                self.db.log_action("skip", tweet.tweet_id, tweet.username, "skip", "low engagement")
                self._log(f"SKIP   @{tweet.username} — low engagement ({tweet.likes} likes)")
                result.skipped += 1
                continue

            # Pre-filter: videos only
            if self.config.filters.videos_only and tweet.media_type != "video":
                self.db.mark_seen(tweet.tweet_id, tweet.username, tweet.content, tweet.media_type)
                self.db.log_action("skip", tweet.tweet_id, tweet.username, "skip", "not video")
                self._log(f"SKIP   @{tweet.username} — not a video")
                result.skipped += 1
                continue

            # Mark as seen before scoring
            self.db.mark_seen(tweet.tweet_id, tweet.username, tweet.content, tweet.media_type)

            # Score with model
            score = self._score_tweet(tweet)
            if score is None:
                result.errors += 1
                continue
            result.scored += 1

            self._log(
                f"SCORE  @{tweet.username} \"{tweet.content[:60]}...\" "
                f"rel={score.relevance} qual={score.quality} topic={score.topic}"
            )

            # 5. Apply thresholds (deterministic, Python)
            if self._meets_thresholds(score):
                action = self._execute_actions(tweet, score)
                if action:
                    result.engaged += 1
                else:
                    result.errors += 1
            else:
                self.db.record_score(score, self.config.model.name, "skipped")
                self.db.log_action("skip", tweet.tweet_id, tweet.username, "skip",
                                   f"below threshold (rel={score.relevance}, qual={score.quality})")
                self._log(
                    f"SKIP   @{tweet.username} — below threshold "
                    f"(rel={score.relevance}<{self.config.thresholds.min_relevance} "
                    f"or qual={score.quality}<{self.config.thresholds.min_quality})"
                )
                result.skipped += 1

        # 6. Done
        mode = "DRY RUN" if self._dry_run else ""
        self._log(
            f"Run complete {mode}: {result.fetched} fetched, {result.new_tweets} new, "
            f"{result.scored} scored, {result.engaged} engaged, {result.skipped} skipped, "
            f"{result.errors} errors"
        )
        result.message = "Complete"
        return result

    # ── Pipeline Steps ─────────────────────────────────────────────

    def _fetch_tweets(self) -> list[Tweet]:
        """Fetch from search terms + watched accounts + trending + feed."""
        all_tweets = []

        # 1. Search by terms
        for term in self.config.filters.search_terms:
            if not self._running:
                break
            try:
                tweets = self.xactions.search(term, limit=50)
                all_tweets.extend(tweets)
            except XActionsAuthError:
                self._log("XActions auth expired!", "ERROR")
                break
            except XActionsError as e:
                self._log(f"Search failed for '{term}': {e}", "WARN")

        # 2. Get tweets from watched accounts
        for account in self.config.filters.watched_accounts:
            if not self._running:
                break
            try:
                tweets = self.xactions.get_tweets(account, limit=10)
                all_tweets.extend(tweets)
            except XActionsAuthError:
                self._log("XActions auth expired!", "ERROR")
                break
            except XActionsError as e:
                self._log(f"Failed to get tweets from {account}: {e}", "WARN")

        # 3. Trending topics (if enabled)
        if self.config.filters.use_trending:
            try:
                from trending import get_trends, filter_trends_for_cyber
                self._log("Fetching trending topics...")
                trends = get_trends(
                    use_rapidapi=self.config.filters.use_rapidapi,
                    use_xactions=True,
                )
                if trends:
                    self._log(f"Got {len(trends)} trending topics")
                    # Filter to cyber-relevant if enabled
                    if self.config.filters.cyber_trends_only:
                        trends = filter_trends_for_cyber(trends)
                        self._log(f"Cyber-filtered: {len(trends)} relevant trends")

                    # Search top N trends
                    max_trends = min(len(trends), self.config.filters.max_trending_searches)
                    for trend in trends[:max_trends]:
                        if not self._running:
                            break
                        try:
                            tweets = self.xactions.search(trend, limit=20)
                            all_tweets.extend(tweets)
                            self._log(f"  Trend '{trend}': {len(tweets)} tweets")
                        except XActionsError as e:
                            self._log(f"  Trend '{trend}' search failed: {e}", "WARN")
                else:
                    self._log("No trends fetched", "WARN")
            except Exception as e:
                self._log(f"Trending fetch failed: {e}", "WARN")

        # 4. Feed mode — pull from people you follow
        if self.config.filters.use_feed_mode:
            try:
                following = self._get_following_accounts()
                if following:
                    self._log(f"Feed mode: scanning {len(following)} followed accounts")
                    max_accounts = min(len(following), self.config.filters.max_feed_accounts)
                    for account in following[:max_accounts]:
                        if not self._running:
                            break
                        try:
                            tweets = self.xactions.get_tweets(account, limit=5)
                            # Filter to videos only if configured
                            if self.config.filters.feed_videos_only:
                                tweets = [t for t in tweets if t.media_type == "video"]
                            # Filter by engagement
                            tweets = [t for t in tweets
                                      if t.likes >= self.config.filters.feed_min_likes
                                      and t.retweets >= self.config.filters.feed_min_retweets]
                            all_tweets.extend(tweets)
                        except XActionsError as e:
                            log.debug(f"Failed to get tweets from @{account}: {e}")
            except Exception as e:
                self._log(f"Feed mode failed: {e}", "WARN")

        # Deduplicate within the batch by tweet_id
        seen_ids = set()
        unique = []
        for t in all_tweets:
            key = t.tweet_id or f"{t.username}:{t.content[:50]}"
            if key not in seen_ids:
                seen_ids.add(key)
                unique.append(t)

        return unique

    def _get_following_accounts(self) -> list[str]:
        """Get list of accounts you follow via XActions CLI."""
        try:
            result = subprocess.run(
                ["xactions", "following", "TheRandomNote", "--limit", "100", "--json"],
                capture_output=True, text=True, timeout=60, shell=False,
            )
            if result.returncode != 0:
                self._log(f"Failed to get following list: {result.stderr[:200]}", "WARN")
                return []

            output = result.stdout.strip()
            # Find JSON in output
            json_start = -1
            for i, char in enumerate(output):
                if char == "[":
                    json_start = i
                    break
            if json_start < 0:
                return []

            data = json.loads(output[json_start:])
            accounts = []
            for item in data:
                username = item.get("username", item.get("screen_name", item.get("handle", "")))
                if username:
                    accounts.append(username.lstrip("@"))
            return accounts
        except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError) as e:
            self._log(f"Following list parse error: {e}", "WARN")
            return []

    def _has_excluded_terms(self, content: str) -> bool:
        content_lower = content.lower()
        return any(term.lower() in content_lower for term in self.config.filters.exclude_terms)

    def _score_tweet(self, tweet: Tweet) -> Optional[TweetScore]:
        """Send tweet to model, handle errors gracefully."""
        try:
            score = self.classifier.score_tweet(
                tweet_id=tweet.tweet_id,
                username=tweet.username,
                content=tweet.content,
                media_type=tweet.media_type,
                likes=tweet.likes,
                retweets=tweet.retweets,
                age_hours=tweet.age_hours,
                model=self.config.model.name,
                system_prompt=self.config.system_prompt,
                temperature=self.config.model.temperature,
                top_p=self.config.model.top_p,
                context_window=self.config.model.context_window,
            )
            return score
        except ClassifierTimeout:
            self._log(f"ERROR  Ollama timeout on tweet {tweet.tweet_id} — skipped", "ERROR")
            self.db.log_action("error", tweet.tweet_id, tweet.username, "error", "model timeout")
            return None
        except ClassifierParseError as e:
            self._log(f"ERROR  Parse error on tweet {tweet.tweet_id}: {e}", "ERROR")
            self.db.log_action("error", tweet.tweet_id, tweet.username, "error", "parse error")
            return None
        except ClassifierError as e:
            self._log(f"ERROR  Model error on tweet {tweet.tweet_id}: {e}", "ERROR")
            self.db.log_action("error", tweet.tweet_id, tweet.username, "error", str(e))
            return None

    def _meets_thresholds(self, score: TweetScore) -> bool:
        return (
            score.relevance >= self.config.thresholds.min_relevance
            and score.quality >= self.config.thresholds.min_quality
        )

    def _execute_actions(self, tweet: Tweet, score: TweetScore) -> bool:
        """Execute like/retweet with rate limiting and delays."""
        if self._dry_run:
            self._log(
                f"DRY RUN — would engage @{tweet.username} "
                f"(rel={score.relevance}, qual={score.quality})",
            )
            self.db.record_score(score, self.config.model.name, "dry_run")
            return True

        # Check rate limits
        within, reason = self.db.check_rate_limit(
            self.config.rate_limit.max_per_hour,
            self.config.rate_limit.max_per_day,
        )
        if not within:
            self._log(f"Rate limited: {reason}", "WARN")
            self.db.record_score(score, self.config.model.name, "rate_limited")
            return False

        actions_taken = []

        # Like
        if self.config.actions.like:
            self._anti_ban_delay()
            try:
                if self.xactions.like(tweet.tweet_id):
                    self.db.increment_hourly()
                    self.db.log_action("like", tweet.tweet_id, tweet.username, "success")
                    self._log(f"LIKE   @{tweet.username} — above threshold")
                    actions_taken.append("liked")
                else:
                    self.db.log_action("like", tweet.tweet_id, tweet.username, "fail", "xactions returned error")
                    self._log(f"ERROR  Like failed for {tweet.tweet_id}", "ERROR")
            except XActionsAuthError:
                self._log("XActions auth expired during like!", "ERROR")
                return False

        # Retweet
        if self.config.actions.retweet:
            self._anti_ban_delay()
            try:
                if self.xactions.retweet(tweet.tweet_id):
                    self.db.increment_hourly()
                    self.db.log_action("retweet", tweet.tweet_id, tweet.username, "success")
                    self._log(f"RETWEET @{tweet.username} — above threshold")
                    actions_taken.append("retweeted")
                else:
                    self.db.log_action("retweet", tweet.tweet_id, tweet.username, "fail", "xactions returned error")
                    self._log(f"ERROR  Retweet failed for {tweet.tweet_id}", "ERROR")
            except XActionsAuthError:
                self._log("XActions auth expired during retweet!", "ERROR")
                return False

        action_str = "+".join(actions_taken) if actions_taken else "engaged"
        self.db.record_score(score, self.config.model.name, action_str)
        return len(actions_taken) > 0

    def _anti_ban_delay(self):
        """Random delay between actions to look human."""
        lo = self.config.anti_ban.min_delay_seconds
        hi = self.config.anti_ban.max_delay_seconds
        delay = random.uniform(lo, hi) if self.config.anti_ban.jitter else (lo + hi) / 2
        time.sleep(delay)

    # ── Cleanup ────────────────────────────────────────────────────

    def cleanup(self):
        self.db.cleanup_old(days=30)
        self.db.close()
