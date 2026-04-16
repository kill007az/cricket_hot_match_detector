"""
Orchestrator service — coordination layer between the engine/poller and the UI.

Responsibilities
----------------
- Tracks all match sessions via the data/live_polls/ volume
- Proxies selected requests to the engine (health, state)
- Aggregates full ball-by-ball history for the UI (engine only keeps last output)
- Single HTTP endpoint the Streamlit UI talks to — UI never calls engine or reads files directly

Endpoints
---------
GET /health
    Engine reachability + number of tracked matches.

GET /schedule[?team=CSK]
    Upcoming IPL 2026 fixtures from data/ipl_2026_schedule.json.
    Filters by date >= today (IST) so today's match is always included even after it starts.
    Optional ?team= query param filters by team abbreviation.

GET /matches
    List of all match folders found in data/live_polls/.

GET /matches/current
    Most recently active match: latest engine output + metadata.

GET /matches/{match_id}/history
    Full ball-by-ball list read from engine_outputs.jsonl.

GET /matches/{match_id}/signals
    All signal events from the match history.

GET /matches/{match_id}/ball_events
    Raw ball-by-ball events for innings 2 (chase).

GET /matches/{match_id}/ball_events_inn1
    Raw ball-by-ball events for innings 1.

GET /matches/{match_id}/scorecard/{innings_num}
    Batting and bowling scorecard for innings 1 or 2.
    Reads scorecard_inn{n}.json written by the poller.

GET /bot/status
    Bot subscriber count and number of alerts sent.

Configuration (environment variables)
--------------------------------------
ENGINE_URL      URL of the engine service  (default: http://localhost:8000)
LIVE_POLLS_DIR  Override path to live_polls (default: <project_root>/data/live_polls)
BOT_STATE_PATH  Path to bot_state.json       (default: <project_root>/data/bot_state.json)
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

import requests as _requests
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ENGINE_URL     = os.environ.get("ENGINE_URL",     "http://localhost:8000")
BOT_URL        = os.environ.get("BOT_URL",        "http://localhost:8088")
_DEFAULT_POLLS    = Path(__file__).resolve().parent.parent / "data" / "live_polls"
_SCHEDULE_PATH    = Path(__file__).resolve().parent.parent / "data" / "ipl_2026_schedule.json"
LIVE_POLLS_DIR = Path(os.environ.get("LIVE_POLLS_DIR", str(_DEFAULT_POLLS)))
_DEFAULT_BOT_STATE = Path(__file__).resolve().parent.parent / "data" / "bot_state.json"
BOT_STATE_PATH = Path(os.environ.get("BOT_STATE_PATH", str(_DEFAULT_BOT_STATE)))

app = FastAPI(
    title="Cricket Hot Match Orchestrator",
    version="1.0.0",
    description="Coordination layer — aggregates match history and proxies engine status.",
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "orchestrator | %s %s  ->  %d  (%.0fms)",
        request.method, request.url.path, response.status_code, ms,
    )
    return response

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def _all_match_dirs() -> list[Path]:
    if not LIVE_POLLS_DIR.exists():
        return []
    return sorted(
        (d for d in LIVE_POLLS_DIR.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )


def _active_match_dir() -> Optional[Path]:
    """
    Most recently modified match folder that has inn1 or inn2 data AND is
    not yet complete (no match_complete.flag written by the poller).
    Prefers folders with engine_outputs.jsonl (inn2 in progress),
    falls back to ball_events_inn1.jsonl (inn1 still in progress).
    """
    dirs = _all_match_dirs()
    for d in dirs:
        if (d / "engine_outputs.jsonl").exists() and not (d / "match_complete.flag").exists():
            return d
    for d in dirs:
        if (d / "ball_events_inn1.jsonl").exists() and not (d / "match_complete.flag").exists():
            return d
    return None


def _engine_ok() -> bool:
    try:
        r = _requests.get(f"{ENGINE_URL}/docs", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _match_meta(match_dir: Path) -> dict:
    """Return lightweight metadata for a match folder."""
    outputs   = _read_jsonl(match_dir / "engine_outputs.jsonl")
    inn1_balls = _read_jsonl(match_dir / "ball_events_inn1.jsonl")
    last = outputs[-1] if outputs else {}

    parts = match_dir.name.split("_vs_")          # "csk_vs_kkr_2026-04-14"
    team1 = parts[0].upper() if len(parts) >= 1 else "?"
    tail  = parts[1] if len(parts) >= 2 else ""    # "kkr_2026-04-14"
    tail_parts = tail.rsplit("_", 1)
    team2 = tail_parts[0].upper() if tail_parts else "?"
    date  = tail_parts[1] if len(tail_parts) == 2 else ""

    # Determine match phase
    if outputs:
        phase = "inn2"
    elif inn1_balls:
        phase = "inn1"
    else:
        phase = "pre"

    # Inn1 summary — prefer scorecard team_total (includes wide/no-ball runs)
    # over summing ball_events_inn1 (legal deliveries only)
    inn1_balls_count = len(inn1_balls)
    inn1_overs   = f"{inn1_balls_count // 6}.{inn1_balls_count % 6}" if inn1_balls_count else "0.0"
    inn1_wickets = sum(1 for b in inn1_balls if b.get("wicket"))
    inn1_runs    = sum(b.get("runs", 0) + b.get("extras", 0) for b in inn1_balls)  # fallback
    sc1_path = match_dir / "scorecard_inn1.json"
    if sc1_path.exists():
        try:
            sc1 = json.loads(sc1_path.read_text(encoding="utf-8"))
            if sc1.get("team_total"):
                inn1_runs = sc1["team_total"]
        except Exception:
            pass

    return {
        "match_id":        match_dir.name,
        "team1":           team1,
        "team2":           team2,
        "date":            date,
        "phase":           phase,
        "balls_seen":      len(outputs),
        "last_state":      last,
        "inn1_summary": {
            "runs":    inn1_runs,
            "wickets": inn1_wickets,
            "overs":   inn1_overs,
            "balls":   inn1_balls_count,
        },
    }

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/schedule")
def schedule(team: Optional[str] = None):
    """
    IPL 2026 schedule. Optionally filter by team abbreviation (e.g. ?team=CSK).
    Returns upcoming matches only, sorted by date.
    """
    if not _SCHEDULE_PATH.exists():
        raise HTTPException(status_code=404, detail="Schedule file not found.")
    data = json.loads(_SCHEDULE_PATH.read_text(encoding="utf-8"))
    from datetime import datetime, timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(IST).date()
    matches = [
        m for m in data.get("matches", [])
        if datetime.fromisoformat(m["datetime_ist"]).date() >= today
    ]
    if team:
        t = team.upper()
        matches = [m for m in matches if t in (m["home_abbr"], m["away_abbr"])]
    return {"matches": matches, "total": len(matches)}


@app.get("/health")
def health():
    """Engine reachability and number of tracked matches."""
    return {
        "engine_reachable": _engine_ok(),
        "matches_tracked":  len(_all_match_dirs()),
        "live_polls_dir":   str(LIVE_POLLS_DIR),
    }


@app.get("/matches")
def list_matches():
    """All match folders, most recent first."""
    return [_match_meta(d) for d in _all_match_dirs()]


@app.get("/matches/current")
def current_match():
    """
    Most recently active match with latest engine state.

    Returns match metadata regardless of phase:
      phase="inn1"  → inn1 still in progress; inn1_summary has current score
      phase="inn2"  → inn2 in progress or complete; last_state has engine output
      phase="pre"   → match folder exists but no balls recorded yet
    """
    match_dir = _active_match_dir()
    if match_dir is None:
        raise HTTPException(status_code=404, detail="No active match found.")
    meta = _match_meta(match_dir)

    # Proxy live engine state if inn2 has started
    if meta["phase"] == "inn2":
        match_id = match_dir.name
        try:
            r = _requests.get(f"{ENGINE_URL}/match/{match_id}/state", timeout=3)
            if r.status_code == 200:
                meta["engine_state"] = r.json()
        except Exception:
            pass

    return meta


@app.get("/matches/{match_id}/history")
def match_history(match_id: str):
    """
    Full ball-by-ball engine output list for a match.

    Returns a list of objects with:
        ball, win_prob, hotness, forecast, runs_needed,
        balls_remaining, wickets, signals
    """
    match_dir = LIVE_POLLS_DIR / match_id
    if not match_dir.exists():
        raise HTTPException(status_code=404, detail=f"Match '{match_id}' not found.")

    outputs = _read_jsonl(match_dir / "engine_outputs.jsonl")
    history = []
    for i, o in enumerate(outputs, start=1):
        history.append({
            "ball":            i,
            "win_prob":        o.get("win_prob"),
            "hotness":         o.get("hotness"),
            "forecast":        o.get("forecast"),      # None before ball 60
            "runs_needed":     o.get("runs_needed"),
            "balls_remaining": o.get("balls_remaining"),
            "wickets":         o.get("wickets"),
            "signals":         o.get("signals", []),
            "processing_ms":   o.get("processing_ms"),
        })
    return history


@app.get("/bot/status")
def bot_status():
    """
    Bot subscriber count and recent alert fingerprints.
    Returns empty state if bot_state.json does not exist yet.
    """
    if not BOT_STATE_PATH.exists():
        return {"subscribers": 0, "alerts_sent": 0, "running": False}
    try:
        data = json.loads(BOT_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"subscribers": 0, "alerts_sent": 0, "running": False}
    return {
        "running":       True,
        "subscribers":   len(data.get("subscribed_chats", [])),
        "alerts_sent":   len(data.get("seen_fps", [])),
    }


class _ChatRequest(BaseModel):
    message: str
    chat_id: str = "streamlit"


@app.post("/chat")
def chat(body: _ChatRequest):
    """
    Proxy a chat message to the bot agent and return the reply.
    Response: { "reply": "...", "charts": ["<base64-png>", ...] }
    """
    try:
        r = _requests.post(
            f"{BOT_URL}/chat",
            json={"message": body.message, "chat_id": body.chat_id},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()
    except _requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Bot service not reachable.")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/matches/{match_id}/scorecard/{innings_num}")
def match_scorecard(match_id: str, innings_num: int):
    """
    Batting and bowling scorecard for a match innings.

    innings_num: 1 or 2

    Returns:
        { "batting": [...], "bowling": [...] }
    """
    if innings_num not in (1, 2):
        raise HTTPException(status_code=400, detail="innings_num must be 1 or 2.")
    match_dir = LIVE_POLLS_DIR / match_id
    if not match_dir.exists():
        raise HTTPException(status_code=404, detail=f"Match '{match_id}' not found.")
    path = match_dir / f"scorecard_inn{innings_num}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Scorecard for innings {innings_num} not available yet.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to read scorecard.")


@app.get("/matches/{match_id}/ball_events_inn1")
def match_ball_events_inn1(match_id: str):
    """Raw ball-by-ball events for innings 1 (batting team's innings)."""
    match_dir = LIVE_POLLS_DIR / match_id
    if not match_dir.exists():
        raise HTTPException(status_code=404, detail=f"Match '{match_id}' not found.")
    records = _read_jsonl(match_dir / "ball_events_inn1.jsonl")
    if not records:
        raise HTTPException(status_code=404, detail="Innings 1 data not available yet.")
    return records


@app.get("/matches/{match_id}/ball_events")
def match_ball_events(match_id: str):
    """
    Raw ball-by-ball events for a match.

    Returns a list of objects with:
        innings, over, runs, extras, wicket
    """
    match_dir = LIVE_POLLS_DIR / match_id
    if not match_dir.exists():
        raise HTTPException(status_code=404, detail=f"Match '{match_id}' not found.")
    return _read_jsonl(match_dir / "ball_events.jsonl")


@app.get("/matches/{match_id}/signals")
def match_signals(match_id: str):
    """All signal events from a match, with ball number."""
    match_dir = LIVE_POLLS_DIR / match_id
    if not match_dir.exists():
        raise HTTPException(status_code=404, detail=f"Match '{match_id}' not found.")

    outputs = _read_jsonl(match_dir / "engine_outputs.jsonl")
    return [
        {"ball": i, "signals": o["signals"]}
        for i, o in enumerate(outputs, start=1)
        if o.get("signals")
    ]
