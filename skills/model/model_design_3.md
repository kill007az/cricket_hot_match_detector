# Model Design & Methodology — Cricket Hot Match Detector (v3)

Reference doc covering all design decisions, iterations, and findings to date.
Load this to resume context without re-reading notebooks.

**Changes from v2**: NB07 results incorporated, forecaster evaluation complete, live notification architecture finalised.

---

## Problem statement

Detect in real-time whether an ongoing T20 cricket match is becoming "hot" (competitively tense, worth tuning in to watch) and send a notification. The signal must be computed ball-by-ball from live data.

**User preference**: recall over precision — missing an exciting match is worse than a false alert.

---

## Data source

**cricsheet.org** — ball-by-ball JSON for all matches. See `skills/data/fetch_ball_by_ball.md` for retrieval.

Training data: **1,159 IPL matches** (2008–2026) successfully parsed, ~128,500 ball-states extracted from 2nd innings chases.

---

## Core insight: only the chase matters

Hotness is computed entirely from the **2nd innings (chase)**. The 1st innings score sets the target; what makes a match hot is whether that target is being closely contested ball by ball.

---

## Feature engineering

Per legal delivery in the chase, we compute:

| Feature | Formula | Notes |
|---|---|---|
| `runs_needed` | `target - score` | Raw |
| `balls_remaining` | `(total_balls - legal_ball).clip(0)` | Clipped to prevent negatives from extras mismatch |
| `wickets_fallen` | cumulative | 0–10 |
| `rrr` | `runs_needed / max(balls_remaining, 1)` | Required run rate per ball; denominator floored at 1 to avoid div/0 |
| `balls_fraction` | `balls_remaining / 120` | Normalised urgency |
| `wickets_fraction` | `wickets_fallen / 10` | Normalised pressure |

**Critical**: `total_balls` must be inferred from innings 1 actual legal deliveries, not `info.overs`. Rain-reduced matches (e.g. RR vs MI 2026 was 11 overs = 66 balls) would be completely wrong otherwise.

---

## Win probability — evolution

### v1: Formula (abandoned)
```python
avg_rate = 1.45  # runs/ball
sensitivity = 15
expected = balls_remaining * avg_rate * (wickets_in_hand / 10) ** 0.5
win_prob = sigmoid((expected - runs_needed) / sensitivity)
```
**Problem**: `120 * 1.45 = 174` — any target above 174 starts near 0%. Completely wrong for high targets like 210.

### v2: Tuned formula (used in nb01, nb02)
```python
avg_rate = 1.667  # 120 * 1.667 = 200 → 200 is 50/50
sensitivity = 20  # softer curve
```
Better but still symmetric — doesn't capture that wickets matter more in death overs.

### v3: Empirical lookup (nb02)
Built from 1,184 IPL matches. Bin → observed win rate. Jagged/noisy due to sparse bins; applied Savitzky-Golay smoothing.

### v4: Neural Network — MSE (nb03, superseded)
Trained to fit the empirical bin win rates as soft regression targets. Smooth by construction.
Backed up as `models/win_prob_nn_mse_nb03.pt`.

### v5: Neural Network — BCE (nb09 — current, saved to models/)
Same architecture as v4 but trained with `BCEWithLogitsLoss` on raw `chaser_won` (0/1) labels.
No binning, no smoothing. Better calibrated at tail states (M2 fix).
Sigmoid removed from model; applied manually in `predict()` after getting logit.

---

## NN Win Probability Architecture

```
Input (6) → Linear(64) → ReLU → Dropout(0.1)
          → Linear(32) → ReLU → Dropout(0.1)
          → Linear(16) → ReLU → Dropout(0.1)
          → Linear(1)  [logit — sigmoid applied in predict()]
```

