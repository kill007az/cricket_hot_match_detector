"""
WinProbModel: thin wrapper around the saved win_prob_nn.pt checkpoint.

Architecture (from NB09 — BCE training):
  Input(6) → Linear(64) → ReLU → Dropout(0.1)
           → Linear(32) → ReLU → Dropout(0.1)
           → Linear(16) → ReLU → Dropout(0.1)
           → Linear(1)  [raw logit — sigmoid applied in predict()]

Trained with BCEWithLogitsLoss on raw chaser_won labels (not smoothed bin averages).
Replaces NB03 MSE model — better calibrated at tail states (M2 fix).
"""

from pathlib import Path

import numpy as np
import torch
import torch.nn as nn


class _WinProbNet(nn.Module):
    def __init__(self, input_dim: int, hidden_dims: list):
        super().__init__()
        layers = []
        prev = input_dim
        for h in hidden_dims:
            layers += [nn.Linear(prev, h), nn.ReLU(), nn.Dropout(0.1)]
            prev = h
        layers += [nn.Linear(prev, 1)]  # no Sigmoid — logit output
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)


class WinProbModel:
    """Loads the checkpoint once; exposes a single predict() call."""

    def __init__(self, model_path: Path):
        ckpt = torch.load(model_path, weights_only=False)
        self._net = _WinProbNet(
            input_dim=ckpt["input_dim"],
            hidden_dims=ckpt["hidden_dims"],
        )
        self._net.load_state_dict(ckpt["model_state_dict"])
        self._net.eval()

        # Normalisation stats (saved as numpy arrays in checkpoint)
        self._mean: np.ndarray = ckpt["X_mean"]
        self._std: np.ndarray = ckpt["X_std"]

    def predict(self, features: np.ndarray) -> float:
        """
        Args:
            features: float32 array of shape (6,) from FeatureExtractor.
        Returns:
            Win probability in (0, 1).
        """
        x = features.reshape(1, -1)
        x_norm = (x - self._mean) / self._std
        tensor = torch.tensor(x_norm, dtype=torch.float32)
        with torch.no_grad():
            logit = self._net(tensor)
            return float(torch.sigmoid(logit).item())
