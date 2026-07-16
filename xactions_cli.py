"""XActions CLI wrapper — subprocess calls for Twitter actions."""

import subprocess
import json
import time
import logging
import shutil
import os
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

log = logging.getLogger("xbot.xactions")


def _find_xactions() -> str:
    """Find xactions executable, handling Windows .cmd files."""
    # Try plain name first (works if on PATH in the current shell)
    found = shutil.which("xactions")
    if found:
        return found
    # Try npm global bin directory
    npm_bin = Path.home() / "AppData" / "Roaming" / "npm"
    for ext in ["", ".cmd", ".ps1"]:
        candidate = npm_bin / f"xactions{ext}"
        if candidate.exists():
            return str(candidate)
    return "xactions"  # fallback, will error with helpful message


@dataclass
class Tweet:
    tweet_id: str
    username: str
    content: str
    media_type: str = "none"  # video, image, none
    likes: int = 0
    retweets: int = 0
    age_hours: float = 0
    url: str = ""


class XActionsError(Exception):
    pass


class XActionsAuthError(XActionsError):
    pass


class XActions:
    """Wrap XActions CLI via subprocess. Each call is logged."""

    def __init__(self, timeout: int = 60):
        self.timeout = timeout

    def _run(self, args: list[str], timeout: Optional[int] = None) -> tuple[str, int]:
        """Run xactions command, return (output, returncode)."""
        xactions_bin = _find_xactions()
        # On Windows, .cmd files need shell=True
        use_shell = xactions_bin.endswith(".cmd")
        cmd = [xactions_bin] + args if not use_shell else xactions_bin + " " + " ".join(args)
        t0 = time.time()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
                shell=use_shell,
            )
            elapsed = (time.time() - t0) * 1000
            if result.returncode != 0:
                stderr = result.stderr.strip()
                if "auth" in stderr.lower() or "login" in stderr.lower() or "cookie" in stderr.lower():
                    raise XActionsAuthError(stderr)
                log.warning(f"xactions {' '.join(args[:2])} failed ({result.returncode}): {stderr[:200]}")
            else:
                log.debug(f"xactions {' '.join(args[:2])} OK in {elapsed:.0f}ms")
            return result.stdout.strip(), result.returncode
        except subprocess.TimeoutExpired:
            raise XActionsError(f"xactions timed out after {timeout or self.timeout}s")
        except FileNotFoundError:
            raise XActionsError("xactions not found. Install with: npm install -g xactions")

    # ── Search ─────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 50) -> list[Tweet]:
        """Search tweets, return list of Tweet objects."""
        output, rc = self._run(["search", query, "--limit", str(limit)])
        if rc != 0:
            log.error(f"Search failed for '{query}': {output[:200]}")
            return []
        return self._parse_tweets(output)

    def get_tweets(self, username: str, limit: int = 10) -> list[Tweet]:
        """Get tweets from a specific user."""
        username = username.lstrip("@")
        output, rc = self._run(["tweets", username, "--limit", str(limit)])
        if rc != 0:
            log.error(f"Get tweets failed for @{username}: {output[:200]}")
            return []
        return self._parse_tweets(output)

    def get_recommendations(self) -> list[Tweet]:
        """Get recommended tweets (algorithm feed)."""
        output, rc = self._run(["scrape", "recommendations"], timeout=90)
        if rc != 0:
            log.error(f"Recommendations failed: {output[:200]}")
            return []
        return self._parse_tweets(output)

    # ── Actions ────────────────────────────────────────────────────

    def like(self, tweet_id: str, tweet_url: str = "") -> bool:
        """Like a tweet via browser automation. Returns True on success."""
        from browser_actions import like_tweet
        url = tweet_url or f"https://x.com/i/web/status/{tweet_id}"
        try:
            success, msg = like_tweet(url)
            if success:
                log.info(f"Liked {tweet_id}")
                return True
            else:
                log.warning(f"Like failed for {tweet_id}: {msg}")
                return False
        except Exception as e:
            log.error(f"Like exception for {tweet_id}: {e}")
            return False

    def retweet(self, tweet_id: str, tweet_url: str = "") -> bool:
        """Retweet via browser automation. Returns True on success."""
        from browser_actions import retweet_tweet
        url = tweet_url or f"https://x.com/i/web/status/{tweet_id}"
        try:
            success, msg = retweet_tweet(url)
            if success:
                log.info(f"Retweeted {tweet_id}")
                return True
            else:
                log.warning(f"Retweet failed for {tweet_id}: {msg}")
                return False
        except Exception as e:
            log.error(f"Retweet exception for {tweet_id}: {e}")
            return False

    def download_video(self, tweet_url: str, dest: str = ".") -> bool:
        """Download video from a tweet."""
        try:
            output, rc = self._run(["media", "download", tweet_url, "--dest", dest], timeout=120)
            return rc == 0
        except XActionsError as e:
            log.error(f"Download failed for {tweet_url}: {e}")
            return False

    # ── Health ─────────────────────────────────────────────────────

    def check_auth(self) -> bool:
        """Verify auth is still valid by checking a real account."""
        try:
            output, rc = self._run(["profile", "TheRandomNote"], timeout=15)
            # If we got profile data back, auth is working
            if rc == 0 and "Followers" in output:
                return True
            if "auth" in output.lower() or "login" in output.lower() or "cookie" in output.lower():
                return False
            # If it returned data but not the expected format, auth probably still works
            return rc == 0
        except XActionsAuthError:
            return False
        except XActionsError:
            # Can't tell — don't block the bot, let it try and fail naturally
            return True

    # ── Parsing ────────────────────────────────────────────────────

    def _parse_tweets(self, output: str) -> list[Tweet]:
        """Parse XActions CLI output into Tweet objects.
        
        Handles both JSON and text output formats.
        XActions prints status lines before the JSON.
        """
        tweets = []
        if not output:
            return tweets

        # Try JSON first — find the first [ or { in the output
        json_start = -1
        for i, char in enumerate(output):
            if char in "[{":
                json_start = i
                break
        
        if json_start >= 0:
            json_str = output[json_start:]
            # Find matching closing bracket
            try:
                data = json.loads(json_str)
                if isinstance(data, list):
                    for item in data:
                        tweets.append(self._tweet_from_json(item))
                elif isinstance(data, dict) and "tweets" in data:
                    for item in data["tweets"]:
                        tweets.append(self._tweet_from_json(item))
                return tweets
            except json.JSONDecodeError:
                pass  # fall through to text parsing

        # Fall back to text parsing
        return self._parse_text_output(output)

    def _tweet_from_json(self, item: dict) -> Tweet:
        media = "none"
        if item.get("has_video") or item.get("video") or item.get("videos"):
            media = "video"
        elif item.get("has_image") or item.get("photos") or item.get("images"):
            media = "image"

        def _int(val, default=0):
            try:
                return int(val)
            except (TypeError, ValueError):
                return default

        # XActions uses 'author' for username, 'text' for content
        username = str(
            item.get("username", "")
            or item.get("author", "")
            or item.get("user", {}).get("screen_name", "")
            or item.get("handle", "")
        )

        tweet_id = str(item.get("id", item.get("tweet_id", "")))

        # Compute age in hours from timestamp
        age_hours = 0.0
        ts = item.get("timestamp", item.get("created_at", ""))
        if ts:
            try:
                from datetime import datetime
                # Parse ISO format
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                age_hours = (datetime.now(dt.tzinfo) - dt).total_seconds() / 3600
            except (ValueError, TypeError):
                pass

        return Tweet(
            tweet_id=tweet_id,
            username=username,
            content=str(item.get("text", item.get("content", ""))),
            media_type=media,
            likes=_int(item.get("likes", item.get("favorite_count", 0))),
            retweets=_int(item.get("retweets", item.get("retweet_count", item.get("retweets", 0)))),
            age_hours=age_hours,
            url=str(item.get("url", item.get("link", ""))),
        )

    def _parse_text_output(self, output: str) -> list[Tweet]:
        """Parse text output as fallback."""
        tweets = []
        for line in output.split("\n"):
            line = line.strip()
            if not line:
                continue
            # XActions text output varies; extract what we can
            # Look for patterns like @username: "content"
            import re
            match = re.match(r'@?(\w+):\s*(.*)', line)
            if match:
                tweets.append(Tweet(
                    tweet_id="",  # may not be available in text mode
                    username=match.group(1),
                    content=match.group(2)[:500],
                    media_type="none",
                ))
        return tweets