- **Parameters**: 3,073
- **Loss**: `BCEWithLogitsLoss` on raw `chaser_won` (0/1) labels (NB09). Previous: MSE on soft bin averages (NB03).
- **Optimiser**: Adam, lr=1e-3, 50 epochs, batch 512, 85/15 split
- **Input normalisation**: z-score per feature (mean/std saved in checkpoint)
- **Data cleaning**: rows with `balls_remaining <= 0` dropped; `rrr` clipped to [0, 6]

**Saved artifacts**:
- `models/win_prob_nn.pt` — weights + architecture config + normalisation stats
- `models/emp_lookup.pkl` — empirical lookup dict as fallback

---

## Hotness score

```python
closeness = 1 - 2 * abs(win_prob - 0.5)   # peaks at 1 when win_prob = 0.5
momentum  = abs(win_prob.diff(6))           # abs change over last 6 balls

hotness = (closeness * 0.6 + momentum * 5 * 0.4).clip(0, 1)
```

**Weights**: 0.6 closeness, 0.4 momentum.

**Note on clipping**: the `.clip(0, 1)` creates spikes at the boundaries in the training distribution. Hotness values pile up at exactly 0.0 (blowouts) and 1.0 (peak drama). This makes the Sigmoid-output forecaster slightly over-predict lows and under-predict highs. Acceptable for now.

**Dataset statistics** (from NB07 on 1,159 matches):
- Mean hotness per ball: 0.364
- 25.1% of balls exceed 0.55 (current threshold is 0.60 — calibrated after first live run)
- 79% of IPL matches have at least one ball exceeding 0.55
- 9.5% of balls exceed 0.70

---

## Two types of "hot" match

| Type | Mechanism | Example |
|---|---|---|
| **Knife-edge finish** | Win prob sustained near 50% in death overs | DC vs GT — last ball finish |
| **Collapse drama** | Rapid wicket cluster swings momentum | IND vs PAK — Bumrah 4/14 |

---

## Reactive detector limitations (discovered in nb06)

### Problem 1: Structural closeness false positive
**KKR vs LSG** — target 182 ≈ 50% win prob from ball 1. Closeness fires immediately. Real drama only at ball 114+.

### Problem 2: Brief momentum blips
**RCB vs RR** — onset at ball 13 from early batting blitz. Match was a 6-wicket blowout.

### Problem 3: Reactive lag for compressed drama
KKR vs LSG: sustained-6-balls threshold fires at ball 119 — too late for notification.

---

## Hotness forecasting (nb07)

### Motivation
Reactive detector answers "is the match hot now?" — but for a live service we need "is this match about to become hot?" with lead time.

### Approach: autoregressive
- **Input**: 12-ball hotness history + balls_remaining/120 → **13 features**
- **Target**: `max(hotness[t:t+6])` — peak hotness in the next 6 balls
- **Why 12→6**: momentum uses 6-ball diff; 12 balls gives 2 full momentum windows to detect acceleration

### Why autoregressive on hotness
Hotness already encodes win_prob (closeness) and momentum (diff). It's a compressed match-state signal. The model doesn't feed its own predictions back — it always uses actual computed hotness up to the current ball (available in real-time).

### Architecture
```
Input (13) → Linear(64) → ReLU → Dropout(0.15)
           → Linear(32) → ReLU → Dropout(0.15)
           → Linear(1)  → Sigmoid
```

- **Parameters**: 3,009
- **Training samples**: 108,808 windows from 1,159 matches
- **Loss**: MSE, 60 epochs, batch 512, 85/15 split
- **Final**: train MSE 0.01488, val MSE 0.01534 (no overfitting)
- **Normalisation**: z-score on 12 hotness lags (mean/std saved); balls_remaining already [0,1]

### Autocorrelation validation (NB07 finding)
- ACF at lag 6: **0.537** ± 0.264 — strong, autoregression viable
- ACF at lag 12: **0.336** ± 0.271 — still meaningful
- Correlation between current and future max hotness: **0.83–0.90** across all match phases

### Forecast evaluation results

