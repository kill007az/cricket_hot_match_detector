# tests/

Simulation and integration tests for the engine + API.

---

## `simulate_hot_match.py`

Replays a full match from a cricsheet JSON file against the live API, simulating what a real polling backend would send ball by ball.

### What it tests

| Concern | How |
|---|---|
| End-to-end pipeline | All 120 balls of KKR vs LSG 2026-04-09 processed via HTTP |
| Signal correctness | Pre-match signal expected at ball 1; in-game signal expected ~ball 90 |
| Idempotency / overlap | 3 random balls re-sent; all must return `is_duplicate: true` |
| Latency | Client round-trip and server-side per-step breakdown printed at end |

### Running

The API server must be running first:

```bash
# Terminal 1 — start server
conda activate cricket_hot
cd cricket_hot_match_detector
uvicorn engine.server:app --reload --port 8000

# Terminal 2 — run simulation
conda activate cricket_hot
cd cricket_hot_match_detector
python -m tests.simulate_hot_match
```

### Expected output

```
Match:        kkr_vs_lsg_2026-04-09
Inn1 runs:    181  →  Target: 182
Inn1 balls:   120
Outcome:      {'winner': 'Lucknow Super Giants', 'by': {'wickets': 3}}

[INIT]  Match session initialised.

Inn2 legal deliveries: 120
──────────────────────────────────────────────────────────────────────
Processing balls: 100%|████████████████████| 120/120 [00:03<00:00, 38.2ball/s]
  *** SIGNAL at ball   1 (over 0.1): 50/50 chase — worth watching from the start
  *** SIGNAL at ball  90 (over 14.6): match heating up — tune in now

OVERLAP TEST — re-sending 3 already-processed balls:
  over 4.2  →  is_duplicate=True ✓
  over 9.5  →  is_duplicate=True ✓
  over 16.1 →  is_duplicate=True ✓

FINAL STATE:
  balls_faced:    120
  runs_scored:    182
  wickets:          7
  runs_remaining:   0

SIGNALS FIRED (2 total):
  ball   1  over 0.1  →  50/50 chase — worth watching from the start
  ball  90  over 14.6 →  match heating up — tune in now

HOTNESS SNAPSHOT (every 10 balls):
 Ball    Win%   Hotness  Forecast  Signals
   10   0.471     0.862        —
   20   0.503     0.795        —
   ...
   90   0.612     0.821     0.831  match heating up — tune in now
  ...
  120   0.012     0.421     0.199

LATENCY STATS (ms)
                       min     p50     p95     p99     max    mean
  Client (round-trip)  1.20    2.10    3.40    5.20    8.10    2.18
  Server (engine only) 0.30    0.85    1.20    1.80    3.50    0.91
  HTTP overhead (est.)                                         1.27

PIPELINE BREAKDOWN (mean ms/ball):
  Step                mean ms   share  bar
  win_prob             0.4200   47.2%  ███████████████
  forecast             0.2800   31.5%  ██████████
  hotness              0.0500    5.6%  █
  state_update         0.0300    3.4%  █
  feature_extract      0.0200    2.2%
  signals              0.0100    1.1%
  ──────────────────  ────────
  TOTAL (engine)       0.8900
  HTTP overhead        1.2700

  BOTTLENECK → win_prob (47.2% of engine time)

Done.
```

Exact timings will vary by machine. Signal timings (ball 1 and ~ball 90) should be consistent.

### Configuration

At the top of `simulate_hot_match.py`:

```python
BASE_URL      = "http://localhost:8000"   # change if running API elsewhere
DATA_FILE     = "data/raw/kkr_vs_lsg_2026-04-09.json"
MATCH_ID      = "kkr_vs_lsg_2026-04-09"
DELAY_SECONDS = 0.0    # set > 0 to simulate real-time polling pace (e.g. 3.6 = 1 ball/3.6s ≈ match pace)
```

---

## Adding a new match simulation

1. Place the cricsheet JSON in `data/raw/`
2. Copy `simulate_hot_match.py` or parameterise `DATA_FILE` and `MATCH_ID`
3. Set your expected signals — for a COLD match, you should see zero in-game signals

Cold match reference files already in `data/raw/`:
- `mi_vs_rr_2025-05-01.json` — MI won by 100 runs (expected: no signals)
- `rr_vs_mi_2026-04-07.json` — rain-reduced, RR won by 27 (expected: no signals)

---

## Cricsheet parsing notes

The simulation script handles two important cricsheet quirks:

**Legal ball filtering.** The cricsheet JSON includes all deliveries (including wides and no-balls) in `overs[].deliveries`. The script filters these out before sending to the engine:

```python
def is_legal(delivery):
    extras = delivery.get("extras", {})
    return "wides" not in extras and "noballs" not in extras
```

**`over` float encoding.** The engine uses `over.delivery` float notation (e.g. `14.3`). The script derives this by counting legal deliveries within each over:

```python
over_num = over_obj["over"]   # 0-indexed integer from cricsheet
legal_idx += 1                 # increments only for legal balls
over_float = round(over_num + legal_idx / 10, 1)   # e.g. 14.3
```

This means over 14, 3rd legal ball → `14.3`, regardless of how many wides or no-balls occurred in that over.
