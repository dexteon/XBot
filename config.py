"""Versioned config schema with load/save/migrate."""

import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

CURRENT_SCHEMA_VERSION = 1
CONFIG_DIR = Path.home() / ".xbot"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class ModelConfig:
    name: str = "huihui_ai/qwen3-abliterated:8b"
    context_window: int = 8192
    temperature: float = 0.3
    top_p: float = 0.9
    repeat_penalty: float = 1.1


@dataclass
class Filters:
    search_terms: list = field(default_factory=lambda: ["cybersecurity", "hacking", "0day", "CVE"])
    exclude_terms: list = field(default_factory=lambda: ["drama", "politics", "scam"])
    watched_accounts: list = field(default_factory=lambda: [])
    videos_only: bool = True
    language: str = "en"
    max_age_hours: int = 24
    min_likes: int = 0
    min_retweets: int = 0
    # Trending
    use_trending: bool = True
    use_rapidapi: bool = True
    cyber_trends_only: bool = True
    max_trending_searches: int = 10
    # Feed mode
    use_feed_mode: bool = True
    max_feed_accounts: int = 50
    feed_videos_only: bool = True
    feed_min_likes: int = 50
    feed_min_retweets: int = 10


@dataclass
class Thresholds:
    min_relevance: int = 60
    min_quality: int = 70


@dataclass
class Actions:
    like: bool = True
    retweet: bool = True
    reply: bool = False
    download_video: bool = False


@dataclass
class RateLimit:
    max_per_hour: int = 10
    max_per_day: int = 50


@dataclass
class Schedule:
    interval_minutes: int = 60
    enabled: bool = True
    active_hours_start: str = "00:00"
    active_hours_end: str = "23:59"


@dataclass
class AntiBan:
    min_delay_seconds: int = 5
    max_delay_seconds: int = 30
    jitter: bool = True


@dataclass
class Config:
    schema_version: int = CURRENT_SCHEMA_VERSION
    model: ModelConfig = field(default_factory=ModelConfig)
    system_prompt: str = (
        "You are a content relevance classifier for cybersecurity.\n"
        "Score each tweet for relevance and quality.\n"
        "Be strict. Prefer technical depth over drama.\n"
        "Return ONLY valid JSON."
    )
    filters: Filters = field(default_factory=Filters)
    thresholds: Thresholds = field(default_factory=Thresholds)
    actions: Actions = field(default_factory=Actions)
    rate_limit: RateLimit = field(default_factory=RateLimit)
    schedule: Schedule = field(default_factory=Schedule)
    anti_ban: AntiBan = field(default_factory=AntiBan)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """Build Config from dict, migrating old versions."""
        version = data.get("schema_version", 0)
        data = _migrate(data, version)
        cfg = cls()
        cfg.model = ModelConfig(**data.get("model", {}))
        cfg.system_prompt = data.get("system_prompt", cfg.system_prompt)
        cfg.filters = Filters(**data.get("filters", {}))
        cfg.thresholds = Thresholds(**data.get("thresholds", {}))
        cfg.actions = Actions(**data.get("actions", {}))
        cfg.rate_limit = RateLimit(**data.get("rate_limit", {}))
        cfg.schedule = Schedule(**data.get("schedule", {}))
        cfg.anti_ban = AntiBan(**data.get("anti_ban", {}))
        cfg.schema_version = CURRENT_SCHEMA_VERSION
        return cfg


def _migrate(data: dict, from_version: int) -> dict:
    """Migrate config from old version to current."""
    # v0 → v1: initial schema, no migration needed
    if from_version < 1:
        data["schema_version"] = CURRENT_SCHEMA_VERSION
    # Future migrations go here:
    # if from_version < 2: ...
    return data


def load_config() -> Config:
    """Load config from disk, or create default."""
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return Config.from_dict(data)
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            print(f"Config load error, using defaults: {e}")
    return Config()


def save_config(cfg: Config) -> None:
    """Save config to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(cfg.to_json(), encoding="utf-8")


def get_default_config() -> Config:
    """Return a fresh default config."""
    return Config()
