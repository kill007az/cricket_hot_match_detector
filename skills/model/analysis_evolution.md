# Analysis Evolution — How We Got Here

Timeline of the analysis from first notebook to current state. Load this to understand what was tried, what failed, and why we made each decision.

---

## Phase 1: Can we even detect hotness? (NB 01)

**Question**: Given a ball-by-ball chase, can we compute a "hotness" score that separates exciting from boring matches?

**Approach**: Built a sigmoid-based win probability formula, computed closeness + momentum, defined hotness as a weighted combination.

**Result**: Worked on obvious cases (DC vs GT last-ball finish = hot, MI winning by 100 = cold). Failed on edge cases — the formula gave 8% start probability for chasing 211, which is wrong.

**Key decision**: Win probability is the foundation of everything. If it's wrong, hotness is wrong. Need a better win prob model.

---

## Phase 2: Empirical win probability (NB 02)

**Question**: Can we build win probability from historical data instead of a formula?

**Approach**: Binned all 1,184 IPL match states by (runs_needed, balls_remaining, wickets) → computed observed win rate per bin. Applied Savitzky-Golay smoothing to reduce noise from sparse bins.

**Result**: Much better calibration — 211 target correctly starts at ~35%, low targets at ~80-95%. But jagged due to bin sparsity, and the lookup table has holes.

**Key decision**: Use this as training signal for a neural net that can generalise smoothly.

---

## Phase 3: Neural net win probability (NB 03)

**Question**: Can a small NN fit the empirical win rates and produce a smooth, generalisable function?

**Approach**: 6-feature MLP (3,073 params), trained on empirical bin win rates as soft regression targets (not raw 0/1 outcomes). MSE loss, z-score normalisation.

**Result**: Clean, smooth win probability curves. Matches empirical data closely. Saved as `models/win_prob_nn.pt`.

**Key decision**: This is the win prob model going forward. All downstream work (hotness, forecasting) builds on it.

---

## Phase 4: Interpretability (NB 04)

**Question**: What is the NN actually learning? Does it match cricket intuition?

**Approach**: Captum integrated gradients, sensitivity analysis, heatmaps, feature ablation.

**Result**: Confirmed the NN correctly weights: wickets matter more in death overs, runs_needed dominates early, rrr is the key derived feature. No surprises — the model learned cricket.

---

## Phase 5: Hotness on all matches (NB 06)

**Question**: Does the NN-based hotness pipeline work on new unseen matches?

**Approach**: Applied full pipeline (NN win prob → closeness + momentum → hotness) to 6 matches including 2 new ones (KKR vs LSG, RCB vs RR).

**Result**: Exposed three problems with the reactive detector:
1. **KKR vs LSG onset at ball 1** — target 182 sits at 50% from the start, closeness fires immediately regardless of actual drama
2. **RCB vs RR onset at ball 13** — brief early momentum blip from batting blitz, match was a blowout
3. **KKR vs LSG reactive fires at ball 119** — too late for a live notification

**Key decisions**:
- The reactive hotness score IS correct for measuring drama — the problems are in the notification trigger logic
- Need a 60-ball gate: no in-game notifications before over 10
- Need a pre-match signal: flag structurally close targets (win_prob 0.40–0.60 at ball 1) separately
- Need forecasting: predict upcoming drama instead of just detecting current drama

---

## Phase 6: Hotness forecasting (NB 07) — CURRENT

**Question**: Can we predict that a match is about to become hot, before the drama actually happens?

**Approach**: Autoregressive MLP — 12 balls of hotness history + balls_remaining → predicted max hotness in next 6 balls. Trained on 108,808 windows from 1,159 IPL matches.

**Key findings**:
- Autocorrelation at lag 6 = 0.537 — hotness IS predictable from its own history
- Correlation between current and future hotness: 0.83–0.90 across all match phases
- Model converges cleanly, no overfitting (train MSE 0.0149, val MSE 0.0153)

**Results on validation matches** (with 60-ball gate):

