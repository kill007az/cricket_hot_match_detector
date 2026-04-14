# Cricket Hot Match Detector

A real-time engine that watches a T20 cricket chase ball by ball and fires a notification when a match is about to become exciting — before the drama peaks.

---

## What it does

Two signals are generated per match:

| Signal | When | Logic |
|---|---|---|
| **Pre-match** | Ball 1 of the chase | Win probability at start is between 40–60% — structurally competitive target |
| **In-game** | After over 10 only | Hotness forecaster predicts peak drama in the next 6 balls |

The system is tuned for **recall over precision** — missing an exciting match is worse than a false alert.

---

## How it works

```
Ball arrives (live feed)
  → ChaseState updated
  → 6 features extracted
  → Win probability (NN) computed
  → Hotness score computed  (closeness + momentum)
  → Hotness forecaster (NN) run if ball >= 60
  → Signals evaluated
  → EngineOutput emitted
```

Three trained artifacts power the pipeline:

| Model | Purpose | Params |
|---|---|---|
| `models/win_prob_nn.pt` | Win probability per ball | 3,073 |
| `models/hotness_forecaster.pt` | Predict hotness in next 6 balls | 3,009 |
| `models/emp_lookup.pkl` | Empirical fallback lookup | — |

Both NNs were trained on **1,159 IPL matches (2008–2026)** from cricsheet.org.

---

## Project structure

```
cricket_hot_match_detector/
├── engine/                   # Core detection engine (pure Python, no HTTP)
│   ├── models.py             # Data structures: BallEvent, ChaseState, EngineOutput, …
│   ├── state.py              # Per-ball state update
│   ├── features.py           # Feature extraction for the win-prob NN
│   ├── win_prob.py           # Win probability NN inference
│   ├── hotness.py            # Hotness score formula
│   ├── forecaster.py         # Hotness forecaster NN inference
│   ├── signals.py            # Signal evaluation (pre-match + in-game)
│   └── orchestrator.py       # Pipeline coordinator, in-memory sessions
│
├── api/                      # FastAPI HTTP layer
│   ├── main.py               # App entry point, lifespan model loading
│   └── routes.py             # POST /match/init, POST /match/{id}/ball, GET /match/{id}/state
│
├── tests/
│   └── simulate_hot_match.py # Full match replay simulation (KKR vs LSG 2026)
│
├── models/                   # Saved model checkpoints (binary, not re-trained here)
├── notebooks/                # Exploratory analysis notebooks (NB01–NB07)
├── data/raw/                 # Cricsheet match JSONs used for validation
├── skills/                   # Session context docs (model design, analysis evolution)
├── feature_docs/             # Sprint feature specifications
│
├── requirements.txt
├── working_context.md        # Running log of decisions and state — read before resuming work
└── README.md
```

---

## Prerequisites

- Python 3.10+
- Conda environment `cricket_hot` with PyTorch and NumPy
- FastAPI + uvicorn (installed below)

---

## Installation

```bash
# Clone and enter repo
git clone <repo-url>
cd cricket_hot_match_detector

# Install API dependencies into the existing conda env
conda activate cricket_hot
pip install -r requirements.txt
```

---

## Running

### 1 — Start the API server

```bash
conda activate cricket_hot
cd cricket_hot_match_detector
uvicorn api.main:app --reload --port 8000
```

You should see:
```
INFO:     Application startup complete.
```

Both models are loaded at startup. The server stays running — leave this terminal open.

### 2 — Run the simulation (new terminal)

```bash
conda activate cricket_hot
cd cricket_hot_match_detector
python -m tests.simulate_hot_match
```

This replays KKR vs LSG (2026-04-09) ball by ball against the live API, including:
- Signal detection with ball number and over
- Overlap / idempotency test (3 balls re-sent)
- Latency breakdown identifying the pipeline bottleneck

---

## API quick reference

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/match/init` | Initialise a match with innings 1 summary |
| `POST` | `/match/{id}/ball` | Process one legal delivery |
| `GET` | `/match/{id}/state` | Current chase state + last output |
| `GET` | `/debug/latency` | Per-step mean latency breakdown |

Full docs at `http://localhost:8000/docs` when the server is running.

See [api/README.md](api/README.md) for request/response examples.

---

## Key design decisions

**BallEvent = legal deliveries only.**
Wides and no-balls are not sent. The backend filters them; only legal balls are POSTed. See [engine/README.md](engine/README.md) for why.

**Idempotent ball processing.**
Re-sending the same delivery (same `over.delivery` float) returns `is_duplicate: true` without mutating state. Safe to retry on network failures.

**60-ball gate on in-game signals.**
No in-game notifications before over 10. Suppresses false positives from structurally close targets (e.g. target 182 ≈ 50% win prob from ball 1).

**`balls_fraction` hardcoded to `/120`.**
Both NNs were trained with this convention. Using `/total_balls` would mis-calibrate the models even though it looks more correct mathematically.

---

## Validation results (from NB07)

| Match | Label | In-game signal | Lead time |
|---|---|---|---|
| DC vs GT, IPL 2026-04-08 | HOT | ~ball 60 | Full second half |
| IND vs PAK, T20 WC 2024-06-09 | HOT | ball 79 | 7 balls ahead of Bumrah spell |
| KKR vs LSG, IPL 2026-04-09 | HOT | ~ball 90 | 24 balls before last-over drama |
| RCB vs RR, IPL 2026-04-10 | COLD blowout | Never fires | Correct |
| RR vs MI, IPL 2026-04-07 | COLD (rain) | Never fires | Correct |
| MI vs RR, IPL 2025-05-01 | COLD | Never fires | Correct |

---

## Known limitations

- Model trained on IPL data only — may mis-calibrate for other leagues
- Sessions are in-memory; restarting the server loses all active match state
- Forecast threshold (0.55) is exploratory, not formally calibrated
- No authentication on the API — do not expose publicly without adding auth
- DLS (rain-reduced) matches are slightly mis-calibrated (see `balls_fraction` note above)

---

## Further reading

- [engine/README.md](engine/README.md) — pipeline internals, data structures, model details
- [api/README.md](api/README.md) — full endpoint reference with request/response examples
- [tests/README.md](tests/README.md) — simulation guide and how to add new matches
- [working_context.md](working_context.md) — running log of sprint decisions
- [skills/analysis_evolution.md](skills/analysis_evolution.md) — how the model evolved across NB01–NB07
- [skills/model_design_3.md](skills/model_design_3.md) — current authoritative model design reference
