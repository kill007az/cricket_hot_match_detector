"""
StateUpdater: applies a BallEvent to ChaseState, returning a new ChaseState.

Immutable update pattern — does not mutate the input state.
"""

import dataclasses

from engine.models import BallEvent, ChaseState


def update(state: ChaseState, event: BallEvent) -> ChaseState:
    """
    Apply event to state and return updated state.

    Rules (per feature spec §5.1):
    - runs_scored increases by runs + extras on every delivery
    - balls_faced increments by 1 on every BallEvent (contract: only legal balls are sent)
    - wickets increments if event.wicket is True
    - balls_remaining is clamped to 0 (derived property on ChaseState)
    """
    new_state = dataclasses.replace(
        state,
        runs_scored=state.runs_scored + event.runs + event.extras,
        balls_faced=state.balls_faced + 1,
        wickets=state.wickets + (1 if event.wicket else 0),
    )
    return new_state
