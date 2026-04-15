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

GET /matches
    List of all match folders found in data/live_polls/.

GET /matches/current
    Most recently active match: latest engine output + metadata.

GET /matches/{match_id}/history
    Full ball-by-ball list read from engine_outputs.jsonl.

GET /matches/{match_id}/signals
    All signal events from the match history.

Configuration (environment variables)
--------------------------------------
ENGINE_URL      URL of the engine service  (default: http://localhost:8000)
LIVE_POLLS_DIR  Override path to live_polls (default: <project_root>/data/live_polls)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import requests as _requests
from fastapi import FastAPI, HTTPException

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ENGINE_URL     = os.environ.get("ENGINE_URL",     "http://localhost:8000")
_DEFAULT_POLLS = Path(__file__).resolve().parent.parent / "data" / "live_polls"
LIVE_POLLS_DIR = Path(os.environ.get("LIVE_POLLS_DIR", str(_DEFAULT_POLLS)))

app = FastAPI(
    title="Cricket Hot Match Orchestrator",
    version="1.0.0",
    description="Coordination layer — aggregates match history and proxies engine status.",
)

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
    """Most recently modified match folder that has at least one engine output."""
    for d in _all_match_dirs():
        if (d / "engine_outputs.jsonl").exists():
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
    outputs = _read_jsonl(match_dir / "engine_outputs.jsonl")
    last = outputs[-1] if outputs else {}
    parts = match_dir.name.split("_vs_")          # "csk_vs_kkr_2026-04-14"
    team1 = parts[0].upper() if len(parts) >= 1 else "?"
    tail  = parts[1] if len(parts) >= 2 else ""    # "kkr_2026-04-14"
    tail_parts = tail.rsplit("_", 1)
    team2 = tail_parts[0].upper() if tail_parts else "?"
    date  = tail_parts[1] if len(tail_parts) == 2 else ""
    return {
        "match_id":      match_dir.name,
        "team1":         team1,
        "team2":         team2,
        "date":          date,
        "balls_seen":    len(outputs),
        "last_state":    last,
    }

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

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
    """Most recently active match with latest engine state."""
    match_dir = _active_match_dir()
    if match_dir is None:
        raise HTTPException(status_code=404, detail="No active match found.")
    meta = _match_meta(match_dir)

    # Also proxy the live engine state if available
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
