"""Ollama classifier client — scores tweets, returns structured data."""

import json
import time
import logging
from dataclasses import dataclass
from typing import Optional
import requests

log = logging.getLogger("xbot.classifier")


@dataclass
class TweetScore:
    tweet_id: str
    username: str
    content: str
    media_type: str
    relevance: int    # 0-100
    quality: int      # 0-100
    topic: str        # zero-day, breach, tool-release, ctf, opinion, news, tutorial, other
    reason: str       # one sentence
    raw_response: str = ""


class ClassifierError(Exception):
    pass


class ClassifierTimeout(ClassifierError):
    pass


class ClassifierParseError(ClassifierError):
    pass


class Classifier:
    """Sends tweets to Ollama, gets back structured scores."""

    def __init__(self, host: str = "http://localhost:11434"):
        self.host = host.rstrip("/")
        self.timeout = 120  # per-tweet timeout (8B model can take 60s+ on first load)

    def health_check(self, model: str) -> bool:
        """Check if Ollama is reachable and model is available."""
        try:
            resp = requests.get(f"{self.host}/api/tags", timeout=5)
            if resp.status_code != 200:
                return False
            models = resp.json().get("models", [])
            return any(m.get("name", "") == model or m.get("model", "") == model for m in models)
        except requests.RequestException:
            return False

    def score_tweet(
        self,
        tweet_id: str,
        username: str,
        content: str,
        media_type: str,
        likes: int = 0,
        retweets: int = 0,
        age_hours: float = 0,
        model: str = "huihui_ai/qwen3-abliterated:8b",
        system_prompt: str = "",
        temperature: float = 0.3,
        top_p: float = 0.9,
        context_window: int = 8192,
    ) -> TweetScore:
        """Send a single tweet to the model and parse the score."""
        user_msg = self._build_user_msg(
            username, content, media_type, likes, retweets, age_hours
        )

        raw = self._call_ollama(
            model=model,
            system_prompt=system_prompt or self._default_system_prompt(),
            user_msg=user_msg,
            temperature=temperature,
            top_p=top_p,
            context_window=context_window,
        )

        score = self._parse_response(raw, tweet_id, username, content, media_type)
        score.raw_response = raw
        return score

    def _default_system_prompt(self) -> str:
        return (
            "You are a content relevance classifier for cybersecurity.\n"
            "Score each tweet for relevance and quality.\n"
            "Be strict. Prefer technical depth over drama.\n\n"
            "Return ONLY this JSON, nothing else:\n"
            '{"relevance": 0-100, "quality": 0-100, '
            '"topic": "zero-day|breach|tool-release|ctf|opinion|news|tutorial|other", '
            '"reason": "one sentence"}'
        )

    def _build_user_msg(self, username: str, content: str, media_type: str,
                        likes: int, retweets: int, age_hours: float) -> str:
        media = "video" if media_type == "video" else ("image" if media_type == "image" else "none")
        return (
            f"Tweet from @{username} ({likes} likes, {retweets} retweets):\n"
            f'"{content}"\n'
            f"Media: {media}\n"
            f"Posted: {age_hours:.0f}h ago\n\n"
            "Score this tweet. Return JSON only."
        )

    def _call_ollama(self, model: str, system_prompt: str, user_msg: str,
                     temperature: float, top_p: float, context_window: int) -> str:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            "stream": False,
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "num_ctx": context_window,
            },
        }
        try:
            t0 = time.time()
            resp = requests.post(
                f"{self.host}/api/chat",
                json=payload,
                timeout=self.timeout,
            )
            elapsed = (time.time() - t0) * 1000
            log.debug(f"Ollama response in {elapsed:.0f}ms")
            if resp.status_code != 200:
                raise ClassifierError(f"Ollama HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            return data.get("message", {}).get("content", "")
        except requests.Timeout:
            raise ClassifierTimeout(f"Ollama timed out after {self.timeout}s")
        except requests.RequestException as e:
            raise ClassifierError(f"Ollama request failed: {e}")

    def _parse_response(self, raw: str, tweet_id: str, username: str,
                        content: str, media_type: str) -> TweetScore:
        """Extract JSON from model response, handle malformed output."""
        # Try direct JSON parse first
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            # Try to find JSON in the response
            data = self._extract_json(raw)
            if data is None:
                raise ClassifierParseError(f"Could not parse JSON from: {raw[:200]}")

        return TweetScore(
            tweet_id=tweet_id,
            username=username,
            content=content,
            media_type=media_type,
            relevance=int(self._clamp(data.get("relevance", 0))),
            quality=int(self._clamp(data.get("quality", 0))),
            topic=str(data.get("topic", "other")),
            reason=str(data.get("reason", "")),
        )

    @staticmethod
    def _extract_json(text: str) -> Optional[dict]:
        """Try to find a JSON object in arbitrary text."""
        import re
        # Find first { ... } block
        match = re.search(r'\{[^{}]+\}', text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return None

    @staticmethod
    def _clamp(val, lo=0, hi=100) -> int:
        try:
            return max(lo, min(hi, int(val)))
        except (TypeError, ValueError):
            return lo
