# XBot — Ollama-Driven Twitter Engagement Bot

## Architecture Decision: Classifier, Not Agent

The model scores tweets. Python decides what to do. This is the core design principle.

The model NEVER decides actions. It returns structured scores and metadata. Python applies deterministic thresholds from user config. This makes the system:
- **Testable** — same input + same model = same output, no prompt drift
- **Debuggable** — you can see exactly why a tweet was engaged or skipped
- **Model-agnostic** — swap models without changing policy logic
- **Fast** — no multi-turn tool-call loops, single inference per tweet

### Model Output Schema

```json
{
  "relevance": 0-100,
  "quality": 0-100,
  "topic": "zero-day|breach|tool-release|ctf|opinion|news|tutorial|other",
  "nsfw": false,
  "reason": "one sentence explanation"
}
```

Python then applies the user's thresholds:

```python
if score.relevance >= config.min_relevance \
   and score.quality >= config.min_quality \
   and tweet.media_type == "video" or not config.videos_only \
   and not any(term in tweet.text for term in config.exclude_terms):
    actions.execute(tweet, score)
```

---

## What We Have Right Now

### Installed and Working

**XActions v3.0.0** — CLI installed globally, authenticated with your Twitter session cookie. Verified working (profile lookup on @TheRandomNote succeeded). Integration via CLI subprocess (not MCP — fewer moving parts, easier to log, fewer failure modes).

**Ollama Models (11 local + 38 cloud):**

| Model | Type | Size | Tool Calling | Role |
|-------|------|------|-------------|------|
| `huihui_ai/qwen3-abliterated:8b` | Local | 5.0 GB | N/A (classifier) | **DEFAULT** — primary scorer |
| `huihui_ai/dolphin3-abliterated:latest` | Local | 4.9 GB | N/A | Uncensored alternative |
| `blackgrg26/WORMGPT-12:latest` | Cloud | ~0 | N/A | Optional big-context fallback |
| `huihui_ai/deepseek-r1-abliterated:7b` | Local | 4.7 GB | N/A | Reasoning tasks (not for scoring) |
| `huihui_ai/qwen3-vl-abliterated:4b` | Local | 3.3 GB | YES | Optional vision (analyze video thumbnails) |
| `llama3.2:latest` | Local | 2.0 GB | N/A | Lightweight fallback |
| `glm-ocr:latest` | Local | 2.2 GB | NO | OCR |
| `moondream:latest` | Local | 1.7 GB | NO | Tiny vision |
| Embedding models (3) | Local | various | N/A | Dedup/semantic matching |

**GPU:** RTX 3070 8GB VRAM, 128 GB system RAM, 37% utilization post-optimization.

**Decision: Default model is `huihui_ai/qwen3-abliterated:8b` LOCAL.** No cloud dependency for the default path. The bot must work offline. WORMGPT-12 is available as a user-selectable option but never the default.

---

## GUI Layout

```
+=====================================================================+
|  XBot Controller                                          [Settings] |
+=====================================================================+
|                                                                     |
|  MODEL                                                               |
|  [ huihui_ai/qwen3-abliterated:8b       ▼ ]  Local (5.0 GB)         |
|  Context: [8192]  Temperature: [0.3]  Top-P: [0.9]                  |
|                                                                     |
|  SYSTEM PROFILE                                                      |
|  +---------------------------------------------------------------+   |
|  | You are a content relevance classifier for cybersecurity.     |   |
|  | Score each tweet for relevance and quality.                   |   |
|  | Be strict. Prefer technical depth over drama.                 |   |
|  +---------------------------------------------------------------+   |
|  Preset: [Cyber Curator ▼]  [Save]  [Save As...]  [Delete]          |
|                                                                     |
|  FILTERS & THRESHOLDS                                               |
|  +---------------------------------------------------------------+   |
|  | SEARCH                                                          |  |
|  |   Terms:    [cybersecurity, hacking, 0day, CVE              ]  |  |
|  |   Exclude:  [drama, politics, scam                           ]  |  |
|  |   Accounts: [@KygerR, @malaboratory, @TheRandomNote         ]  |  |
|  |                                                                 |  |
|  | THRESHOLDS (applied by Python, not model)                      |  |
|  |   Min Relevance: [====●====] 60                                |  |
|  |   Min Quality:   [======●==] 70                                |  |
|  |   Max Tweet Age: [24] hours                                    |  |
|  |                                                                 |  |
|  | CONTENT FILTERS                                                 |  |
|  |   Videos Only:    ☑    Verified Only: ☐                         |  |
|  |   Language:       [en ▼]                                        |  |
|  |                                                                 |  |
|  | ACTIONS                                                         |  |
|  |   Auto-Like:      ☑    Auto-Retweet: ☑                          |  |
|  |   Auto-Reply:     ☐    Download Video: ☐                        |  |
|  |   Max Actions/Hour: [10]   Max Actions/Day: [50]                |  |
|  +---------------------------------------------------------------+   |
|                                                                     |
|  SCHEDULE                                                           |
|  Run every: [60 ▼] minutes   ☑ Enabled                             |
|  Active hours: [06:00] to [23:00]                                  |
|  [▶ START]  [⏸ PAUSE]  [⏹ STOP]  [🔍 DRY RUN]                      |
|                                                                     |
+=====================================================================+
|  ACTIVITY LOG                                                       |
|  +---------------------------------------------------------------+   |
|  | 02:15  FETCH   47 tweets from search + 12 from watched accts  |   |
|  | 02:15  DEDUP   31 new (28 already seen)                       |   |
|  | 02:15  SCORE   @kygerR "New CVE-2026-1234 PoC" rel=92 qual=88 |   |
|  | 02:15  LIKE    @kygerR — above threshold (rel≥60, qual≥70)    |   |
|  | 02:16  RETWEET @kygerR — above threshold                      |   |
|  | 02:16  SKIP    @random "drama thread" rel=15 qual=30          |   |
|  | 02:16  SCORE   @malaboratory "RE thread" rel=85 qual=91       |   |
|  | 02:16  LIKE    @malaboratory — above threshold                 |   |
|  | 02:16  ERROR   Ollama timeout on tweet 1234567 — skipped      |   |
|  +---------------------------------------------------------------+   |
|                                                                     |
|  Today: 18 liked | 7 retweeted | 14 skipped | 1 error              |
+=====================================================================+
```

