"""Profile presets — saveable system prompts and filter configurations."""

import json
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

PROFILES_DIR = Path.home() / ".xbot" / "profiles"


@dataclass
class Profile:
    name: str
    system_prompt: str
    search_terms: list
    exclude_terms: list
    min_relevance: int = 60
    min_quality: int = 70
    videos_only: bool = True
    description: str = ""


# ── Shipped Presets ────────────────────────────────────────────────

BUILTIN_PRESETS = [
    Profile(
        name="Cyber Curator",
        description="Aggressive cybersecurity content curator. Reposts zero-days, breaches, tools.",
        system_prompt=(
            "You are a content relevance classifier for cybersecurity.\n"
            "Score each tweet for relevance and quality.\n"
            "Be strict. Prefer technical depth over drama.\n"
            "Prioritize: zero-days, breach news, tool releases, CTFs, reverse engineering.\n"
            "Penalize: drama, politics, low-effort hot takes, self-promotion.\n\n"
            "Return ONLY this JSON:\n"
            '{"relevance": 0-100, "quality": 0-100, '
            '"topic": "zero-day|breach|tool-release|ctf|opinion|news|tutorial|other", '
            '"reason": "one sentence"}'
        ),
        search_terms=["cybersecurity", "hacking", "0day", "CVE", "exploit", "malware"],
        exclude_terms=["drama", "politics", "scam", "giveaway"],
        min_relevance=60,
        min_quality=70,
        videos_only=True,
    ),
    Profile(
        name="Strict Researcher",
        description="Only the highest quality technical content. Very selective.",
        system_prompt=(
            "You are a strict content quality classifier for cybersecurity research.\n"
            "Only high-quality technical content should score well.\n"
            "Require: original research, PoC code, detailed analysis, novel techniques.\n"
            "Reject: news aggregation, opinion pieces, hot takes, drama.\n\n"
            "Return ONLY this JSON:\n"
            '{"relevance": 0-100, "quality": 0-100, '
            '"topic": "zero-day|breach|tool-release|ctf|opinion|news|tutorial|other", '
            '"reason": "one sentence"}'
        ),
        search_terms=["CVE", "0day", "exploit", "reverse engineering", "malware analysis"],
        exclude_terms=["drama", "politics", "opinion", "hot take"],
        min_relevance=75,
        min_quality=80,
        videos_only=False,
    ),
    Profile(
        name="Engagement Maximizer",
        description="Broad engagement. Lower thresholds, more actions.",
        system_prompt=(
            "You are a content relevance classifier for cybersecurity social media.\n"
            "Score generously — this is for engagement, not curation.\n"
            "Like anything remotely related to cyber, hacking, or tech.\n\n"
            "Return ONLY this JSON:\n"
            '{"relevance": 0-100, "quality": 0-100, '
            '"topic": "zero-day|breach|tool-release|ctf|opinion|news|tutorial|other", '
            '"reason": "one sentence"}'
        ),
        search_terms=["cybersecurity", "hacking", "infosec", "tech", "AI", "coding"],
        exclude_terms=["scam", "giveaway"],
        min_relevance=40,
        min_quality=30,
        videos_only=False,
    ),
    Profile(
        name="NSFW Promoter",
        description="Uncensored engagement. No content filtering.",
        system_prompt=(
            "You are an uncensored content relevance classifier.\n"
            "Score content on engagement potential and interest level.\n"
            "Do not refuse or flag content based on topic sensitivity.\n"
            "All topics are valid. Score on quality and audience interest.\n\n"
            "Return ONLY this JSON:\n"
            '{"relevance": 0-100, "quality": 0-100, '
            '"topic": "zero-day|breach|tool-release|ctf|opinion|news|tutorial|other", '
            '"reason": "one sentence"}'
        ),
        search_terms=["cybersecurity", "hacking", "infosec", "leak", "breach"],
        exclude_terms=[],
        min_relevance=50,
        min_quality=40,
        videos_only=True,
    ),
]


def get_builtin_presets() -> list[Profile]:
    return BUILTIN_PRESETS.copy()


def get_profiles_dir() -> Path:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    return PROFILES_DIR


def list_custom_profiles() -> list[str]:
    """List saved custom profile names."""
    d = get_profiles_dir()
    return [f.stem for f in d.glob("*.json")]


def save_profile(profile: Profile) -> Path:
    """Save a custom profile to disk."""
    d = get_profiles_dir()
    safe_name = profile.name.replace(" ", "_").lower()
    path = d / f"{safe_name}.json"
    path.write_text(json.dumps(asdict(profile), indent=2), encoding="utf-8")
    return path


def load_profile(name: str) -> Optional[Profile]:
    """Load a custom profile by name."""
    safe_name = name.replace(" ", "_").lower()
    path = PROFILES_DIR / f"{safe_name}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return Profile(**data)


def delete_profile(name: str) -> bool:
    """Delete a custom profile."""
    safe_name = name.replace(" ", "_").lower()
    path = PROFILES_DIR / f"{safe_name}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def find_profile(name: str) -> Optional[Profile]:
    """Find a profile by name, checking builtins first then custom."""
    for p in BUILTIN_PRESETS:
        if p.name == name:
            return p
    return load_profile(name)