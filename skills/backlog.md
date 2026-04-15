# Backlog — Cricket Hot Match Detector

Prioritised list of fixes and improvements. ✅ = implemented.

---

## Bugs (correctness)

### B1 — Engine restart loses in-memory state
**Files:** `polling/poller.py`, `engine/orchestrator.py`  
**Problem:** Engine restart wipes `balls_faced`, win prob history, and hotness deque. Poller skips seen balls (correct), but engine has no context → forecaster delayed, gate hit late.  
**Fix:** On detecting engine session missing (404 on `/match/{id}/state`), replay `ball_events.jsonl` through `/match/{id}/ball` before resuming live polling. Rebuilds all in-memory state correctly.

---

### B2 — Innings end condition missing (loss by runs) ✅
**Files:** `polling/poller.py` → `_phase3_poll_inn2()`  
**Fix:** Added `balls_remaining == 0` as third end condition alongside `wickets >= 10` and `runs_needed == 0`.

---

### B3 — Smart wait undershoots (iterative approach needed) ✅
**Files:** `polling/poller.py` → `_smart_wait_for_inn1_complete()`  
**Fix:** Iterative loop — fetch inn1, sleep `remaining × 35s`, repeat until `bowled >= 120`. Self-corrects on each undershoot.

---

## Model / formula improvements

### M1 — Momentum hard cliff at 6-ball window boundary
**Files:** `engine/hotness.py`  
**Problem:** Momentum = `|win_prob[now] - win_prob[-6]|`. When a high-momentum event (wicket, six) exits the 6-ball window, hotness drops sharply in a single ball with no real-world trigger.  
**Fix:** Replace point comparison with exponential moving average or weighted rolling mean over the window. Gradual decay instead of cliff.

---

### M2 — Win prob overconfident at extremes
**Files:** `engine/win_prob.py` (inference), or retrain  
**Problem:** At 6 wickets down needing 80+ off remaining balls, model outputs ~0.05–0.07 instead of ~0.01–0.02. Likely symmetric (too high floor AND too high ceiling).  
**Fix:** Temperature scaling on model output, or output clipping at extremes. Alternatively retrain with better representation of late-wicket / blowout scenarios.

---

### M3 — Forecast threshold too low ✅
**Files:** `engine/signals.py` → `FORECAST_THRESHOLD`  
**Fix:** Changed `FORECAST_THRESHOLD` from `0.55` → `0.60`.

---

## UX / display improvements

### U1 — Add cumulative score/wickets to Phase 3 table ✅
**Files:** `polling/poller.py` → `_print_ball()`  
**Fix:** Added `{wickets}w/{runs_needed}rr` score column to the printed table.

---

### U2 — Display Win% and Hotness as percentages ✅
**Files:** `polling/poller.py` → `_print_ball()`  
**Fix:** Formatted as `38.4%` and `46.1%`. Column widths adjusted.

---

## Polling behaviour

### P1 — Stale warnings fire during strategic timeouts ✅
**Files:** `polling/poller.py` → `_detect_timeout()`, `_phase3_poll_inn2()`  
**Fix:** On no-new-balls poll, scan the 10 most recent commentary items for timeout keywords (`"strategic timeout"`, `"timeout"`, `"strategic break"`). If found, sleep 150s and skip stale check. Commentary-driven, not fixed-over.

---

### P2 — Raw inn2 poll files accumulate (215 files per match) ✅
**Files:** `polling/poller.py` → `_phase3_poll_inn2()`  
**Fix:** Ping-pong buffer — alternate writes between `raw_inn2_a.json` and `raw_inn2_b.json`. One file always intact if a crash corrupts the one being written.

---

### P3 — Super over not detected ✅
**Files:** `polling/poller.py` → `_phase3_poll_inn2()`  
**Problem:** If a T20 ends in a tie, a super over is bowled. Poller would stop at `balls_remaining == 0` (ball 120) and miss it entirely.  
**Fix (two signals):**
1. **Commentary text**: scan for `"super over"` keyword — if found after ball 120, print notice and continue polling.
2. **Ball count**: if `balls_faced > 120`, don't stop on `balls_remaining == 0`.  
Note: engine's `balls_fraction` hardcoded to `/120` — super over balls slightly mis-calibrate models, acceptable for now.

---

### P4 — Post-match Cricsheet data collection
**Files:** New script `scripts/fetch_cricsheet.py`  
**Problem:** Cricsheet JSON for a match becomes available the day after. No automated way to fetch and store in `data/raw/` for retraining/validation.  
**Fix:** Script accepts match date + team names, queries Cricsheet download endpoint, saves to `data/raw/`. See `skills/fetch_ball_by_ball.md` for Cricsheet API details.

---

## Analysis & debug

### A1 — Match analysis / debug view
**Files:** New service, e.g. `analysis/` or extension of `orchestrator/`  
**Scope:** Debug and post-match analysis tool that reads `engine_outputs.jsonl` and `ball_events.jsonl` and produces:
- Win prob curve (ball-by-ball line chart)
- Hotness curve (with forecast overlay where available)
- "Is getting hot" signal timeline — when/if in-game signal fired and at what ball
- Key turning points — balls with largest win prob swing (wickets, boundaries)
- Raw ball-by-ball table (over, runs, extras, wicket, win%, hotness, forecast, signals)
- Works both in real-time (reads live JSONL as it grows) and post-match (reads completed file)  
**Format:** TBD — could be a Streamlit page, a CLI that outputs to terminal, or a Jupyter notebook template. Needs more design thought before implementation.
