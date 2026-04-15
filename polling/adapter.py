"""
adapter.py — converts raw Cricbuzz commentary items into the engine's BallEvent format.

Cricbuzz commentary item (relevant fields, new API format):
    {
        "overNumber":  2.3,      # float: over.legal_ball_in_over (1-indexed)
                                 # None for non-delivery items (messages, etc.)
        "ballNbr":     15,       # sequential ball counter; 0 for non-delivery items
        "legalRuns":   1,        # runs credited to batter
        "totalRuns":   1,        # batter runs + extras (leg-byes, byes)
        "event":       "NONE",   # "NONE" | "FOUR" | "SIX" | "WICKET" | "over-break"
                                 # may also be composite e.g. "over-break,HIGHSCORING_OVER"
        "timestamp":   1776...   # epoch ms — used for chronological ordering
        "batTeamScore": 26,      # cumulative team score at this point
    }

Wide / no-ball identification:
    Wides and no-balls share the same overNumber as the subsequent legal delivery
    and have a lower timestamp.  We deduplicate by overNumber, keeping the item
    with the highest timestamp (the legal ball always comes after the wide/no-ball).

Engine BallEvent contract (from engine/models.py and engine/routes.py):
    innings:  int   always 2 for the chase
    over:     float over.legal_ball notation  e.g. 14.3
    runs:     int   batter runs (legalRuns)
    extras:   int   byes + legByes only  (= totalRuns - legalRuns for legal deliveries)
    wicket:   bool
"""

from __future__ import annotations


def _extract_legal_balls(items: list[dict]) -> list[dict]:
    """
    Given a flat list of raw Cricbuzz commentary items (any order), return
    only legal deliveries in chronological order.

    Logic:
    - Discard items with no overNumber or ballNbr == 0 (messages, separators).
    - Sort remaining by timestamp ascending.
    - For each unique overNumber keep only the item with the highest timestamp.
      This discards wides and no-balls, which share an overNumber with the
      subsequent legal delivery but have an earlier timestamp.
    - Return sorted by overNumber.
    """
    valid = [
        item for item in items
        if item.get("overNumber") is not None and item.get("ballNbr", 0) > 0
    ]
    # Sort chronologically so later items overwrite earlier ones for same overNumber
    valid.sort(key=lambda x: x.get("timestamp", 0))

    seen: dict[float, dict] = {}
    for item in valid:
        over = round(float(item["overNumber"]), 1)
        seen[over] = item  # overwrites wide/no-ball with the legal ball

    return [seen[k] for k in sorted(seen.keys())]


def parse_legal_balls(items: list[dict], innings: int = 2) -> list[dict]:
    """
    Filter to legal deliveries and return engine-compatible BallEvent dicts.

    Output list is ordered chronologically (ball 1 → last ball), matching the
    order in which the poller will send them to the engine.

    Each dict matches the BallEventRequest schema from engine/routes.py:
        {innings, over, runs, extras, wicket}
    """
    events = []
    for item in _extract_legal_balls(items):
        over = round(float(item["overNumber"]), 1)
        legal_runs = int(item.get("legalRuns", 0))
        total_runs = int(item.get("totalRuns", 0))
        event_str  = str(item.get("event", ""))
        events.append({
            "innings": innings,
            "over":    over,
            "runs":    legal_runs,
            "extras":  total_runs - legal_runs,   # byes + legByes for legal deliveries
            "wicket":  "WICKET" in event_str,
        })
    return events


def count_legal_balls(items: list[dict]) -> int:
    """
    Count legal deliveries in an innings commentary.

    Used to compute total_balls for /match/init from the completed inn1.
    Must NOT use overs * 6 — rain-reduced matches would be wrong.
    """
    return len(_extract_legal_balls(items))


def sum_innings_runs(items: list[dict]) -> int:
    """
    Sum all runs for an innings including wides, no-balls, byes and leg-byes.

    For each delivery (including wides/no-balls), totalRuns carries the full
    run contribution on that ball.  Summing across all deliveries with a valid
    overNumber and ballNbr > 0 gives the correct innings total.

    Used to compute target = inn1_runs + 1.
    """
    total = 0
    for item in items:
        if item.get("overNumber") is not None and item.get("ballNbr", 0) > 0:
            total += int(item.get("totalRuns", 0))
    return total


def ball_key(ball: dict) -> str:
    """
    Unique string key for a BallEvent dict.  Mirrors BallEvent.ball_key in
    engine/models.py so the poller's seen-set aligns with the engine's
    idempotency check.

        key = "innings:over"  e.g. "2:14.3"
    """
    return f"{ball['innings']}:{ball['over']:.1f}"
