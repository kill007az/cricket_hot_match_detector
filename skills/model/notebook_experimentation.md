# Notebook & Experimentation Guide

How to create a new experiment notebook in this repo. Follow these conventions so all notebooks stay consistent and results are reproducible.

---

## Notebook numbering

| NB | Topic |
|---|---|
| 01 | Initial exploration, formula win prob, hotness on 4 matches |
| 02 | Empirical win prob from 1,159 IPL matches |
| 03 | NN win prob — MSE on soft empirical targets |
| 04 | Model interpretability (Captum: integrated gradients, feature ablation) |
| 05 | DLS comparison |
| 06 | Hotness pipeline on 6 validation matches |
| 07 | Autoregressive hotness forecaster |
| 08 | Hotness formula tuning + win prob gate (CSK vs KKR false positive diagnosis) |
| 09 | BCE vs MSE win prob experiment (M2 tail calibration) |

New notebooks get the next number. Name them `{nn}_{short_description}.ipynb`.

---

## Standard header cell

Every notebook starts with a markdown cell explaining:
- What problem it's solving (backlog ID if applicable)
- What the approach is and why
- Success criteria — how you'll know the experiment worked

```markdown
# NB0N — Short title

**Problem (BXX/MXX):** One-line description of the issue.

**Approach:** What this notebook does differently and why.

**Success criteria:**
- Specific measurable outcome 1
- Specific measurable outcome 2
```

---

## Standard imports cell

```python
import json
import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset, random_split

plt.rcParams['figure.figsize'] = (14, 5)
plt.rcParams['axes.grid'] = True
plt.rcParams['grid.alpha'] = 0.3

torch.manual_seed(42)
np.random.seed(42)

IPL_DIR = r'C:\Users\hp\AppData\Local\Temp\cricket_data\ipl_all'
DATA    = '../data/raw/'
MODELS_DIR = Path('../models')

FIG_DIR = Path('../data/figures/{nn}_{name}')
FIG_DIR.mkdir(parents=True, exist_ok=True)
```

Always set `torch.manual_seed(42)` and `np.random.seed(42)` for reproducibility.

---

## Data loading

### IPL raw Cricsheet JSONs
Use this parser pattern (same as NB03). **Always infer `total_balls` from inn1 legal deliveries** — never use `info.overs`, which is wrong for rain-reduced matches.

```python
def parse_chase_states(match_path):
    with open(match_path) as f:
        d = json.load(f)
    if len(d['innings']) < 2:
        return []
    outcome = d['info'].get('outcome', {})
    if 'winner' not in outcome:
        return []
    ...
```

### Validation match files (`data/raw/`)

| File | Label | Notes |
|---|---|---|
| `dc_vs_gt_2026-04-08.json` | HOT | GT won by 1 run, last ball finish |
| `ind_vs_pak_2024-06-09.json` | HOT | India won by 6 runs, Bumrah 4/14 collapse |
| `rr_vs_mi_2026-04-07.json` | COLD | Rain-reduced, RR won by 27 |
| `mi_vs_rr_2025-05-01.json` | COLD | MI won by 100 runs — blowout |
| `kkr_vs_lsg_2026-04-09.json` | HOT | LSG won by 3 wkts, last ball |
| `rcb_vs_rr_2026-04-10.json` | MODERATE | RR won by 6 wkts chasing 202 |

Always wrap `build_chase(path)` in a `try/except FileNotFoundError` — some files may not exist locally.

---

## Feature engineering

6 features used by both the win prob model and comparisons. Always derive them the same way:

```python
FEATURE_COLS = ['runs_needed', 'balls_remaining', 'wickets_fallen',
                'rrr', 'balls_fraction', 'wickets_fraction']

df['rrr']              = df['runs_needed'] / (df['balls_remaining'] + 1)
df['balls_fraction']   = df['balls_remaining'] / df['total_balls']   # NOT /120 in notebooks
df['wickets_fraction'] = df['wickets_fallen'] / 10.0
```

**Note:** The production engine hardcodes `balls_fraction = balls_remaining / 120` (both models were trained this way). In notebooks, use actual `total_balls` for more accurate calibration analysis. This discrepancy is known — see `working_context.md` sprint 1 design decisions.

---

## Model architecture

Standard win prob / forecaster pattern:

```python
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
```

Standard training loop: Adam lr=1e-3, 50 epochs, batch 512, 85/15 split.

---

## Checkpoint format

All saved checkpoints use these keys. **Do not add new top-level keys without noting them in `model_design_3.md`.**

```python
ckpt = {
    'input_dim':        int,           # number of input features
    'hidden_dims':      list[int],     # hidden layer sizes
    'model_state_dict': OrderedDict,   # torch state dict
    'feature_cols':     list[str],     # feature names in order
    'X_mean':           np.ndarray,    # normalisation mean
    'X_std':            np.ndarray,    # normalisation std
    # optional: 'training': str        # short description e.g. "BCE NB09"
}
torch.save(ckpt, MODELS_DIR / 'model_name.pt')
```

For experiment variants, **save to a different filename** (e.g. `win_prob_nn_bce.pt`) and gate the save behind a `SAVE_MODEL = False` flag. Only overwrite `win_prob_nn.pt` (the production checkpoint) after manually confirming results.

---

## Figures

Save every plot:
```python
plt.savefig(FIG_DIR / 'descriptive_name.png', dpi=150, bbox_inches='tight')
```

Use `bbox_inches='tight'` for multi-subplot figures. Standard figure sizes:
- Single plot: `(14, 5)`
- 2×2 grid: `(16, 10)`
- Reliability / calibration: `(14, 5)`

---

## Evaluation pattern

Every model experiment should include:

1. **Tail probe** — manually query the model at known extreme states and compare to empirical rate from raw data
2. **Win prob curves** — overlay new vs baseline on all 6 validation matches
3. **Reliability diagram** — binned predicted vs actual win rate (calibration plot)
4. **Signal impact** — does this change when/whether the signal fires on HOT vs COLD matches?

For signal impact, the relevant thresholds are in `engine/signals.py`:
- Win prob gate: `0.25 < wp < 0.75`
- Forecast threshold: `FORECAST_THRESHOLD = 0.60`
- Pre-match gate: `0.40 ≤ wp ≤ 0.60` at ball 1

---

## Comparing against the baseline

Always load the production checkpoint for side-by-side comparison:
```python
ckpt = torch.load(MODELS_DIR / 'win_prob_nn.pt', weights_only=False)
model_baseline = WinProbNet(ckpt['input_dim'], ckpt['hidden_dims'])
model_baseline.load_state_dict(ckpt['model_state_dict'])
model_baseline.eval()
mse_mean, mse_std = ckpt['X_mean'], ckpt['X_std']
```

Keep separate normalisation stats per model — they may differ if trained on different feature distributions.

---

## Updating model_design_3.md

After any experiment that changes a model or formula, update `skills/model/model_design_3.md`:
- Add a new version entry under the relevant section (e.g. "v5: BCE NN")
- Record final train/val loss, key hyperparameters, and what changed
- Update the "Open questions / next steps" section