| Match | Reactive onset | Forecast (raw) | Forecast (with 60-ball gate) | Verdict |
|---|---|---|---|---|
| DC vs GT (HOT) | ball 34 | ball 14 | ~ball 60 | Fires at halfway, match stays hot |
| IND vs PAK (HOT) | ball 86 | ball 79 | ball 79 | +7 ball lead |
| RR vs MI (COLD) | never | never | never | Correct |
| MI vs RR (COLD) | never | never | never | Correct |
| KKR vs LSG (NEW) | ball 1 (false) | ball 13 (false) | **~ball 90** | 24 balls before actual drama |
| RCB vs RR (NEW) | ball 22 (false) | ball 13 (false) | **never** | Blowout correctly suppressed |

### Known forecaster quirks
- **Slight over-prediction on cold windows**: model outputs ~0.15–0.25 where true value is ~0. Sigmoid+MSE regresses toward mean. Never enough to cross threshold — cosmetic only.
- **Edge-clipped targets**: hotness clipped to [0,1] creates spikes in target distribution at boundaries. Forecaster can't perfectly match these.

**Saved artifacts**:
- `models/hotness_forecaster.pt` — weights + architecture + normalisation stats

---

## Live notification architecture

Two signals, independent:

| Signal | When | Logic | Message |
|---|---|---|---|
| **Pre-match** | Ball 1 of chase | win_prob at start between 0.40–0.60 | "This is a 50/50 chase — worth watching" |
| **In-game forecast** | Ball 60+, win_prob 0.25–0.75 | forecaster output ≥ threshold | "Match is heating up — tune in now" |

The 60-ball gate (halfway mark, over 10) suppresses early false positives from structurally close targets. The forecaster provides lead time over the reactive detector.

**Win prob gate (0.25–0.75)**: added after CSK vs KKR 2026-04-16 live run. The forecaster over-amplified a momentum spike in overs 8–9 when win_prob was ~0.20 (KKR clearly losing), firing a false IN_GAME signal. The gate ensures the forecaster is only trusted when the match is genuinely in play. Validated in NB08 — no false positives on any COLD match, no delay to HOT match alerts.

**Implemented** — see `engine/signals.py` for logic, `engine/routes.py` for HTTP surface.

---

## Live service architecture

The full live pipeline is deployed as four Docker Compose services:

```
Cricbuzz (unofficial JSON API)
  → polling/cricbuzz_client.py   # rate-limited, retry + exponential backoff
  → polling/adapter.py           # Cricbuzz items → BallEvent dicts
  → polling/poller.py            # LivePoller (3 phases below)
  → POST /match/{id}/ball        # engine HTTP API
  → engine/orchestrator.py       # NN pipeline
  → EngineOutput persisted as JSONL
  → orchestrator/main.py         # reads JSONL, serves /matches/* endpoints
  → ui/app.py                    # Streamlit dashboard, auto-refreshes every 10s
```

### LivePoller phases

| Phase | Description |
|---|---|
| Phase 1 | Poll `find_live_match()` until the target match appears in Cricbuzz live listings |
| Phase 2 | Poll inn2 commentary until first legal ball appears |
| Phase 2.5 | One blocking fetch of completed inn1 → count legal balls + sum runs → POST /match/init |
| Phase 3 | Continuous inn2 polling loop; send only new (unseen) legal balls; save JSONL; stop on match over |

### Resilience

- **Retry policy**: transient 5xx / connection errors → exponential backoff (2s → 4s → 8s ±20% jitter, max 30s), max 3 attempts
- **Rate limiting**: 429 responses → flat 60s wait before retry
- **Stale detection**: if no new ball received for 3 minutes → WARNING logged; 10 minutes → louder warning
- **Resume support**: on restart, loads seen ball keys from `ball_events.jsonl` → no duplicate sends

### Data persistence (`data/live_polls/{match_id}/`)

| File | Content |
|---|---|
| `raw_inn1_{HHMMSS}.json` | Full inn1 Cricbuzz response (one-shot) |
| `raw_inn2_{HHMMSS}.json` | Full inn2 Cricbuzz response per poll |
| `ball_events.jsonl` | One BallEvent dict per legal delivery sent |
| `engine_outputs.jsonl` | One EngineOutput dict per ball |

