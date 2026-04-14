"""
HotnessForecaster: predicts max hotness in the next 6 balls.

Architecture (from NB07):
  Input(13) → Linear(64) → ReLU → Dropout(0.15)
            → Linear(32) → ReLU → Dropout(0.15)
            → Linear(1)  → Sigmoid

Input composition (13 features):
  - 12 hotness lag values (z-score normalised with X_train_mean / X_train_std)
  - balls_remaining / 120.0  (already in [0,1], not normalised)

Checkpoint keys: model_state_dict, input_dim, hidden_dims, lookback,
                 horizon, X_train_mean, X_train_std
"""

from collections import deque
from pathlib import Path
from typing import Optional

import numpy as np
import torch
import torch.nn as nn


class _ForecasterNet(nn.Module):
    def __init__(self, input_dim: int, hidden_dims: list):
        super().__init__()
        layers = []
        prev = input_dim
        for h in hidden_dims:
            layers += [nn.Linear(prev, h), nn.ReLU(), nn.Dropout(0.15)]
            prev = h
        layers += [nn.Linear(prev, 1), nn.Sigmoid()]
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)


class HotnessForecaster:
    """Loads the checkpoint once; exposes a single predict() call."""

    def __init__(self, model_path: Path):
        ckpt = torch.load(model_path, weights_only=False)
        self._net = _ForecasterNet(
            input_dim=ckpt["input_dim"],
            hidden_dims=ckpt["hidden_dims"],
        )
        self._net.load_state_dict(ckpt["model_state_dict"])
        self._net.eval()

        self._lookback: int = ckpt["lookback"]   # 12
        self._mean: float = float(ckpt["X_train_mean"])
        self._std: float = float(ckpt["X_train_std"])

    def predict(
        self,
        hotness_history: deque,
        balls_remaining: int,
    ) -> Optional[float]:
        """
        Args:
            hotness_history:  deque of recent hotness values (maxlen=12).
                              Must contain >= 12 values; returns None otherwise.
            balls_remaining:  balls left in the match (used to compute br/120).

        Returns:
            Predicted max hotness in the next 6 balls, or None.
        """
        if len(hotness_history) < self._lookback:
            return None

        h = np.array(list(hotness_history), dtype=np.float32)
        h_norm = (h - self._mean) / self._std
        br_frac = np.float32(balls_remaining / 120.0)

        x = np.append(h_norm, br_frac).reshape(1, -1)
        tensor = torch.tensor(x, dtype=torch.float32)

        with torch.no_grad():
            return float(self._net(tensor).item())
