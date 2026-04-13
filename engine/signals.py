"""
SignalEvaluator: generates user-facing notification signals.

Two independent signals (per live architecture in model_design_3.md):

  1. PRE_MATCH  — fired once on ball 1 when the target is a 50/50 chase
  2. IN_GAME    — fired on any ball >= 60 when forecast crosses threshold

Forecast threshold (0.55) is not formally calibrated; see model_design_3.md §open questions.
"""

from typing import Optional

from engine.models import ChaseState

# Tunable constants — adjust after calibration on a larger validation set
PRE_MATCH_LOW: float = 0.40
PRE_MATCH_HIGH: float = 0.60
FORECAST_THRESHOLD: float = 0.55
GATE_BALL: int = 60  # no in-game signals before this ball


def evaluate(
    state: ChaseState,
    win_prob: float,
    forecast: Optional[float],
) -> list:
    """
    Args:
        state:     post-update ChaseState (balls_faced already incremented)
        win_prob:  current ball's win probability
        forecast:  forecaster output, or None if not yet available

    Returns:
        List of signal strings (empty when nothing fires).
    """
    signals = []

    # Pre-match: fires exactly once on the first legal delivery
    if state.balls_faced == 1 and PRE_MATCH_LOW <= win_prob <= PRE_MATCH_HIGH:
        signals.append("50/50 chase — worth watching from the start")

    # In-game forecast: only after the 60-ball gate
    if (
        state.balls_faced >= GATE_BALL
        and forecast is not None
        and forecast >= FORECAST_THRESHOLD
    ):
        signals.append("match heating up — tune in now")

    return signals
