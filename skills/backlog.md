# Backlog — Cricket Hot Match Detector

Prioritised list of fixes and improvements. Add new items at the bottom of each section.

---

## Bugs (correctness)

### B1 — Engine restart loses in-memory state
**Files:** `polling/poller.py`, `engine/orchestrator.py`  
**Problem:** Engine restart wipes `balls_faced`, win prob history, and hotness deque. Poller skips seen balls (correct), but engine has no context → forecaster delayed, gate hit late.  
**Fix:** On detecting engine session missing (404 on `/match/{id}/state`), replay `ball_events.jsonl` through `/match/{id}/ball` before resuming live polling. Rebuilds all in-memory state correctly.

---

### B2 — Innings end condition missing (loss by runs)
**Files:** `polling/poller.py` → `_phase3_poll_inn2()`  
**Problem:** End condition only checks `wickets >= 10` or `runs_needed == 0`. If batting team completes 20 overs without being all out and falls short, poller loops forever.  
**Fix:** Add: `balls_remaining == 0` (or `balls_faced >= total_balls`) as a third end condition → print match over, exit Phase 3.

---

### B3 — Smart wait undershoots (iterative approach needed)
**Files:** `polling/poller.py` → `_phase2_wait_for_inn2()`, `_estimate_inn1_remaining_secs()`  
**Problem:** One-shot sleep of `remaining_balls × 35s` ends too early — real pace per ball is higher than 35s.  
**Fix:** Replace one-shot sleep with iterative loop:
1. Fetch inn1, count balls bowled
2. If complete → exit loop
3. Else sleep `remaining × 35s`, then go to 1
After loop exits (inn1 done), wait 5 min, then start 5-min inn2 poll loop as normal.  
No need to calibrate the 35s constant — loop self-corrects.

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

### M3 — Forecast threshold too low
**Files:** `engine/signals.py` → `FORECAST_THRESHOLD`  
**Problem:** Current threshold 0.55 may fire too readily.  
**Fix:** Change `FORECAST_THRESHOLD = 0.55` → `0.60`. Re-validate on historical matches.

---

## UX / display improvements

### U1 — Add cumulative score/wickets to Phase 3 table
**Files:** `polling/poller.py` → `_print_ball()`  
**Problem:** Table shows per-ball runs/extras/wickets but no running total — hard to follow match state.  
**Fix:** Track `cumulative_runs` and `wickets` in the output dict (already in `EngineOutput`) and add columns to the printed table.

---

### U2 — Display Win% and Hotness as percentages
**Files:** `polling/poller.py` → `_print_ball()`  
**Problem:** Values displayed as `0.384` — requires mental conversion.  
**Fix:** Format as `38.4%` and `46.1%`. Adjust column widths accordingly.

---

## Polling behaviour

### P1 — Stale warnings fire during strategic timeouts
**Files:** `polling/poller.py` → `_check_stale()`  
**Problem:** IPL has two 2.5-min strategic timeouts per innings. Stale warning fires during these expected pauses.  
**Fix:** Suppress stale warning for ~3 min after overs 6 and 10 (or make the warning threshold slightly higher — currently 3 min, could move to 5 min).

---

### P2 — Raw inn2 poll files accumulate (215 files per match)
**Files:** `polling/poller.py` → `_phase3_poll_inn2()`  
**Problem:** Every poll writes a new timestamped `raw_inn2_{HHMMSS}.json`. One match = ~215 files. Cricbuzz returns full commentary history each poll so old files are entirely redundant.  
**Fix:** Ping-pong buffer — alternate writes between `raw_inn2_a.json` and `raw_inn2_b.json`. One file is always intact if a crash corrupts the one being written. Delete all timestamped `raw_inn2_*.json` files after implementing. Keep `raw_inn1_*.json` as-is (written once, no accumulation).
