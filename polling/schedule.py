"""
polling/schedule.py — IPL 2026 schedule reader.

Reads data/ipl_2026_schedule.json and provides:
  - find_next_match(team1, team2)  → next upcoming match for those teams
  - find_next_ipl_match()          → next upcoming IPL match (any teams)
  - seconds_until_match(match)     → seconds until match start IST
  - format_match(match)            → human-readable one-liner

"Upcoming" is defined as datetime > now (IST) — only matches that haven't started yet.
Mid-match restarts are handled by find_live_ipl_match() in run_live.py, not the schedule.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

_SCHEDULE_PATH = Path(__file__).resolve().parent.parent / "data" / "ipl_2026_schedule.json"
_IST = timezone(timedelta(hours=5, minutes=30))

# Map abbreviations and common variants to full names (for matching CLI args)
_ABBR_MAP: dict[str, str] = {
    "RCB": "Royal Challengers Bengaluru",
    "SRH": "Sunrisers Hyderabad",
    "MI":  "Mumbai Indians",
    "KKR": "Kolkata Knight Riders",
    "RR":  "Rajasthan Royals",
    "CSK": "Chennai Super Kings",
    "PBKS": "Punjab Kings",
    "PK":  "Punjab Kings",
    "GT":  "Gujarat Titans",
    "LSG": "Lucknow Super Giants",
    "DC":  "Delhi Capitals",
}


def _load() -> list[dict]:
    if not _SCHEDULE_PATH.exists():
        return []
    data = json.loads(_SCHEDULE_PATH.read_text(encoding="utf-8"))
    return data.get("matches", [])


def _is_upcoming(match: dict) -> bool:
    start = datetime.fromisoformat(match["datetime_ist"])
    return start > datetime.now(_IST)


def _matches_teams(match: dict, team1: str, team2: str) -> bool:
    t1 = team1.upper()
    t2 = team2.upper()
    home = match["home_abbr"].upper()
    away = match["away_abbr"].upper()
    return {t1, t2} == {home, away}


def find_next_match(team1: Optional[str] = None, team2: Optional[str] = None) -> Optional[dict]:
    """
    Return the next upcoming match dict, optionally filtered by team abbreviations.
    Returns None if schedule file is missing or no upcoming match found.
    """
    matches = _load()
    upcoming = [m for m in matches if _is_upcoming(m)]
    if not upcoming:
        return None
    if team1 and team2:
        filtered = [m for m in upcoming if _matches_teams(m, team1, team2)]
        return filtered[0] if filtered else None
    return upcoming[0]


def find_next_ipl_match() -> Optional[dict]:
    """Return the very next upcoming IPL match."""
    return find_next_match()


def seconds_until_match(match: dict, pre_buffer_mins: int = 15) -> float:
    """
    Seconds until `pre_buffer_mins` before match start.
    Returns 0 if match is already within the buffer window or in the past.
    """
    start = datetime.fromisoformat(match["datetime_ist"])
    target = start - timedelta(minutes=pre_buffer_mins)
    delta = (target - datetime.now(_IST)).total_seconds()
    return max(0.0, delta)


def format_match(match: dict) -> str:
    """Human-readable one-liner for a match."""
    return (
        f"M{match['match']:>2}  {match['home_abbr']} vs {match['away_abbr']}"
        f"  {match['date']} {match['time_ist']} IST"
        f"  ({match['venue']})"
    )