| Match | What happens | Lead time gained |
|---|---|---|
| DC vs GT (HOT) | Fires at ~ball 60, match stays hot to last ball | Full second half coverage |
| IND vs PAK (HOT) | Fires ball 79 (over 14), Bumrah spell starts over 16 | 7 balls ahead |
| RR vs MI (COLD) | Never fires | Correct |
| MI vs RR (COLD) | Never fires | Correct |
| KKR vs LSG (HOT) | Fires ~ball 90, actual drama at ball 114 | ~24 balls (4 overs) ahead |
| RCB vs RR (MODERATE) | Never fires (with gate) | Correct — blowout suppressed |

**Known issues**:
- Forecast slightly over-predicts on cold windows (outputs ~0.15–0.25 instead of ~0). Cosmetic — never crosses threshold.
- Edge-clipped hotness targets (pile-up at 0.0 and 1.0) aren't ideal for MSE + Sigmoid.
- Not truly autoregressive (doesn't feed own predictions back) — uses actual computed hotness. This is correct for live use since actual hotness is always available in real-time.

---

## Phase 7: Production engine — Sprint 1 (2026-04-14)

**Question**: Can the full pipeline be packaged as a production-ready engine with an HTTP API?

**Approach**: Sprint-driven build from a formal feature spec (`feature_docs/feature_doc_sprint_1.md`). Engine split into pure Python package + FastAPI layer. Full match simulation test against the live API.

**Result**: Fully functional. KKR vs LSG replayed ball-by-ball in ~3 seconds. Pre-match signal fires correctly at ball 1. In-game signal fires at balls 60–62 (mild false positive at gate boundary) and 119–120 (correct, actual last-over drama).

**Key findings from simulation run**:
- Engine bottleneck: `win_prob` NN inference (72% of server time, ~0.33ms/ball)
- Total engine time: ~0.46ms/ball; HTTP round-trip: ~3–5ms/ball (after `requests.Session()` fix)
- Balls 60–62 false positive caused by ball 30 hotness=1.0 spike inflating the history window at gate boundary — threshold calibration needed
- In-game signal fires on consecutive balls (60, 61, 62) — signal deduplication not yet implemented

**Key decisions**:
- `BallEvent` = legal deliveries only; upstream filters wides/no-balls
- `balls_fraction` hardcoded to `/120.0` to match NN training
- Sessions in-memory; stateless across server restarts
- `requests.Session()` mandatory for simulation — per-request TCP setup costs ~2s on Windows

For full engineering detail see `skills/engine_sprint_1.md`.

---

## Current state (as of 2026-04-14)

### What's built and working
- **Win probability model**: NN trained on 1,184 IPL matches, saved (`win_prob_nn.pt`)
- **Hotness score**: closeness + momentum formula, validated on 6 matches
- **Hotness forecaster**: autoregressive MLP, saved (`hotness_forecaster.pt`)
- **Production engine**: `engine/` package + `api/` FastAPI layer, fully runnable
- **Simulation test**: KKR vs LSG replayed end-to-end with latency profiling

### Live notification architecture (built)
```
POST /match/init  (ball 0 — feed inn1 summary)

Ball arrives → POST /match/{id}/ball
  → ChaseState updated
  → features extracted
  → win_prob (NN)
  → hotness (closeness + momentum)
  → IF ball == 1 AND win_prob in [0.40, 0.60]:
      signal: "50/50 chase — worth watching from the start"
  → IF ball >= 60 AND history >= 12:
      forecast (NN)
      IF forecast >= 0.55:
        signal: "match heating up — tune in now"
  → EngineOutput returned (win_prob, hotness, forecast, signals, processing_ms)
```

### What's NOT built yet
- Live data ingestion (cricsheet is historical only)
- Notification delivery (Telegram bot / push)
- Threshold calibration on a larger validation set
- Signal deduplication (in-game signal fires on every ball above threshold, not once per crossing)
- Persistent session store (sessions lost on server restart)
- First innings signals

### Model artifacts
| File | Purpose |
|---|---|
| `models/win_prob_nn.pt` | Win probability NN (3,073 params) |
| `models/emp_lookup.pkl` | Empirical lookup fallback |
| `models/hotness_forecaster.pt` | Hotness forecaster (3,009 params) |
