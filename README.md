# Cricket Hot Match Detector

A real-time engine that watches a T20 cricket chase ball by ball and fires a notification when a match is about to become exciting — before the drama peaks.

---

## What it does

Two signals are generated per match:

| Signal | When | Logic |
|---|---|---|
| **Pre-match** | Ball 1 of the chase | Win probability at start is between 40–60% — structurally competitive target |
| **In-game** | After over 10 only | Hotness forecaster predicts peak drama in the next 6 balls |

The system is tuned for **recall over precision** — missing an exciting match is worse than a false alert.

Signals are delivered via a **Telegram bot** that also answers natural-language questions about the match and serves live charts on demand.

---

## How it works

```
Live Cricbuzz feed  →  Polling service  →  Engine API  →  Orchestrator  →  UI
                                                                 ↓
                                                            Telegram Bot
```

**Engine pipeline (per legal delivery):**
```
BallEvent arrives
  → ChaseState updated
  → 6 features extracted
  → Win probability (NN) computed
  → Hotness score computed  (closeness + momentum)
  → Hotness forecaster (NN) run if ball >= 60
  → Signals evaluated
  → EngineOutput emitted
```

Three trained artifacts power the pipeline:

| Model | Purpose | Params |
|---|---|---|
| `models/win_prob_nn.pt` | Win probability per ball | 3,073 |
| `models/hotness_forecaster.pt` | Predict hotness in next 6 balls | 3,009 |
| `models/emp_lookup.pkl` | Empirical fallback lookup | — |

Both NNs were trained on **1,159 IPL matches (2008–2026)** from cricsheet.org.

---

## Project structure

```
cricket_hot_match_detector/
├── engine/                   # Core detection engine + HTTP layer
│   ├── models.py             # Data structures: BallEvent, ChaseState, EngineOutput, …
│   ├── state.py              # Per-ball state update
│   ├── features.py           # Feature extraction for the win-prob NN
│   ├── win_prob.py           # Win probability NN inference
│   ├── hotness.py            # Hotness score formula
│   ├── forecaster.py         # Hotness forecaster NN inference
│   ├── signals.py            # Signal evaluation (pre-match + in-game)
│   ├── orchestrator.py       # Pipeline coordinator, in-memory sessions
│   ├── routes.py             # FastAPI route handlers
│   ├── server.py             # App entry point, lifespan model loading
│   └── README.md             # Pipeline internals, data structures, model details
│
├── polling/                  # Live data polling service
│   ├── adapter.py            # Cricbuzz items → BallEvent dicts + scorecard extraction
│   ├── cricbuzz_client.py    # Cricbuzz API client (retry + backoff)
│   ├── engine_client.py      # HTTP client for engine API
│   ├── poller.py             # LivePoller: inn1 + inn2 live polling loops
│   ├── schedule.py           # IPL 2026 schedule reader — smart wait helpers
│   └── run_live.py           # CLI entry for Docker / standalone polling
│
├── orchestrator/             # Coordination layer — single API surface for UI + bot
│   └── main.py               # Aggregates match history, proxies engine health
│
├── bot/                      # Telegram chatbot + LangGraph ReAct agent
│   ├── main.py               # PTB Application, command handlers, entry point
│   ├── agent.py              # LangGraph ReAct agent with per-chat session memory
│   ├── tools.py              # 14 LangChain tools calling orchestrator API
│   ├── charts.py             # Matplotlib chart generation → PNG bytes
│   ├── alert_loop.py         # Background asyncio loop — proactive + lifecycle alerts
│   ├── llm.py                # LLM factory (Gemini Flash, configurable via env)
│   └── state.py              # Persistent state (subscribed chats, seen fingerprints)
│
├── ui/                       # Streamlit live match dashboard
│   └── app.py                # Auto-refreshing win prob + hotness charts
│
├── tests/
│   └── simulate_hot_match.py # Full match replay simulation (KKR vs LSG 2026)
│
├── models/                   # Saved model checkpoints (binary, not re-trained here)
├── notebooks/                # Exploratory analysis notebooks (NB01–NB07)
├── data/
│   ├── raw/                  # Cricsheet match JSONs used for training/validation
│   ├── ipl_2026_schedule.json  # Full IPL 2026 fixture list (M1–M70)
│   └── live_polls/           # Per-match live data (JSONL ball events + engine outputs)
├── skills/                   # Session context docs (model design, analysis evolution)
│
├── .env.example              # Template for TELEGRAM_TOKEN, GOOGLE_API_KEY, etc.
├── Dockerfile                # Single image for all services
├── docker-compose.yml        # engine + poller + orchestrator + ui + bot
├── requirements.txt
├── run.py                    # Unified local launcher (engine + poller, no Docker)
└── working_context.md        # Running log of decisions and state — read before resuming
```

