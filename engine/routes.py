"""
API routes for the cricket hot match engine.

Endpoints
---------
POST /match/init
    Initialise a new match session with first-innings summary data.

POST /match/{match_id}/ball
    Process one legal delivery and return per-ball engine output.
    Idempotent: re-sending the same delivery returns the previous output
    with is_duplicate=True.

GET  /match/{match_id}/state
    Return the current ChaseState and latest EngineOutput for a match.
"""

import dataclasses
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from engine.models import BallEvent

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas (Pydantic)
# ---------------------------------------------------------------------------

class MatchInitRequest(BaseModel):
    match_id: str
    target: int = Field(..., gt=0, description="Runs needed to win (inn1 score + 1)")
    total_balls: int = Field(
        ..., gt=0, le=120,
        description="Actual legal balls in innings 1 (NOT overs * 6)"
    )


class MatchInitResponse(BaseModel):
    match_id: str
    target: int
    total_balls: int
    message: str


class BallEventRequest(BaseModel):
    innings: int = Field(2, description="Always 2 for a chase")
    over: float = Field(
        ...,
        description="over.delivery notation — e.g. 14.3 = over 14, 3rd legal ball"
    )
    runs: int = Field(..., ge=0, description="Batter runs off this ball")
    extras: int = Field(0, ge=0, description="Extras on this ball (byes, leg-byes)")
    wicket: bool = False
    timestamp: Optional[datetime] = None


class EngineOutputResponse(BaseModel):
    match_id: str
    win_prob: float
    hotness: float
    forecast: Optional[float]
    runs_needed: int
    balls_remaining: int
    wickets: int
    signals: list
    is_duplicate: bool
    processing_ms: float


class MatchStateResponse(BaseModel):
    match_id: str
    target: int
    total_balls: int
    runs_scored: int
    wickets: int
    balls_faced: int
    runs_needed: int
    balls_remaining: int
    last_output: Optional[EngineOutputResponse]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _engine(request: Request) -> "EngineOrchestrator":  # noqa: F821
    return request.app.state.engine


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/match/init", response_model=MatchInitResponse, status_code=201)
def init_match(body: MatchInitRequest, request: Request):
    engine = _engine(request)
    engine.init_match(
        match_id=body.match_id,
        target=body.target,
        total_balls=body.total_balls,
    )
    return MatchInitResponse(
        match_id=body.match_id,
        target=body.target,
        total_balls=body.total_balls,
        message="Match session initialised.",
    )


@router.post("/match/{match_id}/ball", response_model=EngineOutputResponse)
def process_ball(match_id: str, body: BallEventRequest, request: Request):
    engine = _engine(request)
    if not engine.has_match(match_id):
        raise HTTPException(
            status_code=404,
            detail=f"Match '{match_id}' not found. Call POST /match/init first.",
        )

    event = BallEvent(
        match_id=match_id,
        innings=body.innings,
        over=body.over,
        runs=body.runs,
        extras=body.extras,
        wicket=body.wicket,
        timestamp=body.timestamp or datetime.utcnow(),
    )

    output = engine.process_ball(event)
    return EngineOutputResponse(**dataclasses.asdict(output))


@router.get("/match/{match_id}/state", response_model=MatchStateResponse)
def get_match_state(match_id: str, request: Request):
    engine = _engine(request)
    if not engine.has_match(match_id):
        raise HTTPException(
            status_code=404,
            detail=f"Match '{match_id}' not found.",
        )

    session = engine.get_session(match_id)
    cs = session.chase_state
    last = session.last_output

    last_resp = None
    if last is not None:
        last_resp = EngineOutputResponse(**dataclasses.asdict(last))

    return MatchStateResponse(
        match_id=cs.match_id,
        target=cs.target,
        total_balls=cs.total_balls,
        runs_scored=cs.runs_scored,
        wickets=cs.wickets,
        balls_faced=cs.balls_faced,
        runs_needed=cs.runs_needed,
        balls_remaining=cs.balls_remaining,
        last_output=last_resp,
    )


@router.get("/debug/latency")
def debug_latency(request: Request):
    return _engine(request).get_latency_stats()
