# Model Design & Methodology — Cricket Hot Match Detector

Reference doc covering all design decisions, iterations, and findings to date.
Load this to resume context without re-reading notebooks.

---

## Problem statement

Detect in real-time whether an ongoing T20 cricket match is becoming "hot" (exciting, worth tuning in to watch) and send a notification. The signal must be computed ball-by-ball from live data.

---

## Data source

**cricsheet.org** — ball-by-ball JSON for all matches. See `skills/fetch_ball_by_ball.md` for retrieval.

Training data: **1,184 IPL matches** (2008–2026), ~128,500 ball-states extracted from 2nd innings chases.

---

## Core insight: only the chase matters

Hotness is computed entirely from the **2nd innings (chase)**. The 1st innings score sets the target; what makes a match hot is whether that target is being closely contested ball by ball.

---

## Feature engineering

Per legal delivery in the chase, we compute:

| Feature | Formula | Notes |
|---|---|---|
| `runs_needed` | `target - score` | Raw |
| `balls_remaining` | `total_balls - legal_ball` | `total_balls` from inn1 actual balls, NOT `info.overs * 6` |
| `wickets_fallen` | cumulative | 0–10 |
| `rrr` | `runs_needed / (balls_remaining + 1)` | Required run rate per ball |
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
**Problem**: `120 * 1.45 = 174` — so any target above 174 starts near 0%, any target below 174 starts near 100%. Completely wrong for high targets like 210.

### v2: Tuned formula (used in nb01, nb02)
```python
avg_rate = 1.667  # 120 * 1.667 = 200 → 200 is 50/50
sensitivity = 20  # softer curve
```
**Rationale**: domain knowledge — 200 is roughly a 50/50 target in IPL. Sensitivity=20 gives:
- Chase 240 → ~17% start probability
- Chase 200 → ~50%
- Chase 160 → ~83%
- Chase 120 → ~95%

Better but still symmetric — doesn't capture that wickets matter more in death overs.

### v3: Empirical lookup (nb02)
Built from 1,184 IPL matches. For each `(runs_needed_bin, balls_remaining_bin, wickets_fallen)` state, computed observed win rate. Bin sizes: runs_needed //5, balls_remaining //6, min 10 samples per bin.

**Problem**: jagged/noisy due to sparse bins. Applied Savitzky-Golay smoothing (window=11, poly=2) per-match before use.

**Findings from comparison**:
- Empirical correctly gives ~35% to DC chasing 211 at ball 1 (formula gave 8%)
- Empirical correctly gives ~80% to Pakistan chasing 120 at ball 1 (formula gave 97%)
- Cold matches (MI winning by 100) correctly stay near 0% throughout

### v4: Neural Network (nb03 — current, saved to models/)
Trained to fit the empirical bin win rates as soft regression targets. Smooth by construction.

---

## NN Architecture

```
Input (6) → Linear(64) → ReLU → Dropout(0.1)
          → Linear(32) → ReLU → Dropout(0.1)
          → Linear(16) → ReLU → Dropout(0.1)
          → Linear(1)  → Sigmoid
```

- **Parameters**: 3,073
- **Loss**: MSE against empirical bin win rates (soft labels, not raw 0/1)
- **Optimiser**: Adam, lr=1e-3
- **Epochs**: 50, batch size 512
- **Train/val split**: 85/15
- **Input normalisation**: z-score per feature (mean/std saved in checkpoint)

**Why soft labels over binary BCE**: training on raw 0/1 outcomes would make the NN chase noisy game-level results. Training on empirical bin rates (which average over many similar situations) gives a smoother, better-calibrated target.

**Saved artifacts**:
- `models/win_prob_nn.pt` — weights + architecture config + normalisation stats
- `models/emp_lookup.pkl` — empirical lookup dict as fallback

**Loading the model**:
```python
import torch, torch.nn as nn
from pathlib import Path

class WinProbNet(nn.Module):
    def __init__(self, input_dim=6, hidden_dims=[64, 32, 16]):
        super().__init__()
        layers = []
        prev = input_dim
        for h in hidden_dims:
            layers += [nn.Linear(prev, h), nn.ReLU(), nn.Dropout(0.1)]
            prev = h
        layers += [nn.Linear(prev, 1), nn.Sigmoid()]
        self.net = nn.Sequential(*layers)
    def forward(self, x):
        return self.net(x).squeeze(-1)

ckpt = torch.load('models/win_prob_nn.pt', weights_only=False)
model = WinProbNet(input_dim=ckpt['input_dim'], hidden_dims=ckpt['hidden_dims'])
model.load_state_dict(ckpt['model_state_dict'])
model.eval()

FEATURE_COLS = ckpt['feature_cols']
X_mean, X_std = ckpt['X_mean'], ckpt['X_std']
```

