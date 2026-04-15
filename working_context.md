# Working Context — Cricket Hot Match Engine

Append a new section here after every major/minor step. Keeps future sessions
oriented without re-reading all source files.

---

## Sprint 1 — Initial build (2026-04-14)

### What was built

Full engine repo scaffolded from the sprint-1 feature spec.

**Directory layout**
```
engine/
    __init__.py
    models.py        — BallEvent, ChaseState, HotnessState, MatchSession, EngineOutput
    state.py         — StateUpdater (immutable per-ball state update)
    features.py      — FeatureExtractor (6 features for win-prob NN)
    win_prob.py      — WinProbModel (loads win_prob_nn.pt)
    hotness.py       — HotnessCalculator (closeness + momentum formula)
    forecaster.py    — HotnessForecaster (loads hotness_forecaster.pt)
    signals.py       — SignalEvaluator (pre-match + in-game signals)
    orchestrator.py  — EngineOrchestrator (full pipeline, in-memory sessions)

api/
    __init__.py
    main.py          — FastAPI app, lifespan model loading
    routes.py        — POST /match/init, POST /match/{id}/ball, GET /match/{id}/state

tests/
    __init__.py
    simulate_hot_match.py  — Replay KKR vs LSG ball-by-ball via HTTP

.gitignore
requirements.txt
working_context.md  (this file)
```

### Key design decisions

1. **BallEvent = legal delivery only.**
   Wides and no-balls are not sent to the engine. Extras on a BallEvent are
   byes/leg-byes only. The simulation script filters illegal deliveries before
   sending.

2. **Idempotency via ball_key.**
   `ball_key = f"{match_id}:{innings}:{over:.1f}"`. Duplicate POSTs return
   `is_duplicate=True` with the previous output unchanged.

3. **`balls_fraction` hardcoded to `/ 120.0`.**
   Both the win-prob NN and the forecaster were trained with this convention.
   The spec says `/ total_balls` — that's a known discrepancy; training wins.

4. **Overlap in data ingestion.**
   The simulation script deliberately re-sends 3 random balls to demonstrate
   idempotency. The engine absorbs them without mutating state.

5. **Models loaded once at startup.**
   `EngineOrchestrator.__init__` loads both `.pt` files. FastAPI lifespan
   stores the orchestrator on `app.state.engine` — shared across requests.

6. **Forecast gate: ball >= 60 AND >= 12-ball history.**
   Both conditions must hold. Early in the match neither condition is met.

### Model checkpoint keys (for reference)

| Checkpoint | Relevant keys |
|---|---|
| `win_prob_nn.pt` | `model_state_dict`, `input_dim`, `hidden_dims`, `X_mean`, `X_std`, `feature_cols` |
| `hotness_forecaster.pt` | `model_state_dict`, `input_dim`, `hidden_dims`, `lookback`, `horizon`, `X_train_mean`, `X_train_std` |

### How to run

```bash
# Start API
conda run -n cricket_hot uvicorn api.main:app --reload --port 8000

# In a second terminal — run the simulation
conda run -n cricket_hot python -m tests.simulate_hot_match
```

### Known gaps / next steps

- **Threshold calibration** — `FORECAST_THRESHOLD = 0.60` in `signals.py` (raised from 0.55 after first live run).
  Not formally calibrated. Run on more matches.
- **Signal deduplication** — in-game signal can fire on multiple consecutive
  balls once threshold is crossed. Decide: fire-once-per-match vs fire-once-
  per-crossing.
- **`balls_fraction` / total_balls for DLS** — currently both models hard-code
  `/120`; DLS matches with fewer overs are slightly mis-calibrated. Acceptable
  for now.
- **First innings signals** — not in scope for sprint 1.
- **Persistent session store** — sessions are in-memory; restart loses state.
  Sprint 2 could add Redis or SQLite.
- **Authentication** — no auth on the API; add before exposing publicly.

---

## Sprint 2 — Live polling service + first live run (2026-04-14/16)

### What was built

**Polling service** (`polling/`):
- `CricbuzzClient` — Cricbuzz unofficial API client with bot-avoidance headers; new endpoint `/api/mcenter/{id}/full-commentary/{inn}` (old `/api/cricket-match/` → 404)
- `adapter.py` — rewritten for new API field names (`legalRuns`, `totalRuns`, `overNumber`); wide/no-ball dedup by overNumber+timestamp
- `LivePoller` — 3-phase loop; `--cb-id` required (auto-discovery endpoint gone); resume via `ball_events.jsonl`
- `run.py` — unified local launcher (engine + poller)
- Docker: `Dockerfile` + `docker-compose.yml` for all four services

**Backlog fixes after first live run (CSK vs KKR, 2026-04-14):**
- B2: innings end condition for loss-by-runs
- B3: iterative smart wait (self-correcting)
- M3: forecast threshold 0.55 → 0.60
- U1: cumulative score column in Phase 3 table
- U2: win%/hotness as percentages
- P1: strategic timeout detection via commentary text
- P2: ping-pong buffer for raw inn2 files
- P3: super over detection + looping

**Tests:** 30 unit tests in `tests/test_poller_changes.py`

### Key observations from first live run

- Win prob and hotness tracked match drama accurately (peak hotness 0.912 at over 8.5 when genuinely 50/50)
- Momentum cliff at 6-ball boundary visible (backlog M1 — not yet fixed)
- Engine restart mid-match wiped state; forecaster delayed as a result (backlog B1 — not yet fixed)
- Win prob slightly overconfident at extreme wicket-loss scenarios (backlog M2 — not yet fixed)

### Remaining backlog (group 3)

- **B1** — engine restart state replay from `ball_events.jsonl`
- **M1** — momentum smoothing (EMA instead of hard 6-ball window)
- **M2** — win prob calibration at extremes
- **P4** — post-match Cricsheet data collection script
- **A1** — match analysis / debug view (scope TBD)
