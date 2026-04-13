# engine/

The core detection engine. Pure Python — no HTTP, no I/O. Takes a `BallEvent`, returns an `EngineOutput`. Can be used standalone or behind any transport layer.

---

## Module map

| Module | Responsibility |
|---|---|
| `models.py` | All data structures |
| `state.py` | Per-ball `ChaseState` update |
| `features.py` | 6-feature vector extraction |
| `win_prob.py` | Win probability NN wrapper |
| `hotness.py` | Hotness score formula |
| `forecaster.py` | Hotness forecaster NN wrapper |
| `signals.py` | Signal evaluation (pre-match + in-game) |
| `orchestrator.py` | Full pipeline, session management |

---

## Pipeline

Each call to `EngineOrchestrator.process_ball(event)` runs these steps in order:

```
1. Idempotency check    — return cached output if ball already seen
2. State update         — runs_scored, wickets, balls_faced
3. Feature extraction   — 6 features for the win-prob NN
4. Win probability      — NN inference → float in (0, 1)
5. Hotness              — closeness + momentum formula → float in [0, 1]
6. Forecast (gated)     — NN inference if ball >= 60 AND >= 12-ball history
7. Signal evaluation    — check pre-match and in-game thresholds
8. Commit + emit        — update session state, return EngineOutput
```

---

## Data structures

### `BallEvent`

Represents one **legal** delivery in the chase.

```python
@dataclass
class BallEvent:
    match_id: str
    innings: int     # always 2 for a chase
    over: float      # 14.3 = over 14 (0-indexed), 3rd legal ball in that over
    runs: int        # batter runs
    extras: int      # byes / leg-byes (NOT wides or no-balls — those are illegal)
    wicket: bool
    timestamp: datetime
```

**Contract: only legal deliveries are sent.**
Wides and no-balls must be filtered by the upstream data source before calling the engine. This is deliberate:
- It keeps `balls_faced` correct without extra logic
- Extras on a `BallEvent` are byes/leg-byes only — they add to `runs_scored` but not `balls_faced`

**Deduplication key:** `f"{match_id}:{innings}:{over:.1f}"` — safe to re-send without corrupting state.

---

### `ChaseState`

```python
@dataclass
class ChaseState:
    match_id: str
    target: int        # innings 1 score + 1
    total_balls: int   # actual legal balls in innings 1 (NOT overs * 6)

    runs_scored: int = 0
    wickets: int = 0
    balls_faced: int = 0

    # Derived (properties, always consistent):
    runs_needed  = target - runs_scored   (floored at 0)
    balls_remaining = total_balls - balls_faced  (floored at 0)
```

**Why `total_balls` from innings 1 actual deliveries?**
Rain-reduced matches (DLS) have fewer than 120 balls. Using `info.overs * 6` would be wrong. The caller must pass the actual count of legal deliveries from innings 1.

---

### `HotnessState`

```python
@dataclass
class HotnessState:
    win_prob_history: deque(maxlen=12)
    hotness_history:  deque(maxlen=12)
```

Sliding window of the last 12 values. Fed into the hotness formula (momentum uses `win_prob_history[-6]`) and the forecaster (needs 12 hotness lags).

---

### `EngineOutput`

```python
@dataclass
class EngineOutput:
    match_id: str
    win_prob: float          # 0–1
    hotness: float           # 0–1
    forecast: float | None   # None before ball 60 or before 12-ball history
    runs_needed: int
    balls_remaining: int
    wickets: int
    signals: list[str]
    is_duplicate: bool       # True if this ball was already processed
    processing_ms: float     # Server-side engine time (excludes HTTP overhead)
```

---

## Feature extraction

Defined in `features.py`. Output is a `float32` array of length 6 in this exact order:

| Index | Feature | Formula | Notes |
|---|---|---|---|
| 0 | `runs_needed` | `target - runs_scored` | Raw |
| 1 | `balls_remaining` | `total_balls - balls_faced` | Clipped to 0 |
| 2 | `wickets_fallen` | `wickets` | 0–10 |
| 3 | `rrr` | `runs_needed / max(balls_remaining, 1)` | Denominator floored at 1 |
| 4 | `balls_fraction` | `balls_remaining / 120.0` | **Hardcoded 120** — see note |
| 5 | `wickets_fraction` | `wickets / 10` | Normalised |

