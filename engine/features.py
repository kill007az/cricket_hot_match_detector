"""
FeatureExtractor: derives 6 model input features from ChaseState.

Feature order must match the training order in NB03 exactly:
  [runs_needed, balls_remaining, wickets_fallen, rrr, balls_fraction, wickets_fraction]

NOTE: balls_fraction uses / 120.0 (hardcoded), not / total_balls.
Both win_prob NN and hotness forecaster were trained with this convention.
Using / total_balls would mis-calibrate DLS matches, but / 120.0 mis-calibrates them too —
the difference is this matches what the model actually learned.
"""

import numpy as np

from engine.models import ChaseState

FEATURE_NAMES = [
    "runs_needed",
    "balls_remaining",
    "wickets_fallen",
    "rrr",
    "balls_fraction",
    "wickets_fraction",
]


def extract(state: ChaseState) -> np.ndarray:
    """
    Returns a float32 array of shape (6,).

    Division safety: rrr denominator is max(balls_remaining, 1) — never divides by 0.
    """
    rn = float(state.runs_needed)
    br = float(state.balls_remaining)
    wk = float(state.wickets)

    br_safe = max(br, 1.0)

    features = np.array(
        [rn, br, wk, rn / br_safe, br / 120.0, wk / 10.0],
        dtype=np.float32,
    )
    return features