### Entry points

| Command | Description |
|---|---|
| `docker compose up --build` | Start all four services |
| `python run.py` | Local: spawns engine subprocess + runs poller |
| `python -m polling.run_live` | Poller only (engine must already be running) |

---

## Validation matches (final)

| Match | Label | Outcome | Max hotness | Best notification timing |
|---|---|---|---|---|
| DC vs GT, IPL 2026-04-08 | HOT | GT won by 1 run | 1.000 | Pre-match (target 211 ≈ 35% WP) + forecast fires ~ball 60 |
| IND vs PAK, T20 WC 2024-06-09 | HOT | India won by 6 runs | 0.935 | Forecast fires ball 79 (over 14), 35 balls before end |
| RR vs MI, IPL 2026-04-07 | COLD | RR won by 27 (rain) | 0.442 | Correctly silent throughout |
| MI vs RR, IPL 2025-05-01 | COLD | MI won by 100 runs | 0.319 | Correctly silent throughout |
| KKR vs LSG, IPL 2026-04-09 | HOT | LSG won by 3 wkts last ball | 1.000 | Pre-match (182 ≈ 49% WP) + forecast fires ~ball 90 (24 balls lead) |
| RCB vs RR, IPL 2026-04-10 | MODERATE | RR won by 6 wkts chasing 202 | 0.950 | Correctly silent with 60-ball gate |

---

## Known edge cases

| Case | Type | Severity | Notes |
|---|---|---|---|
| Dominant-team wobble | False positive | Low | Brief scare is worth catching per user preference |
| Blowout momentum spike (forecaster) | False positive | Medium → mitigated | Forecaster over-amplifies pre-gate momentum when win_prob < 0.25. Fixed by WP gate in signals.py. |
| Tail-ender heroics (extreme) | False negative | Low | Only truly hopeless chases (9/10 down) miss — 7-down last-ball wins are caught |
| D/L mid-chase | False positive | Medium | Target revision causes artificial momentum spike. Rare. |
| Non-IPL calibration | Mis-calibration | High if used beyond IPL | NN trained on IPL scoring rates only |

---

## Notebook map

| Notebook | Purpose |
|---|---|
| `01_match_analysis.ipynb` | Initial exploration, formula win prob, hotness on 4 matches |
| `02_empirical_win_prob.ipynb` | Empirical model from 1184 IPL matches, formula tuning |
| `03_nn_win_prob.ipynb` | NN training, comparison vs empirical, model save |
| `04_model_interpretability.ipynb` | Captum: integrated gradients, sensitivity, heatmaps, feature ablation |
| `06_hotness_nn.ipynb` | Final hotness pipeline using NN win prob, all 6 matches |
| `07_hotness_forecast.ipynb` | Autoregressive hotness forecasting, exploratory viz, model training |
| `08_hotness_formula_tuning.ipynb` | Hotness formula variants × 7 validation matches; WP gate diagnosis on CSK vs KKR |

---

## Open questions / next steps

- **Threshold tuning**: calibrate forecast threshold for recall-first strategy on larger validation set
- **Feature-augmented forecaster**: if autoregressive isn't enough for edge cases, add win_prob + raw features as inputs
- **Notification delivery**: push notification wiring (Telegram / webhook) — currently signals are printed to console and stored in JSONL
- **First innings signals**: currently only 2nd innings — a dominant 1st innings (e.g. 240) might be worth a heads-up
- **Multi-match support**: poller currently targets one match; could run multiple LivePoller instances in parallel for days with concurrent IPL fixtures
- ~~**Notification pipeline**: live data polling + engine wiring~~ — *done: polling service + engine API + Docker compose*
- ~~**Live data source**: cricsheet is historical only~~ — *done: Cricbuzz unofficial JSON API via `polling/cricbuzz_client.py`*
