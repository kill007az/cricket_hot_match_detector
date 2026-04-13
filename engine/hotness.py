"""
HotnessCalculator: computes the per-ball hotness score.

Formula (from model_design_3.md):
  closeness = 1 - 2 * |win_prob - 0.5|   # 1 when perfectly 50/50
  momentum  = |win_prob - win_prob[t-6]|  # absolute shift over last 6 balls
  hotness   = clip(0, 1,  closeness * 0.6 + momentum * 5 * 0.4)

Momentum is 0 when fewer than 6 win_prob values exist in history.
"""

from collections import deque


def compute(win_prob: float, win_prob_history: deque) -> float:
    """
    Args:
        win_prob:          current ball's win probability (0–1)
        win_prob_history:  deque of previous win_prob values (maxlen=12)
                           populated BEFORE calling this function

    Returns:
        hotness score in [0, 1]
    """
    closeness = 1.0 - 2.0 * abs(win_prob - 0.5)

    momentum = 0.0
    if len(win_prob_history) >= 6:
        momentum = abs(win_prob - win_prob_history[-6])

    raw = closeness * 0.6 + momentum * 5.0 * 0.4
    return max(0.0, min(1.0, raw))
