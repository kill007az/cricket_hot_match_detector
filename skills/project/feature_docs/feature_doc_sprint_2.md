# 🏏 Cricket Hot Match Engine — Feature Specification (sprint 2)

Sprint 2 covers the live polling service, first live run observations, and all backlog fixes implemented
after the CSK vs KKR live run on 2026-04-14.

---

## 1. 🎯 Objective

Ship a complete live data polling service that fetches ball-by-ball data from the Cricbuzz unofficial JSON
API, POSTs it to the engine, and handles real-world edge cases: innings transitions, timeouts, super overs,
file accumulation, and stale detection.

---

## 2. 🧩 Scope

### Included

* CricbuzzClient — rate-limited API client with retry + exponential backoff
* LivePoller — 3-phase polling loop
* Adapter — Cricbuzz items → BallEvent dicts
* Docker Compose deployment (engine + poller + orchestrator + UI)
* Backlog fixes: B2, B3, M3, U1, U2, P1, P2, P3

### Excluded

* Auto-discovery of live match ID (P5 — requires HAR capture during live match)
* Engine restart state replay (B1)
* Momentum smoothing / hotness formula tuning (M1, M4 — pending NB08)
* Win prob calibration (M2)

---

## 3. 🏗️ LivePoller Architecture

```
Phase 1: find_live_match()
   ↓  match appears in Cricbuzz live listings
Phase 2: smart_wait_for_inn1_complete()  +  poll_until_inn2_starts()
   ↓  inn1 complete + inn2 first ball detected
Phase 2.5: fetch inn1 summary → POST /match/init
   ↓
Phase 3: continuous inn2 polling loop
   ↓  new legal balls → POST /match/{id}/ball
   ↓  end condition met
Match over
```

---

## 4. ✅ B2 — Innings End Condition: Loss by Runs

### Problem
`_phase3_poll_inn2()` only ended the innings on `runs_needed == 0` or `wickets >= 10`.
If the batting team ran out of balls without winning, the loop would hang.

### Fix
Added `balls_remaining == 0` (when not in super over) as a third end condition.

### Requirements (Gherkin)

```
Feature: Innings end condition

Scenario: Loss by runs
  Given balls_remaining reaches 0
  And runs_needed > 0
  And super_over is False
  When the end condition is evaluated
  Then the match must be marked as over
```

```
Scenario: Mid-innings not ended prematurely
  Given balls_remaining > 0
  And runs_needed > 0
  And wickets < 10
  When the end condition is evaluated
  Then the match must not be marked as over
```

```
Scenario: Win by runs
  Given runs_needed reaches 0
  When the end condition is evaluated
  Then the match must be marked as over
  Regardless of balls_remaining or wickets
```

---

## 5. ✅ B3 — Smart Wait: Iterative Inn1 Completion

### Problem
Phase 2 smart wait computed `remaining_balls × 35s` once and slept that duration.
If inn1 ended early (wickets) or the estimate was wrong, the wait undershot and
inn2 polling started before inn1 was complete.

### Fix
Iterative loop: fetch inn1, count legal balls, compute remaining, sleep, repeat until
`bowled >= 120` (or actual total_balls). Self-corrects on each undershoot.

### Requirements (Gherkin)

```
Feature: Iterative smart wait

Scenario: Inn1 already complete on first check
  Given inn1 legal balls >= 120
  When _smart_wait_for_inn1_complete is called
  Then no sleep must occur
  And exactly one inn1 fetch must be made
```

```
Scenario: Inn1 undershoots and self-corrects
  Given first inn1 fetch returns 60 balls remaining
  And second fetch returns 0 balls remaining
  When _smart_wait_for_inn1_complete is called
  Then exactly two inn1 fetches must be made
  And sleep must be called once between fetches
```

---

## 6. ✅ M3 — Forecast Threshold 0.55 → 0.60

### Problem
`FORECAST_THRESHOLD = 0.55` triggered too many false positives on moderately tense matches.

### Fix
Raised threshold to `0.60` in `engine/signals.py`.

### Requirements (Gherkin)

```
Feature: Forecast threshold

Scenario: Signal fires at threshold
  Given forecast = 0.61
  And balls_faced >= 60
  When signals are evaluated
  Then "match heating up" signal must be in output
```

```
Scenario: Signal does not fire below threshold
  Given forecast = 0.59
  And balls_faced >= 60
  When signals are evaluated
  Then signals list must be empty
```

---

## 7. ✅ U1 — Cumulative Score Column in Phase 3 Table

### Problem
The Phase 3 per-ball print table had no cumulative score — hard to read without context.

### Fix
Added `{wickets}w/{runs_needed}rr` score column to `_print_ball()`.

### Requirements (Gherkin)

```
Feature: Score column in ball table

Scenario: Score column present
  Given a ball with wickets=3 and runs_needed=80
  When _print_ball is called
  Then the output must contain "3w/"
  And the output must contain "rr"
```

