# Live Match Observations — CSK vs KKR, IPL 2026-04-14

First full end-to-end live run of the pipeline. Match: CSK 192/x in 20 overs, KKR chasing 193. KKR lost (collapsed from ~50/50 at over 9 to 6 wickets down by over 11).

---

## What worked well

- **Phase 2 smart wait** fired correctly — detected inn1 already complete (120 balls), polled inn2 immediately.
- **Ball parsing and adapter** — all legal deliveries parsed correctly, extras counted right (wides excluded, byes/legbyes included).
- **Win prob tracking** — climbed from 0.38 at ball 1 to 0.54 at over 8.6 (genuine 50/50 phase), then collapsed on back-to-back wickets at 9.3, 10.5, 10.6. Closely tracked Cricbuzz's own win prob.
- **Hotness peak at 0.912** (over 8.5) — correctly identified the most dramatic phase of the match.
- **Momentum spikes on wickets** — clearly visible in the table; hotness jumped on each wicket fall.
- **Resume on restart** — poller loaded 19 seen ball keys from `ball_events.jsonl`, didn't re-send old balls.
- **Stale data warnings** — fired correctly during strategic timeouts and over breaks (~3-4 min gaps at overs 7.6 and 12.5).

---

## Bugs and issues observed

### 1. Smart wait undershoots
**Observed:** Smart wait completed ~4 overs too early (inn1 still in progress).  
**Cause:** `_BALL_DURATION_SECS = 35` is too low — real pace including wides, no-balls, field settings, and over breaks is higher.  
**Fix planned:** Replace one-shot sleep with an iterative loop — re-fetch inn1 after each sleep, recompute remaining balls, sleep again. Loop until `balls_bowled >= 120` (or inn1 complete signal), then wait 5 min, then start Phase 3.

---

### 2. Momentum hard cliff at 6-ball window boundary
**Observed:** Hotness dropped sharply from 0.651 → 0.315 on a neutral dot ball at over 5.2 — exactly 6 balls after the over-1.4 wicket.  
**Cause:** Momentum = `|win_prob[now] - win_prob[-6]|`. As soon as the wicket ball leaves the 6-ball window, momentum resets hard.  
**Fix planned:** Smooth momentum — use exponential moving average or rolling mean over the window rather than a single point comparison. Gradual decay instead of cliff.

---

### 3. Engine restart loses all in-memory state
**Observed:** Engine was restarted mid-match. Poller correctly skipped 19 seen balls (resume worked), but engine had no history of them — `balls_faced` started from 0, win prob history empty, hotness deque empty.  
**Consequences:**
- Forecaster delayed: needed 12 fresh hotness values before producing output. With restart at ball ~20, forecaster appeared at ball ~73 instead of ball 60.
- `balls_faced` counter was ~19 short, so the 60-ball gate was hit ~19 balls late.
**Fix planned:** On engine restart detection (engine returns 404 / session not found), replay `ball_events.jsonl` through the engine before resuming live polling. Rebuilds `balls_faced`, win prob history, hotness deque correctly.

---

### 4. Innings end condition missing (loss by runs)
**Observed:** Match ended at over 19.6 (KKR fell short, innings complete, wickets still in hand). Poller had no end condition for this — would have kept polling and eventually hit the stale data warning.  
**Cause:** End condition only checks `wickets >= 10` or `runs_needed == 0`. Doesn't detect innings completion at ball 120.  
**Fix planned:** Add end condition: `balls_remaining == 0` (or `balls_faced >= total_balls`) → innings over, batting team lost.

---

### 5. Win prob overconfident in dead-game scenarios
**Observed:** Win% stayed at 0.05–0.07 even at 6 wickets down needing 80+ off remaining balls — should be near 0.01–0.02. Cricbuzz showed 93% for the fielding side.  
**Cause:** Likely underrepresentation in training data of matches that reach 6-7 wickets down in a losing chase. Also likely symmetric — model probably stays too pessimistic when chasing team is cruising.  
**Fix planned:** Calibration pass — temperature scaling or output clipping at extremes. Retraining with better representation of late-wicket situations.

---

### 6. Stale warnings fire during strategic timeouts
**Observed:** Stale data warnings fired during the ~3-4 min gaps at strategic timeouts (after overs 6 and 10 in IPL).  
**These are expected pauses, not real outages.**  
**Fix planned:** Add awareness of strategic timeout windows — suppress stale warning for ~3 min after overs 6 and 10.

---

## Cosmetic / UX improvements noted

| # | Change | Note |
|---|---|---|
| 1 | Add cumulative score/wickets column to Phase 3 table | Hard to follow match state without it |
| 2 | Display Win% and Hotness as percentages (e.g. `38.4%`) | Easier to read than `0.384` |
| 3 | Change forecast threshold from 0.55 → 0.60 | Current threshold fires too readily |

---

## Forecaster behaviour

- Requires 12 hotness values in deque before producing output (by design).
- In a clean run, this is satisfied well before ball 60 — gate is the binding constraint.
- After engine restart mid-match, deque is empty — forecaster delayed until 12 fresh values accumulate. Fix is the engine state replay (bug #3 above).
- Forecast values in this match were low (0.07–0.25) throughout — correct, since the match was effectively decided by over 11.
