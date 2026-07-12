"""SQLite database for dedup, action logging, and rate limiting."""

import sqlite3
import time
from datetime import datetime, date
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

DB_DIR = Path.home() / ".xbot"
DB_FILE = DB_DIR / "xbot.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_tweets (
    tweet_id      TEXT PRIMARY KEY,
    username      TEXT NOT NULL,
    content       TEXT,
    media_type    TEXT,
    fetched_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    scored_at     TIMESTAMP,
    relevance     INTEGER,
    quality       INTEGER,
    topic         TEXT,
    reason        TEXT,
    action_taken  TEXT,
    model_used    TEXT
);

CREATE TABLE IF NOT EXISTS action_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    action        TEXT NOT NULL,
    tweet_id      TEXT,
    username      TEXT,
    status        TEXT NOT NULL,
    error_message TEXT,
    latency_ms    INTEGER
);

CREATE TABLE IF NOT EXISTS rate_limits (
    date          TEXT NOT NULL,
    hour          INTEGER NOT NULL,
    action_count  INTEGER DEFAULT 0,
    PRIMARY KEY (date, hour)
);

CREATE INDEX IF NOT EXISTS idx_seen_scored ON seen_tweets(scored_at);
CREATE INDEX IF NOT EXISTS idx_action_ts ON action_log(timestamp);
"""


@dataclass
class TweetScore:
    tweet_id: str
    username: str
    content: str
    media_type: str
    relevance: int
    quality: int
    topic: str
    reason: str


class Database:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_FILE
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._connect()

    def _connect(self):
        for attempt in range(3):
            try:
                self._conn = sqlite3.connect(str(self.db_path), timeout=10)
                self._conn.row_factory = sqlite3.Row
                self._conn.executescript(_SCHEMA)
                self._conn.commit()
                return
            except sqlite3.OperationalError:
                if attempt < 2:
                    time.sleep(0.1)
                else:
                    raise

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        for attempt in range(3):
            try:
                cur = self._conn.execute(sql, params)
                self._conn.commit()
                return cur
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < 2:
                    time.sleep(0.1)
                else:
                    raise

    # ── Dedup ──────────────────────────────────────────────────────

    def filter_unseen(self, tweet_ids: list[str]) -> list[str]:
        """Return only tweet_ids not in seen_tweets."""
        if not tweet_ids:
            return []
        placeholders = ",".join("?" * len(tweet_ids))
        rows = self._conn.execute(
            f"SELECT tweet_id FROM seen_tweets WHERE tweet_id IN ({placeholders})",
            tuple(tweet_ids),
        ).fetchall()
        seen = {r["tweet_id"] for r in rows}
        return [tid for tid in tweet_ids if tid not in seen]

    def is_seen(self, tweet_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM seen_tweets WHERE tweet_id = ?", (tweet_id,)
        ).fetchone()
        return row is not None

    def mark_seen(self, tweet_id: str, username: str, content: str = "", media_type: str = ""):
        self._execute(
            """INSERT OR IGNORE INTO seen_tweets (tweet_id, username, content, media_type)
               VALUES (?, ?, ?, ?)""",
            (tweet_id, username, content[:500], media_type),
        )

    def record_score(self, score: TweetScore, model_used: str, action_taken: str):
        self._execute(
            """UPDATE seen_tweets SET
                 scored_at = ?, relevance = ?, quality = ?, topic = ?,
                 reason = ?, action_taken = ?, model_used = ?
               WHERE tweet_id = ?""",
            (datetime.now().isoformat(), score.relevance, score.quality,
             score.topic, score.reason, action_taken, model_used, score.tweet_id),
        )

    # ── Action Log ─────────────────────────────────────────────────

    def log_action(self, action: str, tweet_id: str = "", username: str = "",
                   status: str = "success", error_message: str = "", latency_ms: int = 0):
        self._execute(
            """INSERT INTO action_log (action, tweet_id, username, status, error_message, latency_ms)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (action, tweet_id, username, status, error_message, latency_ms),
        )

    def get_recent_stats(self, hours: int = 24) -> dict:
        """Get action counts for the last N hours."""
        rows = self._conn.execute(
            """SELECT action, status, COUNT(*) as cnt
               FROM action_log
               WHERE timestamp >= datetime('now', ?)
               GROUP BY action, status""",
            (f"-{hours} hours",),
        ).fetchall()
        stats = {}
        for r in rows:
            key = f"{r['action']}_{r['status']}"
            stats[key] = r["cnt"]
        return stats

    def get_today_action_count(self) -> int:
        """Count successful actions today."""
        today = date.today().isoformat()
        row = self._conn.execute(
            """SELECT COUNT(*) as cnt FROM action_log
               WHERE date(timestamp) = ? AND status = 'success'
               AND action IN ('like', 'retweet', 'reply')""",
            (today,),
        ).fetchone()
        return row["cnt"] if row else 0

    # ── Rate Limiting ──────────────────────────────────────────────

    def get_hourly_count(self, dt: Optional[datetime] = None) -> int:
        dt = dt or datetime.now()
        today_str = dt.date().isoformat()
        hour = dt.hour
        row = self._conn.execute(
            "SELECT action_count FROM rate_limits WHERE date = ? AND hour = ?",
            (today_str, hour),
        ).fetchone()
        return row["action_count"] if row else 0

    def increment_hourly(self, dt: Optional[datetime] = None):
        dt = dt or datetime.now()
        today_str = dt.date().isoformat()
        hour = dt.hour
        self._execute(
            """INSERT INTO rate_limits (date, hour, action_count) VALUES (?, ?, 1)
               ON CONFLICT(date, hour) DO UPDATE SET action_count = action_count + 1""",
            (today_str, hour),
        )

    def check_rate_limit(self, max_per_hour: int, max_per_day: int) -> tuple[bool, str]:
        """Returns (within_limit, reason)."""
        hourly = self.get_hourly_count()
        if hourly >= max_per_hour:
            return False, f"Hourly limit reached ({hourly}/{max_per_hour})"
        today = self.get_today_action_count()
        if today >= max_per_day:
            return False, f"Daily limit reached ({today}/{max_per_day})"
        return True, ""

    # ── Maintenance ────────────────────────────────────────────────

    def cleanup_old(self, days: int = 30):
        """Remove seen_tweets and logs older than N days."""
        self._execute(
            "DELETE FROM seen_tweets WHERE fetched_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        self._execute(
            "DELETE FROM action_log WHERE timestamp < datetime('now', ?)",
            (f"-{days} days",),
        )

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