**Running inference**:
```python
import numpy as np

def nn_win_prob(runs_needed, balls_remaining, wickets_fallen):
    rn, br, wk = map(lambda x: np.array(x, dtype=np.float32),
                     [runs_needed, balls_remaining, wickets_fallen])
    raw = np.stack([rn, br, wk, rn/(br+1), br/120, wk/10], axis=1)
    X   = torch.tensor((raw - X_mean) / X_std)
    with torch.no_grad():
        return model(X).numpy().clip(0.02, 0.98)
```

---

## Hotness score

```python
closeness = 1 - 2 * abs(win_prob - 0.5)   # peaks at 1 when win_prob = 0.5
momentum  = abs(win_prob.diff(6))           # abs change over last 6 balls

hotness = (closeness * 0.6 + momentum * 5 * 0.4).clip(0, 1)
```

**Weights**: 0.6 closeness, 0.4 momentum — closeness is primary signal, momentum catches swing without needing 50/50.

**Momentum scaling**: raw momentum values are small (0.05–0.15 typical). `* 5` brings into comparable range to closeness.

**Window**: 6 balls (1 over). Also compute 12-ball version for reference.

**Notification threshold**: 0.6, sustained for 6 consecutive balls (1 over). Avoids false triggers from single-ball spikes.

---

## Two types of "hot" match

Discovered from analysing DC vs GT vs IND vs PAK:

| Type | Mechanism | Example |
|---|---|---|
| **Knife-edge finish** | Win prob sustained near 50% in death overs | DC vs GT — last ball finish |
| **Collapse drama** | Rapid wicket cluster swings momentum | IND vs PAK — Bumrah 4/14 |

Both are captured: closeness catches knife-edge, momentum catches collapse. IND vs PAK scores lower on closeness (India always slightly ahead) but high on momentum after wicket clusters.

---

## Validation matches

| Match | Label | Outcome | Hotness behaviour |
|---|---|---|---|
| DC vs GT, IPL 2026-04-08 | HOT | GT won by 1 run | High hotness last 3 overs, onset over 19 |
| IND vs PAK, T20 WC 2024-06-09 | HOT | India won by 6 runs | Hotness driven by wicket momentum |
| RR vs MI, IPL 2026-04-07 | COLD | RR won by 27 (rain) | Never sustained above threshold |
| MI vs RR, IPL 2025-05-01 | COLD | MI won by 100 runs | Near-zero throughout |
| KKR vs LSG, IPL 2026-04-09 | NEW | LSG won by 3 wkts last ball | TBD — expected HOT |
| RCB vs RR, IPL 2026-04-10 | NEW | RR won by 6 wkts chasing 202 | TBD — expected moderate |

---

## Notebook map

| Notebook | Purpose |
|---|---|
| `01_match_analysis.ipynb` | Initial exploration, formula win prob, hotness on 4 matches |
| `02_empirical_win_prob.ipynb` | Empirical model from 1184 IPL matches, formula tuning |
| `03_nn_win_prob.ipynb` | NN training, comparison vs empirical, model save |
| `04_model_interpretability.ipynb` | Captum: integrated gradients, sensitivity, heatmaps, feature ablation |
| `06_hotness_nn.ipynb` | Final hotness pipeline using NN win prob, all 6 matches |

---

## Open questions / next steps

- **Notification pipeline**: polling live data, Telegram bot wiring
- **Live data source**: cricsheet is historical only — need a live API for real-time use (cricbuzz unofficial endpoints, or a paid API)
- **First innings hotness**: currently only 2nd innings is scored — a dominant 1st innings (e.g. 240) might itself be "interesting" even before the chase
- **Threshold calibration**: 0.6 sustained for 6 balls is untested on more matches — needs broader validation
- **KKR vs LSG / RCB vs RR**: run nb06 to validate NN on these new matches