### GUI Components

**1. Model Selector**
- Dropdown populated from `ollama list`
- Shows local (size) vs cloud (~)
- Default: `huihui_ai/qwen3-abliterated:8b`
- Context window, temperature, top_p controls
- Low temperature (0.2-0.4) recommended for consistent scoring

**2. System Profile Box**
- Large text area for the classifier system prompt
- Preset dropdown with saveable profiles
- Shipped presets: Cyber Curator, Strict Researcher, Engagement Maximizer, Custom
- Profiles stored as versioned JSON

**3. Filters & Thresholds**
- Search terms, exclude terms, watched accounts
- **Relevance threshold slider** (0-100) — Python applies this
- **Quality threshold slider** (0-100) — Python applies this
- Content filters (videos only, language, verified, max age)
- Action toggles (like, retweet, reply, download)
- Rate limiting (max per hour, max per day)

**4. Schedule**
- Interval, active hours, enable toggle
- Start/Pause/Stop/Dry Run buttons
- Dry Run = score everything, execute nothing, show what WOULD happen

**5. Activity Log**
- Real-time, color-coded
- Shows FETCH → DEDUP → SCORE → ACTION/SKIP/ERROR pipeline
- Stats counter

---

## Execution Flow (with error branches)

```
SCHEDULER TRIGGERS
│
├──► HEALTH CHECK
│    ├── Ollama reachable?       ──NO──► LOG error, retry in 5 min, EXIT
│    ├── XActions auth valid?    ──NO──► LOG "re-login needed", EXIT
│    └── Rate limit headroom?    ──NO──► LOG "rate limited", EXIT
│
├──► FETCH TWEETS (XActions CLI)
│    ├── xactions search "terms" --limit 50
│    ├── xactions tweets @account1 --limit 10  (per watched account)
│    ├── xactions search "terms" --filter videos
│    │
│    ├── SUCCESS ──► merge results
│    └── FAIL    ──► LOG error, continue with partial results
│
├──► DEDUP (SQLite seen_tweets table)
│    ├── SELECT tweet_id FROM seen_tweets WHERE tweet_id IN (...)
│    ├── Filter out already-seen tweet_ids
│    ├── Log: "47 fetched, 31 new, 16 already seen"
│    └── Remaining = candidates for scoring
│
├──► SCORE (Ollama) — per candidate tweet
│    │
│    │  Send to model:
│    │    SYSTEM: {classifier prompt}
│    │    USER: Tweet from @{user} ({likes} likes, {retweets} RTs):
│    │          "{tweet_text}"
│    │          Media: {media_type}
│    │          Age: {hours}h ago
│    │          Score relevance 0-100, quality 0-100, topic, reason.
│    │
│    ├── PARSE JSON ──FAIL──► LOG parse error, use fallback scores (0,0), SKIP
│    ├── TIMEOUT  ──► LOG timeout, SKIP, continue to next tweet
│    └── SUCCESS  ──► store score in memory
│
├──► APPLY THRESHOLDS (Python — deterministic)
│    │
│    │  for tweet, score in candidates:
│    │    if score.relevance >= config.min_relevance
│    │    and score.quality >= config.min_quality
│    │    and not contains_excluded_terms(tweet)
│    │    and within_rate_limit():
│    │        → ENGAGE
│    │    else:
│    │        → SKIP (log reason)
│
├──► EXECUTE ACTIONS (XActions CLI)
│    ├── xactions like <tweet_id>
│    │     ├── SUCCESS ──► LOG "liked @user"
│    │     └── FAIL    ──► LOG error, continue
│    ├── xactions retweet <tweet_id>
│    │     ├── SUCCESS ──► LOG "retweeted @user"
│    │     └── FAIL    ──► LOG error, continue
│    └── Random delay 5-30s between actions (anti-ban)
│
├──► RECORD TO DATABASE
│    ├── INSERT INTO seen_tweets (tweet_id, scored_at, relevance, quality, 
│    │   topic, action_taken, model_used)
│    ├── INSERT INTO action_log (timestamp, action, tweet_id, username, 
│    │   status, error_message)
│    └── UPDATE rate_limit_counter
│
└──► DONE — sleep until next interval
```

