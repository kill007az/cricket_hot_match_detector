"""
Core data structures for the cricket hot match engine.

BallEvent contract: only LEGAL deliveries (no wides, no no-balls).
The sending backend is responsible for filtering illegal deliveries and
aggregating any extras from them into the next legal delivery if needed.
"""

from __future__ import annotations

import dataclasses
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class BallEvent:
    """A single legal delivery in the 2nd innings chase."""

    match_id: str
    innings: int          # 2 for the chase
    over: float           # e.g. 14.3 = over 14 (0-indexed), 3rd legal ball in over
    runs: int             # batter runs off this ball
    extras: int           # extras on this ball (byes, leg-byes included; wides/no-balls
                          # are not sent as BallEvents — they're illegal)
    wicket: bool
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def ball_key(self) -> str:
        """Unique string ID for this delivery. Used for idempotency checks."""
        return f"{self.match_id}:{self.innings}:{self.over:.1f}"


@dataclass
class ChaseState:
    """Running state of the 2nd innings chase."""

    match_id: str
    target: int           # runs needed to win (inn1 score + 1)
    total_balls: int      # total legal balls in the match (from inn1 actual balls)

    runs_scored: int = 0
    wickets: int = 0
    balls_faced: int = 0  # legal deliveries faced so far

    @property
    def runs_needed(self) -> int:
        return max(self.target - self.runs_scored, 0)

    @property
    def balls_remaining(self) -> int:
        return max(self.total_balls - self.balls_faced, 0)


@dataclass
class HotnessState:
    """Temporal memory for the hotness pipeline."""

    win_prob_history: deque = field(
        default_factory=lambda: deque(maxlen=12)
    )
    hotness_history: deque = field(
        default_factory=lambda: deque(maxlen=12)
    )


@dataclass
class MatchSession:
    """All mutable state for one active match."""

    chase_state: ChaseState
    hotness_state: HotnessState
    processed_balls: set = field(default_factory=set)
    last_output: Optional[EngineOutput] = None


@dataclass
class EngineOutput:
    """Per-ball output emitted by the engine."""

    match_id: str

    win_prob: float
    hotness: float
    forecast: Optional[float]   # None before ball 60 or before 12-ball history exists

    runs_needed: int
    balls_remaining: int
    wickets: int

    signals: list

    is_duplicate: bool = False   # True if this ball was already processed (idempotent replay)
    processing_ms: float = 0.0   # Total server-side engine time for this ball (excl. HTTP)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)
