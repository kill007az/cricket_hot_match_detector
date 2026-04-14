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

- **Threshold calibration** — `FORECAST_THRESHOLD = 0.55` in `signals.py` is
  from NB07 exploration, not formally tuned. Run on more matches.
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
