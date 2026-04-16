# Win Probability Model — BCE Retraining (M2 Fix)

Reference for the BCE win prob model introduced in NB09. Load this to understand why the model changed and how to work with it.

---

## Problem it solves

The NB03 model (MSE on soft targets) was overconfident at extreme tail states:
- 6 wickets down, 80+ runs needed off 18 balls → model said ~0.05–0.07
- True historical win rate in that state: ~0.01–0.02

Root cause: NB03 binned ball-states and trained on bin averages. Sparse tail bins get smoothed toward neighbours, pulling the target up from 0% to ~5%. The model faithfully learns "predict 5% here."

---

## What changed

### Loss function
| | NB03 | NB09 |
|---|---|---|
| Loss | `MSELoss` | `BCEWithLogitsLoss` |
| Target | Smoothed empirical bin average | Raw `chaser_won` (0 or 1) |
| Binning | Yes (5-run × 6-ball × wicket bins, n≥10) | No |

BCE learns the true win frequency directly from outcomes. At a state with 50 losses and 0 wins, BCE pushes the prediction toward 0 — no bin average to dilute it.

### Model architecture
Sigmoid **removed** from the model. It now outputs a raw logit. `predict()` applies `torch.sigmoid()` manually:

```python
# engine/win_prob.py
with torch.no_grad():
    logit = self._net(tensor)
    return float(torch.sigmoid(logit).item())
```

**Why:** `BCEWithLogitsLoss` expects raw logits (fuses sigmoid+BCE internally for numerical stability). Keeping Sigmoid in the model and using `BCELoss` instead causes `RuntimeError: all elements of input should be between 0 and 1` when extreme feature values push sigmoid output to exactly 0.0 or 1.0.

### Data cleaning (required for BCE)
Two issues discovered during training that cause NaN loss:

1. **`balls_remaining <= 0`**: Some matches have negative `balls_remaining` (extras push inn2 past `total_balls`). Filter these out before training — they produce `rrr = rn / 0 = inf`.

2. **`rrr` extremes**: After cleaning, normalised `rrr` still hit 61 (e.g. needing 50 off 1 ball). Logits blow up → NaN gradients on first backward pass. Fix: `rrr.clip(0, 6)`.

```python
train_df = train_df[train_df['balls_remaining'] > 0].copy()
train_df['rrr'] = (train_df['runs_needed'] / train_df['balls_remaining']).clip(0, 6)
```

---

## Checkpoint

Saved to `models/win_prob_nn.pt`. Same keys as NB03:

```python
{
    'input_dim':        6,
    'hidden_dims':      [64, 32, 16],
    'model_state_dict': ...,   # Linear weights only — no Sigmoid params
    'feature_cols':     [...],
    'X_mean':           np.ndarray,
    'X_std':            np.ndarray,
    'training':         'BCE on raw chaser_won labels (NB09)',
}
```

Old MSE model backed up as `models/win_prob_nn_mse_nb03.pt`.

---

## Loading the model correctly

**Critical:** Always instantiate `_WinProbNet` **without** Sigmoid before calling `load_state_dict`. PyTorch only saves Linear weights — if you define the class with Sigmoid, the load succeeds silently but the model outputs raw logits without sigmoid applied. If you define without Sigmoid and forget `torch.sigmoid()` in predict, same problem in reverse.

```python
# CORRECT — matches engine/win_prob.py
class _WinProbNet(nn.Module):
    def __init__(self, input_dim, hidden_dims):
        ...
        layers += [nn.Linear(prev, 1)]   # no Sigmoid
        ...

model.load_state_dict(ckpt['model_state_dict'])
logit = model(x)
prob  = torch.sigmoid(logit).item()   # sigmoid here, not in model
```

For notebooks comparing against the old NB03 model, use a separate class **with** Sigmoid:
```python
class WinProbNetWithSigmoid(nn.Module):
    ...
    layers += [nn.Linear(prev, 1), nn.Sigmoid()]   # NB03 style
```

---

## Feature computation

`rrr` formula changed between NB03 and NB09:

| | Formula | Notes |
|---|---|---|
| NB03 training | `rn / (br + 1)` | `+1` avoids div-by-zero but biases rrr slightly low |
| NB09 training | `rn / br` (clipped 0–6) | Cleaner; `br > 0` enforced by data filter |
| Production (`features.py`) | `rn / max(br, 1)` | Equivalent to NB09 for `br >= 1` |

Production `features.py` already matches the NB09 training formula — no changes needed.

`balls_fraction` uses `br / 120.0` in production (hardcoded). NB09 training used `br / total_balls`. For 20-over IPL matches these are identical.

---

## Validation results

| Match | Label | Signals | Tail (max wp) | Notes |
|---|---|---|---|---|
| DC vs GT 2026-04-08 | HOT | ✓ 3 fired | skipped (close finish) | Last-ball finish — model correctly stays above 0 until end |
| IND vs PAK 2024-06-09 | HOT | ✓ 15 fired | skipped (close finish) | |
| RR vs MI 2026-04-07 | COLD | ✓ silent | 0.003 ≤ 0.05 ✓ | Rain-reduced, only 66 balls |
| MI vs RR 2025-05-01 | COLD | ✓ silent | 0.0001 ≤ 0.05 ✓ | Blowout, 100-run win |

Test: `conda run -n cricket_hot python -m tests.test_win_prob_bce`
