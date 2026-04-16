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

### M4 — Momentum over-weighted in hotness formula ✅
**Files:** `engine/hotness.py`, `notebooks/08_hotness_formula_tuning.ipynb`  
**Problem:** Momentum multiplier (`× 5`) amplifies brief batting blitzes in lopsided matches to the same degree as genuine comebacks. In CSK vs KKR 2026-04-16 (KKR lost by 38 runs), a cluster of sixes in overs 8-9 pushed hotness to ~55% and forecast to 63% despite win prob being only ~20%. Signal fired incorrectly.  
**Root cause:** `momentum * 5 * 0.4` contributes up to 0.26 hotness from a 0.13 win prob swing regardless of closeness. At win_prob=0.20, closeness=0.39 — the match isn't close, but momentum drowns it out.  
**Fix options (to be evaluated in NB08 on all 6 validation matches):**
1. Reduce the momentum multiplier (e.g. `× 3` or `× 2`)
2. Weight momentum by closeness — `momentum × closeness` so momentum only matters when the match is close
3. Replace point comparison with EMA (overlaps M1) for smoother decay
4. Add a win prob gate in `signals.py` — don't fire if `win_prob < 0.25 or win_prob > 0.75`  
**Decision (NB08):** All formula variants score 100% on 7 matches. Reducing the multiplier (×3/×2) delays KKR vs LSG signal by ~9 overs — unacceptable. Root cause was the **forecaster** amplifying a pre-gate momentum spike, not raw hotness. **Fix applied:** win prob gate `0.25–0.75` added to `engine/signals.py` — forecaster output suppressed when match is clearly one-sided. No formula change, no retraining needed.  
**Success criteria:** CSK vs KKR 2026-04-16 fires no signal; all 3 HOT matches still fire.

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
**Files:** `polling/poller.py` → `_phase3_poll_inn2()`, `_detect_super_over()`  
**Fix:** Commentary text scanned for `"super over"` keyword; `_super_over` flag set when detected. End condition gates updated — `balls_remaining == 0` ignored during super over; ends on `runs_needed == 0`, `wickets >= 2`, or `super_over_balls >= 12`. Loops for repeated super overs.

---

### P4 — Post-match Cricsheet data collection
**Files:** New script `scripts/fetch_cricsheet.py`  
**Problem:** Cricsheet JSON for a match becomes available the day after. No automated way to fetch and store in `data/raw/` for retraining/validation.  
**Fix:** Script accepts match date + team names, queries Cricsheet download endpoint, saves to `data/raw/`. See `skills/data/fetch_ball_by_ball.md` for Cricsheet API details.

---

### D1 — Decouple training data pipeline from recent match data (IMPORTANT)
**Files:** `data/raw/`, `data/live_polls/`, new `scripts/convert_poll_to_cricsheet.py`  
**Problem:** Two separate data realities exist and are currently conflated:

1. **Cricsheet-style data** (`data/raw/*.json`) — standardised historical JSONs used for model training. Lagged by ~1 week after a match. Required format for NB01–NB08 and any retraining.
2. **Live poll data** (`data/live_polls/{match_id}/`) — `ball_events.jsonl` + `engine_outputs.jsonl` written in real time. Available immediately after the match ends. Not in Cricsheet format.

This means recent matches (within ~1 week) cannot be used for validation or retraining even though we have full ball-by-ball data from our own poller. NB08 (hotness tuning) cannot include CSK vs KKR 2026-04-16 until cricsheet publishes it, despite having polled every ball ourselves.

**Fix — two parallel tracks:**

**Track A: Poll-to-Cricsheet converter** (`scripts/convert_poll_to_cricsheet.py`)  
Convert `ball_events.jsonl` + inn1 raw JSON into a Cricsheet-compatible JSON structure.  
Allows immediate use of any polled match in notebooks without waiting for cricsheet.  
Output saved to `data/raw/` using the same naming convention.  

**Track B: Cricsheet auto-fetch** (`scripts/fetch_cricsheet.py`, from P4)  
Once cricsheet publishes the match (~1 week), fetch the authoritative version and replace the converted file. Authoritative source preferred for long-term training data integrity.

**Success criteria:**  
- A match polled today can be loaded in NB08 the same day via the converter  
- Once cricsheet publishes, the authoritative file replaces the converted one transparently  
- Notebooks and training scripts do not need to know which source a file came from

---

### P5 — Auto-discovery of live Cricbuzz match ID (HIGH PRIORITY)
**Files:** `polling/cricbuzz_client.py` → `find_live_match()`, `polling/poller.py` → `_phase1_find_match()`, `polling/run_live.py`, `run.py`  
**Problem:** The old Cricbuzz live-listing endpoint (`/api/cricket-match/live-matches`) returned 404 in April 2026. Auto-discovery was disabled; `--cb-id` is now mandatory. Users must manually find the numeric match ID from the Cricbuzz URL each time.  
**Fix:** Capture a new HAR from `cricbuzz.com/cricket-match/live` during a live match to find the current live-listing endpoint. Once found:
1. Update `find_live_match()` in `cricbuzz_client.py` with the new URL
2. Restore auto-discovery loop in `_phase1_find_match()` — poll until target teams appear
3. Make `--cb-id` optional again (falls back to auto-discovery if omitted)
4. Update `skills/live/cricbuzz_api_endpoints.md` with the new endpoint  
**How to find it:** Open DevTools on `cricbuzz.com/cricket-match/live` during a live match, filter XHR, look for a JSON response listing multiple live matches. Follow HAR capture steps in `skills/live/cricbuzz_api_endpoints.md`.

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
