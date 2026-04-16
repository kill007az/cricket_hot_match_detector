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

---

## How it works

```
Live Cricbuzz feed  →  Polling service  →  Engine API  →  Orchestrator  →  UI
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
│   ├── adapter.py            # Cricbuzz items → BallEvent dicts
│   ├── cricbuzz_client.py    # Cricbuzz API client (retry + backoff)
│   ├── engine_client.py      # HTTP client for engine API
│   ├── poller.py             # LivePoller: 3-phase polling loop
│   └── run_live.py           # CLI entry for Docker / standalone polling
│
├── orchestrator/             # Coordination layer — single API surface for UI
│   └── main.py               # Aggregates match history, proxies engine health
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
│   └── live_polls/           # Per-match live data (JSONL ball events + engine outputs)
├── skills/                   # Session context docs (model design, analysis evolution)
│
├── Dockerfile                # Single image for all services
├── docker-compose.yml        # engine + poller + orchestrator + ui
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

**2. Set match details in `.env`** (create this file in the project root):

```
TEAM1=CSK
TEAM2=KKR
CB_ID=151763
```

Find the numeric Cricbuzz match ID in the match URL:
`cricbuzz.com/live-cricket-scores/151763/...` → ID is `151763`.
Auto-discovery is currently unavailable; see [skills/live/cricbuzz_api_endpoints.md](skills/live/cricbuzz_api_endpoints.md).

**3. Start all services:**

```bash
docker compose up --build -d
```

`-d` runs containers in the background (detached). To stream logs after:

```bash
docker compose logs -f           # all services
docker compose logs -f poller    # one service
```

| Service | URL |
|---|---|
| Engine API + Swagger | http://localhost:8000/docs |
| Orchestrator API | http://localhost:8080/docs |
| Live dashboard (Streamlit) | http://localhost:8501 |

### Starting a new match

No rebuild needed. Update `.env` with the new match details, then restart the engine and poller:

```bash
# edit .env: update TEAM1, TEAM2, CB_ID
docker compose restart engine
docker compose up -d --no-build poller
```

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

# --team1, --team2, and --cb-id are all required.
# --cb-id is the numeric ID from the Cricbuzz match URL.
python run.py --team1 CSK --team2 KKR --cb-id 151763
```

Arguments:

| Flag | Default | Description |
|---|---|---|
| `--team1 / --team2` | — | Team abbreviations (e.g. CSK, KKR). **Required.** |
| `--cb-id` | — | Cricbuzz numeric match ID from the match URL. **Required.** |
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
┌─────────────────────────────────────────────────────────────────┐
│ Docker Compose                                                  │
│                                                                 │
│   ┌──────────────┐   legal balls   ┌────────────────────────┐  │
│   │   poller     │ ─────────────→  │   engine  :8000        │  │
│   │              │                 │   FastAPI + NN models  │  │
│   │  Cricbuzz    │                 └────────────┬───────────┘  │
│   │  unofficial  │                              │               │
│   │  JSON API    │                       EngineOutputs          │
│   └──────────────┘                              ↓               │
│                                    ┌────────────────────────┐  │
│                         JSONL      │  orchestrator  :8080   │  │
│                       ─────────→   │  reads live_polls/     │  │
│                      (shared vol)  └────────────┬───────────┘  │
│                                                 │               │
│                                        HTTP API │               │
│                                                 ↓               │
│                                    ┌────────────────────────┐  │
│                                    │   ui  :8501            │  │
│                                    │   Streamlit dashboard  │  │
│                                    └────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

The poller and orchestrator share `data/live_polls/` via a Docker bind mount. The UI talks exclusively to the orchestrator (no direct file access, no direct engine calls).

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
| `GET` | `/health` | Liveness check |
| `GET` | `/matches` | List all match IDs with data |
| `GET` | `/matches/current` | Most recently active match summary |
| `GET` | `/matches/{id}/history` | Full ball-by-ball engine outputs for a match |
| `GET` | `/matches/{id}/signals` | Signals fired during a match |

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

**Inn1 smart wait (iterative).**
Phase 2 iteratively fetches inn1, sleeps `remaining_balls × 35s`, and repeats until inn1 is complete — self-correcting any undershoot. Then polls inn2 every 5 min until it starts. Legal balls are counted from actual deliveries (not `overs × 6`) to handle rain-reduced matches.

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
- Engine sessions are in-memory; restarting the engine loses active match state (ball history in JSONL is not replayed)
- Forecast threshold (0.60) is exploratory, not formally calibrated
- No authentication on the APIs — do not expose publicly without adding auth
- DLS (rain-reduced) matches are slightly mis-calibrated (see `balls_fraction` note above)

---

## Further reading

- [engine/README.md](engine/README.md) — pipeline internals, data structures, model details
- [tests/README.md](tests/README.md) — simulation guide and how to add new matches
- [working_context.md](working_context.md) — running log of sprint decisions
- [skills/model/analysis_evolution.md](skills/model/analysis_evolution.md) — how the model evolved across NB01–NB07
- [skills/model/model_design_3.md](skills/model/model_design_3.md) — model design reference (ML / NN details)
- [skills/data/fetch_ball_by_ball.md](skills/data/fetch_ball_by_ball.md) — post-match historical data retrieval from Cricsheet
- [skills/live/cricbuzz_api_endpoints.md](skills/live/cricbuzz_api_endpoints.md) — Cricbuzz API endpoints + rediscovery guide
- [skills/project/backlog.md](skills/project/backlog.md) — prioritised fix and improvement list