**`balls_fraction` uses `/120.0` not `/total_balls`.**
Both models were trained with `br/120.0` hardcoded (from NB03 and NB07). Using `/total_balls` would appear more correct but would mis-calibrate model output because it diverges from training distribution.

---

## Hotness formula

```python
closeness = 1 - 2 * abs(win_prob - 0.5)   # 1 when perfectly 50/50, 0 at 0% or 100%
momentum  = abs(win_prob - win_prob[t-6])  # absolute shift over last 6 balls (0 if < 6 history)

hotness = clip(0, 1,  closeness * 0.6  +  momentum * 5 * 0.4)
```

- **Closeness** (60%) is the primary signal — captures knife-edge finishes
- **Momentum** (40%) catches swing/collapse without needing 50/50 — captures Bumrah-style wicket clusters
- Multiplying momentum by 5 brings it into comparable range to closeness (raw values are ~0.05–0.15)

---

## Forecaster

Defined in `forecaster.py`. Predicts `max(hotness[t:t+6])` — peak hotness in the next 6 balls.

**Input (13 features):**
- 12 hotness lag values — z-score normalised using `X_train_mean` / `X_train_std` from checkpoint
- `balls_remaining / 120.0` — already in [0,1], not normalised

**Architecture:**
```
Input(13) → Linear(64) → ReLU → Dropout(0.15)
          → Linear(32) → ReLU → Dropout(0.15)
          → Linear(1)  → Sigmoid
```

**Gate conditions:** forecast is only computed when:
1. `balls_faced >= 60` (halfway mark — suppresses early false positives)
2. `len(hotness_history) >= 12` (enough history to fill the input)

**Known quirk:** Model slightly over-predicts on cold windows (~0.15–0.25 where true value is ~0). This is MSE mean-regression bias and never crosses the 0.55 threshold on genuine blowouts.

---

## Signal thresholds

Defined in `signals.py`. Both are tunable constants:

```python
PRE_MATCH_LOW      = 0.40   # win prob range for pre-match alert
PRE_MATCH_HIGH     = 0.60
FORECAST_THRESHOLD = 0.55   # forecaster output threshold for in-game alert
GATE_BALL          = 60     # no in-game signals before this ball
```

`FORECAST_THRESHOLD` is from NB07 exploration — not formally calibrated on a held-out set. Reduce it to increase recall; raise it to reduce false positives.

---

## Session management

`EngineOrchestrator` holds all active sessions in a dict keyed by `match_id`. Sessions are in-memory only — a server restart loses all state.

```python
engine = EngineOrchestrator(Path("models/"))

engine.init_match("match_001", target=182, total_balls=120)
output = engine.process_ball(event)
```

`init_match` is idempotent — calling it again on an existing `match_id` resets the session.

---

## Model checkpoint formats

### `win_prob_nn.pt`

| Key | Type | Description |
|---|---|---|
| `model_state_dict` | `dict` | PyTorch weights |
| `input_dim` | `int` | 6 |
| `hidden_dims` | `list` | `[64, 32, 16]` |
| `X_mean` | `np.ndarray` | Per-feature z-score means (shape 6) |
| `X_std` | `np.ndarray` | Per-feature z-score stds (shape 6) |
| `feature_cols` | `list[str]` | Feature names in order |

### `hotness_forecaster.pt`

| Key | Type | Description |
|---|---|---|
| `model_state_dict` | `dict` | PyTorch weights |
| `input_dim` | `int` | 13 |
| `hidden_dims` | `list` | `[64, 32]` |
| `lookback` | `int` | 12 |
| `horizon` | `int` | 6 |
| `X_train_mean` | `float` | Mean of hotness lags (scalar — same mean applied to all 12) |
| `X_train_std` | `float` | Std of hotness lags (scalar) |