---

## Quickstart — Docker (recommended)

### First-time setup

**1. Create the live polls directory** (required before first run — Docker bind mount will fail without it):

```bash
mkdir data/live_polls
```

> **Windows note:** If your project path contains spaces (e.g. `Personal Projects/`), Docker Desktop may
> fail to create the directory automatically. Always create `data/live_polls` manually before running.

**2. Create a `.env` file** (copy from `.env.example`):

```
# Required for the Telegram bot
TELEGRAM_TOKEN=your_telegram_bot_token_here
GOOGLE_API_KEY=your_google_api_key_here

# Optional — pin a specific match (auto-discovered if omitted)
# TEAM1=CSK
# TEAM2=KKR
# CB_ID=151763
```

Get a `TELEGRAM_TOKEN` from [@BotFather](https://t.me/BotFather).
Get a `GOOGLE_API_KEY` from [Google AI Studio](https://aistudio.google.com/) (free tier works).

If `TEAM1`/`TEAM2` are omitted, the poller auto-discovers any live IPL match by scraping
`cricbuzz.com/cricket-match/live-scores`. `CB_ID` is optional — find it in the match URL
(`cricbuzz.com/live-cricket-scores/151763/...` → `151763`) to skip discovery entirely.

**3. Start all services:**

```bash
docker compose up --build -d
```

`-d` runs containers in the background (detached). To stream logs after:

```bash
docker compose logs -f           # all services
docker compose logs -f poller    # one service
```

| Service | URL / access |
|---|---|
| Engine API + Swagger | http://localhost:8000/docs |
| Orchestrator API | http://localhost:8080/docs |
| Live dashboard (Streamlit) | http://localhost:8501 |
| Telegram bot | Message your bot on Telegram; send `/start` to subscribe to alerts |

### Starting a new match

No rebuild needed. Restart the engine and poller — the poller will auto-discover the new match:

```bash
docker compose restart engine
docker compose up -d --no-build poller
```

To pin a specific fixture, update `.env` with `TEAM1`, `TEAM2` (and optionally `CB_ID`) before restarting.

The engine must be restarted to clear its in-memory match session from the previous match.
The orchestrator and UI keep running untouched.

### Replaying a match from scratch

If the poller was restarted mid-match and you want to re-process all balls:

```bash
rm data/live_polls/<match_id>/ball_events.jsonl
docker compose restart engine
docker compose up -d --no-build poller
```

The poller uses `ball_events.jsonl` as its seen-set. Deleting it forces a full replay from Cricbuzz.
The engine must also be restarted — otherwise it still holds the previous session in memory and
will return duplicates for every ball.

### Stopping

```bash
docker compose down
```

Data is persisted to `data/live_polls/{match_id}/` on the host via bind mount.

---

## Quickstart — local (no Docker)

```bash
conda activate cricket_hot
pip install -r requirements.txt

# No args — auto-discovers any live IPL match
python run.py

# Pin a specific match
python run.py --team1 CSK --team2 KKR

# Override Cricbuzz ID (skip discovery)
python run.py --team1 CSK --team2 KKR --cb-id 151763
```

Arguments:

| Flag | Default | Description |
|---|---|---|
| `--team1 / --team2` | auto-discovered | Team abbreviations (e.g. CSK, KKR). Optional — auto-detected from Cricbuzz if omitted. |
| `--cb-id` | auto-discovered | Cricbuzz numeric match ID. Optional — found from match URL if omitted. |
| `--match-id` | auto-generated | Override the data folder slug |
| `--poll-interval N` | 30 | Seconds between Cricbuzz polls **during inn2** (Phase 2 uses 5 min) |
| `--port N` | 8000 | Engine API port |
| `--log-level` | WARNING | DEBUG / INFO / WARNING / ERROR |

---

## Running the simulation

Replay a historical match ball by ball against the engine to verify the pipeline end-to-end:

```bash
conda activate cricket_hot
python -m tests.simulate_hot_match
```

This replays KKR vs LSG (2026-04-09) and prints:
- Signal detection with ball number and over
- Overlap / idempotency test (3 balls re-sent)
- Latency breakdown identifying the pipeline bottleneck

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│ Docker Compose                                                       │
│                                                                      │
│   ┌──────────────┐   legal balls   ┌────────────────────────┐       │
│   │   poller     │ ─────────────→  │   engine  :8000        │       │
│   │              │                 │   FastAPI + NN models  │       │
│   │  Cricbuzz    │                 └────────────┬───────────┘       │
│   │  HTML scrape │                              │                    │
│   │  + JSON API  │                       EngineOutputs               │
│   └──────────────┘                              ↓                    │
│                                    ┌────────────────────────┐       │
│                         JSONL      │  orchestrator  :8080   │       │
│                       ─────────→   │  reads live_polls/     │       │
│                      (shared vol)  └──────────┬─────────────┘       │
│                                               │                      │
│                                      HTTP API │                      │
│                              ┌────────────────┴──────────────┐      │
│                              ↓                                ↓      │
│                ┌─────────────────────┐     ┌───────────────────┐    │
│                │   ui  :8501         │     │   bot             │    │
│                │   Streamlit dash    │     │   Telegram alerts │    │
│                └─────────────────────┘     │   + LLM agent     │    │
│                                            └───────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
```

The poller and orchestrator share `data/live_polls/` via a Docker bind mount. The UI talks exclusively to the orchestrator (no direct file access, no direct engine calls).

---

## Telegram bot

The bot is a personal notifier and match analyst. It does not require any setup beyond `/start`.

### Commands

| Command | Description |
|---|---|
| `/start` | Subscribe to match alerts |
| `/stop` | Unsubscribe |
| `/status` | Live scoreboard: both innings scores, win%, hotness, forecast |
| `/chart winprob` | Win probability chart |
| `/chart hotness` | Hotness chart |
| `/chart forecast` | Hotness + forecast overlay |
| `/signals` | All signals fired this match |
| `/turning` | Top 5 win probability turning points |
| `/balls [n]` | Last N balls table (default 20) |
| `/matches` | List all recorded matches |
| Free text | Ask anything — answered by Gemini Flash via LangGraph ReAct agent with session memory |

### Alerts

Six lifecycle and hotness alerts per match (once each, never duplicated after restart):

| Alert | Trigger |
|---|---|
| 🏏 **INN1_STARTED** | First ball of innings 1 recorded |
| 📊 **INN1_ENDED** | Innings 1 complete, innings 2 about to start |
| 🎯 **INN2_STARTED** | First ball of the chase recorded |
| 📢 **PRE_MATCH** | Ball 1 win probability is 40–60% — structurally even chase |
| 🔥 **IN_GAME** | Hotness forecaster crosses threshold after over 10 |
| 🏆 **MATCH_ENDED** | Match over (target reached / all out / balls exhausted) |

Lifecycle alerts (INN1_STARTED, INN1_ENDED, INN2_STARTED, MATCH_ENDED) are written by Gemini Flash for natural-language summaries, with template fallbacks if the LLM is unavailable.

Alert state is persisted to `data/bot_state.json` — restarts do not re-send old alerts.

### Agent tools

The free-text agent has 14 tools:

| Tool | Purpose |
|---|---|
| `get_match_status` | Live scoreboard for both innings |
| `get_win_prob_curve` | Win probability chart |
| `get_hotness_curve` | Hotness chart |
| `get_forecast_overlay` | Hotness + forecast overlay chart |
| `get_signal_timeline` | Signals fired this match |
| `get_key_turning_points` | Top N win prob swing moments |
| `get_ball_by_ball_table` | Last N balls table |
| `get_match_scorecard` | Over-by-over scorecard (inn1 or inn2) |
| `get_batting_summary` | Aggregates: sixes, fours, run rate, extras |
| `get_batting_card` | Per-batter: runs, balls, 4s, 6s, SR |
| `get_bowling_card` | Per-bowler: overs, runs, wkts, economy |
| `run_python` | Execute Python against match data for custom analysis |
| `list_matches` | All recorded matches |
| `get_schedule` | Upcoming IPL 2026 fixtures (optionally filtered by team) |

---

## Engine API quick reference

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/match/init` | Initialise a match with innings 1 summary |
| `POST` | `/match/{id}/ball` | Process one legal delivery |
| `GET` | `/match/{id}/state` | Current chase state + last output |
| `GET` | `/debug/latency` | Per-step mean latency breakdown |

Full docs at `http://localhost:8000/docs` when the server is running.

See [engine/README.md](engine/README.md) for request/response examples and data structures.

---

## Orchestrator API quick reference

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness check + engine reachability |
| `GET` | `/schedule` | Upcoming IPL 2026 fixtures (`?team=CSK` to filter) |
| `GET` | `/matches` | List all match IDs with metadata |
| `GET` | `/matches/current` | Most recently active match + latest engine state |
| `GET` | `/matches/{id}/history` | Full ball-by-ball engine outputs (inn2) |
| `GET` | `/matches/{id}/signals` | Signals fired during a match |
| `GET` | `/matches/{id}/ball_events` | Raw ball-by-ball events for inn2 |
| `GET` | `/matches/{id}/ball_events_inn1` | Raw ball-by-ball events for inn1 |
| `GET` | `/matches/{id}/scorecard/{1\|2}` | Batting + bowling scorecard for an innings |
| `GET` | `/bot/status` | Bot subscriber count and alert fingerprints |

Full docs at `http://localhost:8080/docs` when running.

---

## Key design decisions

**BallEvent = legal deliveries only.**
Wides and no-balls are not sent. Only legal balls are POSTed. extras = byes + legByes only.

**Idempotent ball processing.**
Re-sending the same delivery (same `over.delivery` float) returns `is_duplicate: true` without mutating state. Safe to retry on network failures. The poller maintains a seen-set and resumes cleanly after restarts.

**60-ball gate on in-game signals.**
No in-game notifications before over 10. Suppresses false positives from structurally close targets (e.g. target 182 ≈ 50% win prob from ball 1).

**`balls_fraction` hardcoded to `/120`.**
Both NNs were trained with this convention. Using `/total_balls` would mis-calibrate the models even though it looks more correct mathematically.

**Full-match polling.**
The poller records both innings. Phase 1 live-polls inn1 ball-by-ball (5s interval), saving to `ball_events_inn1.jsonl` and `scorecard_inn1.json`. Phase 2 detects inn2 start via the Cricbuzz match state and switches to chase polling. This enables the bot to answer inn1 questions ("who scored?", "batting card?") and fire accurate lifecycle alerts.

**Schedule-aware smart wait.**
On startup, the poller reads `data/ipl_2026_schedule.json` to find the next IPL match and sleeps until 15 minutes before the scheduled start time. Sleeping is done in 5-minute chunks so PC wake-from-sleep doesn't miss the window. If no schedule data is available, falls back to 60s retry.

---

## Validation results (from NB07)

| Match | Label | In-game signal | Lead time |
|---|---|---|---|
| DC vs GT, IPL 2026-04-08 | HOT | ~ball 60 | Full second half |
| IND vs PAK, T20 WC 2024-06-09 | HOT | ball 79 | 7 balls ahead of Bumrah spell |
| KKR vs LSG, IPL 2026-04-09 | HOT | ~ball 90 | 24 balls before last-over drama |
| RCB vs RR, IPL 2026-04-10 | COLD blowout | Never fires | Correct |
| RR vs MI, IPL 2026-04-07 | COLD (rain) | Never fires | Correct |
| MI vs RR, IPL 2025-05-01 | COLD | Never fires | Correct |

---

## Known limitations

- Model trained on IPL data only — may mis-calibrate for other leagues
- Engine sessions are in-memory; restarting the engine loses active match state (ball history in JSONL is not replayed — backlog B1)
- Forecast threshold (0.60) is exploratory, not formally calibrated
- No authentication on the APIs — do not expose publicly without adding auth
- DLS (rain-reduced) matches are slightly mis-calibrated (see `balls_fraction` note above)
- Bot is single-user by design — no multi-user auth; anyone with the bot link can subscribe
- Agent session memory (MemorySaver) is in-process only — lost on container restart

---

## Further reading

- [engine/README.md](engine/README.md) — pipeline internals, data structures, model details
- [bot/README.md](bot/README.md) — Telegram bot user guide and setup
- [tests/README.md](tests/README.md) — simulation guide and how to add new matches
- [working_context.md](working_context.md) — running log of sprint decisions
- [skills/model/analysis_evolution.md](skills/model/analysis_evolution.md) — how the model evolved across NB01–NB07
- [skills/model/model_design_3.md](skills/model/model_design_3.md) — model design reference (ML / NN details)
- [skills/data/fetch_ball_by_ball.md](skills/data/fetch_ball_by_ball.md) — post-match historical data retrieval from Cricsheet
- [skills/live/cricbuzz_api_endpoints.md](skills/live/cricbuzz_api_endpoints.md) — Cricbuzz API endpoints + rediscovery guide
- [skills/project/backlog.md](skills/project/backlog.md) — prioritised fix and improvement list