---

## Dedup / Idempotency

**SQLite schema:**

```sql
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
    action_taken  TEXT,  -- 'liked', 'retweeted', 'liked+retweeted', 'skipped', 'error'
    model_used    TEXT
);

CREATE TABLE IF NOT EXISTS action_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    action        TEXT NOT NULL,  -- 'like', 'retweet', 'reply', 'download', 'skip', 'error'
    tweet_id      TEXT,
    username      TEXT,
    status        TEXT NOT NULL,  -- 'success', 'fail', 'skip'
    error_message TEXT,
    latency_ms    INTEGER
);

CREATE TABLE IF NOT EXISTS rate_limits (
    date          TEXT NOT NULL,  -- '2026-07-12'
    hour          INTEGER,        -- 0-23
    action_count  INTEGER DEFAULT 0,
    PRIMARY KEY (date, hour)
);
```

Every tweet fetched gets its tweet_id checked against `seen_tweets`. If it exists, skip entirely — no model call wasted. This means the bot only spends inference on NEW content.

---

## Cost / Latency Budget

**Per-run estimate (qwen3-abliterated:8b local):**

```
SEARCH PHASE:
  xactions search (3 queries × limit 50)     ~15s (CLI/Puppeteer)
  xactions tweets (5 accounts × limit 10)    ~25s
  Total fetch:                                ~40s

DEDUP PHASE:
  SQLite lookup on ~200 tweet_ids             <100ms

SCORING PHASE:
  Average new tweets per run:                 ~30 (after dedup)
  Tokens per tweet (input):                   ~150 (tweet + system prompt)
  Tokens per tweet (output):                  ~80 (JSON scores)
  Total tokens per tweet:                     ~230
  Total tokens per run:                       ~6,900 (30 × 230)
  
  RTX 3070 + qwen3:8b Q4 throughput:         ~40 tok/s
  Latency per tweet:                          ~6s
  Total scoring time:                         ~180s (3 min)

ACTION PHASE:
  Average actions per run:                    ~8 (after thresholds)
  XActions CLI per action:                    ~3s
  Random delays (8 × 15s avg):                ~120s
  Total action time:                          ~145s

TOTAL RUN TIME:                               ~6-7 minutes
MODEL VRAM:                                   ~5.5 GB (model + KV cache at 8K context)
TOKENS PER RUN:                               ~6,900
COST:                                         $0 (local model)
```

**For comparison, WORMGPT-12 cloud:**
- Same token count, but latency depends on cloud response (1-5s per call)
- Total scoring time: ~60-150s (faster but external dependency)
- Cost: free (Ollama cloud) but availability not guaranteed

**Recommendation:** Use local qwen3-abliterated:8b. 6-7 min per hourly run is well within budget. The bot spends 53 of 60 minutes idle.

---

## Config Schema (Versioned)

```json
{
  "schema_version": 1,
  "model": {
    "name": "huihui_ai/qwen3-abliterated:8b",
    "context_window": 8192,
    "temperature": 0.3,
    "top_p": 0.9,
    "repeat_penalty": 1.1
  },
  "system_prompt": "You are a content relevance classifier...",
  "filters": {
    "search_terms": ["cybersecurity", "hacking", "0day", "CVE"],
    "exclude_terms": ["drama", "politics", "scam"],
    "watched_accounts": ["@KygerR", "@malaboratory"],
    "videos_only": true,
    "language": "en",
    "max_age_hours": 24,
    "min_likes": 0,
    "min_retweets": 0
  },
  "thresholds": {
    "min_relevance": 60,
    "min_quality": 70
  },
  "actions": {
    "like": true,
    "retweet": true,
    "reply": false,
    "download_video": false
  },
  "rate_limit": {
    "max_per_hour": 10,
    "max_per_day": 50
  },
  "schedule": {
    "interval_minutes": 60,
    "enabled": true,
    "active_hours_start": "06:00",
    "active_hours_end": "23:00"
  },
  "anti_ban": {
    "min_delay_seconds": 5,
    "max_delay_seconds": 30,
    "jitter": true
  }
}
```

