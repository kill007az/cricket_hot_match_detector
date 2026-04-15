# Engine Build — Sprint 1 Reference

Authoritative reference for the production engine built in sprint 1. Load this to resume engineering work without re-reading source files.

---

## What was built

A fully functional ball-by-ball cricket hotness engine with:
- Pure Python engine package (`engine/`) — no HTTP dependency, testable standalone
- FastAPI HTTP layer (`api/`) — 3 routes + 1 debug endpoint
- Simulation test (`tests/simulate_hot_match.py`) — full match replay with latency stats

---

## Project layout

```
engine/
    models.py        — BallEvent, ChaseState, HotnessState, MatchSession, EngineOutput
    state.py         — StateUpdater (immutable per-ball update)
    features.py      — FeatureExtractor (6 features, hardcoded /120)
    win_prob.py      — WinProbModel (loads win_prob_nn.pt)
    hotness.py       — HotnessCalculator (closeness + momentum)
    forecaster.py    — HotnessForecaster (loads hotness_forecaster.pt)
    signals.py       — SignalEvaluator (pre-match + in-game thresholds)
    orchestrator.py  — EngineOrchestrator (pipeline coordinator, sessions dict)

engine/  (routes + server moved here from api/ in sprint 2)
    server.py        — FastAPI app, lifespan model loading onto app.state.engine
    routes.py        — POST /match/init, POST /match/{id}/ball, GET /match/{id}/state,
                       GET /debug/latency

tests/
    simulate_hot_match.py  — Replays KKR vs LSG 2026-04-09 via HTTP
```

---

## Key data contracts

### BallEvent — legal deliveries only

```python
BallEvent(
    match_id = "kkr_vs_lsg_2026-04-09",
    innings  = 2,
    over     = 14.3,   # over 14 (0-indexed), 3rd legal ball in that over
    runs     = 4,      # batter runs
    extras   = 0,      # byes/leg-byes only — wides/no-balls are NOT sent
    wicket   = False,
    timestamp = datetime.utcnow(),
)
```

**The engine only receives legal deliveries.** The upstream data source filters wides and no-balls. Every BallEvent increments `balls_faced` by exactly 1.

**Deduplication key:** `f"{match_id}:{innings}:{over:.1f}"` — safe to re-POST on network retry. Returns `is_duplicate: True` without mutating state.

### ChaseState initialisation

```python
engine.init_match(
    match_id    = "kkr_vs_lsg_2026-04-09",
    target      = 182,    # inn1 runs + 1
    total_balls = 120,    # actual legal balls in inn1 — NOT overs * 6
)
```

`total_balls` must be counted from actual innings 1 deliveries, not `info.overs`. DLS/rain matches have fewer than 120.

---

## Pipeline (per ball)

```
1. Idempotency check    — skip if ball_key already in session.processed_balls
2. State update         — runs_scored += runs + extras; balls_faced += 1; wickets += wicket
3. Feature extraction   — [runs_needed, balls_remaining, wickets, rrr, balls_fraction, wickets_fraction]
4. Win probability      — NN inference (win_prob_nn.pt)
5. Hotness              — closeness * 0.6 + momentum * 5 * 0.4, clipped [0,1]
6. Forecast (gated)     — NN inference (hotness_forecaster.pt) only if balls_faced >= 60
                          AND len(hotness_history) >= 12
7. Signal evaluation    — pre-match (ball 1, WP in [0.4, 0.6]) + in-game (ball >= 60, forecast >= 0.55)
8. Commit + emit        — update session state, return EngineOutput with processing_ms
```

---

## Critical implementation notes

**`balls_fraction = balls_remaining / 120.0` — hardcoded.**
Both NNs trained with this. Using `/total_balls` produces wrong features even though it looks more correct. Do not change.

**`rrr = runs_needed / max(balls_remaining, 1)` — denominator floored at 1.**
Prevents divide-by-zero at end of innings.

**Hotness history appended BEFORE forecasting.**
The forecaster input is the last 12 hotness values including the current ball. This matches training where window = `h[t-12:t]` and the current ball is at index `t-1`.

**Momentum uses win_prob_history[-6], not hotness_history.**
The history deque is populated BEFORE `hotness.compute()` is called for past balls, but the CURRENT ball's win_prob has not been appended yet when momentum is calculated. This is intentional — momentum = `|current_wp - wp_6_balls_ago|`.

---

## API routes

| Method | Path | Body | Returns |
|---|---|---|---|
| `POST` | `/match/init` | `{match_id, target, total_balls}` | `{match_id, target, total_balls, message}` |
| `POST` | `/match/{id}/ball` | `{innings, over, runs, extras, wicket, timestamp?}` | `EngineOutput` |
| `GET` | `/match/{id}/state` | — | `ChaseState + last EngineOutput` |
| `GET` | `/debug/latency` | — | Per-step mean latency breakdown |

---

## Simulation results (KKR vs LSG 2026-04-09)

| Signal | Ball | Over | Notes |
|---|---|---|---|
| Pre-match | 1 | 0.1 | Target 182 ≈ 49% WP — correct |
| In-game | 60–62 | 9.6–10.2 | Mild false positive — forecast just scraped 0.55 (0.586→0.551→0.551). Ball 30 hotness spike (1.0) inflated history. |
| In-game | 119–120 | 19.5–19.6 | Correct — actual last-over drama, LSG won by 3 wkts on last ball |

The ball 60–62 false positive is caused by:
1. The forecast threshold (0.55) not being calibrated — it's exploratory from NB07
2. The ball 30 hotness=1.0 spike (a large mid-match momentum swing) pushing the 12-ball history window high at ball 60

---

## Latency profile (typical, localhost, after Session fix)

| | mean | bottleneck |
|---|---|---|
| Engine (server-side) | ~0.5ms/ball | win_prob NN (~72%), forecaster NN (~21%) |
| HTTP round-trip | ~3–5ms/ball | TCP overhead on keep-alive session |

Before the `requests.Session()` fix, each request opened a new TCP socket, costing ~2s on Windows. Always use `requests.Session()` for sequential API calls.

---

## Known issues / sprint 2 candidates

| Issue | Impact | Fix |
|---|---|---|
| Forecast threshold 0.55 not calibrated | False positives at gate boundary | Calibrate on 10+ labelled matches; tune per recall target |
| In-game signal fires on consecutive balls | Spam if drama lasts >1 ball | Add "fire-once-per-match" or "cooldown N balls" logic in `signals.py` |
| Sessions are in-memory | Restart loses all active matches | Add Redis/SQLite session store |
| No API authentication | Unsafe to expose publicly | Add API key header or OAuth |
| `--reload` adds file-watch overhead | Cosmetic in dev | Use `--no-reload` or a production server (gunicorn) in prod |

---

## How to run

```bash
# Start API (from repo root)
conda activate cricket_hot
uvicorn api.main:app --reload --port 8000

# Run simulation (new terminal)
conda activate cricket_hot
python -m tests.simulate_hot_match
```

Conda env: `cricket_hot` at `C:\Users\hp\.conda\envs\cricket_hot`
