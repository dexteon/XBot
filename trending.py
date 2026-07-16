"""Trending topics fetcher — pulls from Twitter154 RapidAPI + XActions trending."""

import subprocess
import json
import logging
import shutil
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger("xbot.trending")

RAPIDAPI_KEY_ENV = "RAPIDAPI_KEY"
TWITTER154_HOST = "twitter154.p.rapidapi.com"


def _find_xactions() -> str:
    """Find xactions executable (same logic as xactions_cli.py)."""
    found = shutil.which("xactions")
    if found:
        return found
    npm_bin = Path.home() / "AppData" / "Roaming" / "npm"
    for ext in ["", ".cmd", ".ps1"]:
        candidate = npm_bin / f"xactions{ext}"
        if candidate.exists():
            return str(candidate)
    return "xactions"


def get_trends_rapidapi() -> list[str]:
    """Fetch trending topics from Twitter154 via RapidAPI.

    Returns list of trend names (strings).
    Uses RAPIDAPI_KEY from environment.
    """
    import requests

    api_key = os.environ.get(RAPIDAPI_KEY_ENV, "")
    if not api_key:
        log.warning("RAPIDAPI_KEY not set — skipping RapidAPI trends")
        return []

    headers = {
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": TWITTER154_HOST,
    }
    try:
        resp = requests.get(
            f"https://{TWITTER154_HOST}/trends/",
            headers=headers,
            timeout=15,
        )
        if resp.status_code != 200:
            log.error(f"RapidAPI trends returned {resp.status_code}")
            return []

        data = resp.json()
        trends = []
        # Twitter154 returns [{"trends": [{"name": "...", "query": "..."}], "as_of": ...}]
        if isinstance(data, list) and data:
            for item in data[0].get("trends", []):
                name = item.get("name", "")
                if name:
                    trends.append(name)
        return trends
    except requests.RequestException as e:
        log.error(f"RapidAPI trends request failed: {e}")
        return []
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        log.error(f"RapidAPI trends parse error: {e}")
        return []


def get_trends_xactions() -> list[str]:
    """Fetch trending topics via XActions CLI (scrape trending).

    Returns list of trend topic strings.
    """
    try:
        xactions_bin = _find_xactions()
        use_shell = xactions_bin.endswith(".cmd")
        cmd = [xactions_bin, "scrape", "trending", "--json"] if not use_shell \
            else f'"{xactions_bin}" scrape trending --json'
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            shell=use_shell,
        )
        if result.returncode != 0:
            log.warning(f"XActions trending failed: {result.stderr[:200]}")
            return []

        # Parse JSON from output (skip status lines)
        output = result.stdout.strip()
        json_start = -1
        for i, char in enumerate(output):
            if char == "[":
                json_start = i
                break

        if json_start < 0:
            return []

        data = json.loads(output[json_start:])
        trends = []
        for item in data:
            topic = item.get("topic", "")
            # XActions format: "Sports · Trending" — extract the actual topic
            if " · " in topic:
                topic = topic.split(" · ")[-1]
            if topic and "Trending in" not in topic:
                trends.append(topic)
        return trends
    except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError) as e:
        log.warning(f"XActions trending parse failed: {e}")
        return []


def get_trends(use_rapidapi: bool = True, use_xactions: bool = True) -> list[str]:
    """Fetch trending topics from all configured sources.

    Args:
        use_rapidapi: Try Twitter154 RapidAPI first
        use_xactions: Fall back to XActions scrape trending

    Returns:
        Deduplicated list of trending topic strings.
    """
    all_trends = []

    if use_rapidapi:
        trends = get_trends_rapidapi()
        if trends:
            log.info(f"RapidAPI: {len(trends)} trends")
            all_trends.extend(trends)

    if use_xactions and len(all_trends) < 5:
        # Only use XActions if RapidAPI didn't return enough
        trends = get_trends_xactions()
        if trends:
            log.info(f"XActions: {len(trends)} trends")
            all_trends.extend(trends)

    # Deduplicate (case-insensitive)
    seen = set()
    unique = []
    for t in all_trends:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            unique.append(t)

    return unique


def filter_trends_for_cyber(trends: list[str], model=None) -> list[str]:
    """Optionally filter trending topics to cyber-relevant ones.

    Args:
        trends: Raw trend list
        model: If provided, use Ollama model to score relevance
               If None, use keyword matching only.

    Returns:
        Filtered list of trends relevant to cybersecurity.
    """
    # Quick keyword filter
    cyber_keywords = [
        "hack", "cyber", "security", "breach", "vulnerability", "exploit",
        "malware", "ransomware", "phishing", "zero-day", "0day", "CVE",
        "data leak", "ransom", "botnet", "DDoS", "encryption", "privacy",
        "surveillance", "NSA", "CIA", "FBI", "leak", "dox", "doxx",
        "infosec", "opsec", "red team", "blue team", "SOC", "CISO",
    ]

    cyber_trends = []
    for trend in trends:
        trend_lower = trend.lower()
        if any(kw.lower() in trend_lower for kw in cyber_keywords):
            cyber_trends.append(trend)

    return cyber_trends if cyber_trends else trends  # fallback to all if no cyber match