On load, the app checks `schema_version`. If it differs from current, run a migration function. This prevents config breakage when the schema evolves.

---

## Failure Handling Matrix

| Failure | Detection | Response |
|---------|-----------|----------|
| Ollama down | Health check before run | Log error, retry in 5 min, skip run |
| Ollama timeout on single tweet | 30s per-call timeout | Log timeout, skip that tweet, continue |
| Model returns garbage (not JSON) | JSON parse fails | Log parse error, skip tweet, continue |
| XActions auth expired | CLI returns auth error | Log "re-login needed", pause bot, notify user |
| XActions action fails (404, rate limited) | CLI exit code / error text | Log error, continue with next action |
| Tweet 404 (deleted between fetch and action) | CLI error | Log, mark as error in seen_tweets, continue |
| SQLite locked | DB error | Retry 3× with 100ms backoff, then log and exit |
| Rate limit reached | Pre-action check | Log "rate limited", exit run gracefully |
| Network down | Fetch fails | Log, retry next interval |
| Model VRAM OOM | Ollama error | Log, fall back to smaller model if configured |

**Principle:** Never crash the bot. Every error is caught, logged, and the bot either continues (skip the failed item) or exits the current run cleanly (waits for next interval).

---

## File Structure

```
G:\DexProjects\XBot\
├── main.py              # PySide6 GUI entry point
├── bot_engine.py        # Core: scheduler, pipeline orchestration, error handling
├── classifier.py        # Ollama scoring: send tweet, parse JSON scores
├── xactions_cli.py      # XActions CLI wrapper: search, like, retweet (subprocess)
├── database.py          # SQLite: seen_tweets, action_log, rate_limits
├── config.py            # Config load/save/migrate (versioned schema)
├── profiles.py          # Profile presets save/load
├── requirements.txt
├── profiles/
│   ├── cyber_curator.json
│   └── custom/
├── data/
│   └── xbot.db          # SQLite
└── logs/
    └── xbot.log         # Rotating file log
```

---

## XActions Integration: CLI Subprocess

Direct CLI calls via `subprocess.run()`. Each call logged with latency and exit code.

```python
class XActionsCLI:
    def search(self, query: str, limit: int = 50) -> list[dict]:
        result = subprocess.run(
            ["xactions", "search", query, "--limit", str(limit), "--json"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            raise XActionsError(result.stderr)
        return json.loads(result.stdout)

    def like(self, tweet_id: str) -> bool:
        result = subprocess.run(
            ["xactions", "like", tweet_id],
            capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0

    def retweet(self, tweet_id: str) -> bool:
        result = subprocess.run(
            ["xactions", "retweet", tweet_id],
            capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0
```

No MCP server, no daemon, no extra moving parts. Each action is a clean process invocation.

---

## Risks & Mitigations

1. **Twitter Ban Risk** — Rate limited to 10/hr, 50/day. Random delays 5-30s between actions. Active hours only (6am-11pm). Bot should look human, not scripted.

2. **Cookie Expiry** — XActions auth_token expires every ~30 days. Bot detects auth failures (CLI error pattern) and surfaces a "Re-login required" notification in the GUI. No silent failures.

3. **Model Inconsistency** — Low temperature (0.3) for stable scoring. If scores seem off, user can swap models from the dropdown without code changes. The classifier output schema is the same regardless of model.

4. **VRAM Pressure** — Model uses ~5.5 GB. If user is gaming (Marvel Rivals etc.), VRAM may be tight. Bot should check `nvidia-smi` free VRAM before loading model and warn/fallback if insufficient.

---

## Answers to Open Questions (from planning review)

- **MCP vs CLI:** CLI. Fewer moving parts, each call is logged, no daemon to manage.
- **Default model:** `huihui_ai/qwen3-abliterated:8b` LOCAL. No cloud dependency for default path.
- **Auto-reply:** Out of scope for v1. Like + retweet only.
- **Original content:** Different product. Not included.
- **Multiple accounts:** Single account.
- **Charts/dashboard:** Skip until there's data worth charting. Text log + stats counter is enough for v1.
