"""
SignalEvaluator: generates user-facing notification signals.

Two independent signals (per live architecture in model_design_3.md):

  1. PRE_MATCH  — fired once on ball 1 when the target is a 50/50 chase
  2. IN_GAME    — fired on any ball >= 60 when forecast crosses threshold

Forecast threshold (0.60) is not formally calibrated; see model_design_3.md §open questions.
Win prob gate (0.25–0.75) suppresses the in-game signal when the match is clearly one-sided.
Diagnosed after CSK vs KKR 2026-04-16: forecaster over-amplified a pre-gate momentum spike
when win_prob was ~0.20, firing a false IN_GAME alert. Gate validated in NB08.
"""

from typing import Optional

from engine.models import ChaseState

# Tunable constants — adjust after calibration on a larger validation set
PRE_MATCH_LOW: float = 0.40
PRE_MATCH_HIGH: float = 0.60
FORECAST_THRESHOLD: float = 0.60
GATE_BALL: int = 60  # no in-game signals before this ball
WIN_PROB_GATE_LOW: float = 0.25   # suppress signal when match is clearly one-sided
WIN_PROB_GATE_HIGH: float = 0.75


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

    # In-game forecast: only after the 60-ball gate and when match is still live
    if (
        state.balls_faced >= GATE_BALL
        and forecast is not None
        and forecast >= FORECAST_THRESHOLD
        and WIN_PROB_GATE_LOW <= win_prob <= WIN_PROB_GATE_HIGH
    ):
        signals.append("match heating up — tune in now")

    return signals