---

## 8. ✅ U2 — Win% and Hotness as Percentages

### Problem
Win prob and hotness were printed as raw floats (e.g. `0.384`), not percentages.

### Fix
Formatted as `38.4%` and `46.1%` in `_print_ball()`. Forecast also shown as `65.0%` or `—` if None.

### Requirements (Gherkin)

```
Feature: Percentage formatting

Scenario: Win prob formatted as percentage
  Given win_prob = 0.384
  When _print_ball is called
  Then output must contain "38.4%"
```

```
Scenario: Hotness formatted as percentage
  Given hotness = 0.912
  When _print_ball is called
  Then output must contain "91.2%"
```

```
Scenario: Forecast formatted when present
  Given forecast = 0.65
  When _print_ball is called
  Then output must contain "65.0%"
```

```
Scenario: Forecast shown as dash when absent
  Given forecast = None
  When _print_ball is called
  Then output must contain "—"
```

---

## 9. ✅ P1 — Strategic Timeout Detection

### Problem
During DRS reviews and strategic timeouts, no new balls arrive. The stale-ball warning fired
incorrectly, creating noise.

### Fix
On no-new-balls poll, scan the first 10 commentary items for timeout keywords in `event` or
`commText` fields. If found, sleep 150 seconds and skip stale check.

Keywords: `"strategic timeout"`, `"timeout"`, `"strategic break"`.

### Requirements (Gherkin)

```
Feature: Strategic timeout detection

Scenario: Timeout keyword in event field
  Given commentary item with event = "strategic timeout"
  When _detect_timeout is called
  Then True must be returned
```

```
Scenario: Timeout keyword in commText field
  Given commentary item with commText = "Strategic Timeout called by batting side"
  When _detect_timeout is called
  Then True must be returned
```

```
Scenario: Strategic break keyword
  Given commentary item with commText = "strategic break in play"
  When _detect_timeout is called
  Then True must be returned
```

```
Scenario: No false positive on normal commentary
  Given commentary item with event = "FOUR" and commText = "Driven through covers"
  When _detect_timeout is called
  Then False must be returned
```

```
Scenario: Only first 10 items checked
  Given a list of 11 items where only item at index 10 contains timeout keyword
  When _detect_timeout is called
  Then False must be returned
```

---

## 10. ✅ P2 — Ping-Pong Buffer for Raw Inn2 Files

### Problem
Each inn2 poll wrote a new timestamped file. After 120 balls at 30s intervals, this
produced ~215 files per match — unmanageable.

### Fix
Alternate writes between `raw_inn2_a.json` and `raw_inn2_b.json`. One file is always
intact if a crash corrupts the one being written.

### Requirements (Gherkin)

```
Feature: Ping-pong buffer

Scenario: Index alternates on each poll
  Given _raw_inn2_idx starts at 0
  When polled 4 times
  Then indices used must be [0, 1, 0, 1]
```

```
Scenario: Two paths initialized
  Given a LivePoller instance
  Then _raw_inn2_paths must contain exactly two entries
  And paths must be named raw_inn2_a.json and raw_inn2_b.json
```

---

## 11. ✅ P3 — Super Over Detection and Looping

### Problem
Super overs were not detected. The engine's `balls_remaining == 0` end condition
fired when a tie resulted in a super over, ending polling prematurely.

### Fix
- Commentary scanning: detect `"super over"` keyword in event or commText
- `_super_over` flag set when detected
- End conditions updated: `balls_remaining == 0` ignored during super over
- Super over ends on: `runs_needed == 0`, `wickets >= 2`, or `_super_over_balls >= 12`
- Loop continues for repeated ties (multiple super overs)

### Requirements (Gherkin)

```
Feature: Super over detection

Scenario: Super over keyword in event field
  Given commentary item with event = "super over"
  When _detect_super_over is called
  Then True must be returned
```

```
Scenario: Super over keyword in commText
  Given commentary item with commText = "It's a Super Over!"
  When _detect_super_over is called
  Then True must be returned
```

```
Feature: Super over end conditions

Scenario: Normal innings ends on balls_remaining zero
  Given super_over is False
  And balls_remaining = 0
  When end condition is evaluated
  Then match must be marked over
```

```
Scenario: Super over ignores balls_remaining zero
  Given super_over is True
  And balls_remaining = 0
  And wickets = 1
  And super_over_balls = 6
  When end condition is evaluated
  Then match must NOT be marked over
```

```
Scenario: Super over ends on 2 wickets
  Given super_over is True
  And wickets = 2
  When end condition is evaluated
  Then match must be marked over
```

```
Scenario: Super over ends on 12 balls safety net
  Given super_over is True
  And super_over_balls = 12
  When end condition is evaluated
  Then match must be marked over
```

```
Scenario: runs_needed zero always ends match
  Given runs_needed = 0
  When end condition is evaluated
  Then match must be marked over
  Regardless of super_over flag
```
