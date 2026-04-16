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

    extras includes byes, leg-byes, AND runs from any wide/no-ball that shared
    the same overNumber (i.e. was bowled before the legal delivery on that ball).
    This ensures the engine's runs_needed decreases correctly for those deliveries.
    """
    # Build a map: overNumber → all items at that overNumber (sorted by timestamp asc)
    valid = [
        item for item in items
        if item.get("overNumber") is not None and item.get("ballNbr", 0) > 0
    ]
    valid.sort(key=lambda x: x.get("timestamp", 0))

    by_over: dict[float, list[dict]] = {}
    for item in valid:
        over = round(float(item["overNumber"]), 1)
        by_over.setdefault(over, []).append(item)

    events = []
    for over in sorted(by_over.keys()):
        group = by_over[over]
        # Last item (highest timestamp) is the legal ball
        legal_item = group[-1]
        legal_runs = int(legal_item.get("legalRuns", 0))
        total_runs = int(legal_item.get("totalRuns", 0))
        event_str  = str(legal_item.get("event", ""))

        # Sum totalRuns from any preceding wide/no-ball items at same overNumber
        wide_noball_runs = sum(int(it.get("totalRuns", 0)) for it in group[:-1])

        events.append({
            "innings": innings,
            "over":    over,
            "runs":    legal_runs,
            "extras":  (total_runs - legal_runs) + wide_noball_runs,
            "wicket":  "WICKET" in event_str,
        })
    return events


def parse_extra_deliveries(items: list[dict], innings: int = 2) -> list[dict]:
    """
    Return wide and no-ball deliveries — those with overNumber + ballNbr > 0
    that share an overNumber with a legal ball (i.e. were overwritten during
    deduplication in _extract_legal_balls).

    Each returned dict has:
        { "innings", "over_key", "extra_runs" }

    over_key is the raw overNumber string used for fingerprinting seen extras.
    extra_runs is totalRuns for that delivery.
    """
    valid = [
        item for item in items
        if item.get("overNumber") is not None and item.get("ballNbr", 0) > 0
    ]
    valid.sort(key=lambda x: x.get("timestamp", 0))

    # Build map: overNumber → list of items (sorted by timestamp asc)
    by_over: dict[float, list[dict]] = {}
    for item in valid:
        over = round(float(item["overNumber"]), 1)
        by_over.setdefault(over, []).append(item)

    extras = []
    for over, group in by_over.items():
        if len(group) > 1:
            # All but the last (highest timestamp = legal ball) are wides/no-balls
            for item in group[:-1]:
                extras.append({
                    "innings":    innings,
                    "over_key":   f"{innings}:{over:.1f}:{item.get('timestamp', 0)}",
                    "extra_runs": int(item.get("totalRuns", 0)),
                })
    return extras


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


def extract_scorecard(items: list[dict]) -> dict:
    """
    Extract batting and bowling scorecards from raw Cricbuzz commentary items.

    Returns:
        {
          "batting": [
            { "name", "runs", "balls", "fours", "sixes", "strike_rate", "dots" }
            ...  ordered by batId appearance
          ],
          "bowling": [
            { "name", "overs", "runs", "wickets", "maidens", "wides", "noballs", "economy" }
            ...  ordered by bowlId appearance
          ]
        }

    Strategy: for each batsman/bowler, keep the item with the highest ball count
    (most recent stats). Ignores placeholder entries with empty names or id == 0.
    """
    batters: dict[int, dict] = {}
    bowlers: dict[int, dict] = {}
    batter_order: list[int] = []
    bowler_order: list[int] = []

    for item in items:
        bs = item.get("batsmanStriker") or {}
        bat_id = bs.get("batId", 0)
        if bat_id and bs.get("batName"):
            prev = batters.get(bat_id)
            if prev is None or bs.get("batBalls", 0) >= prev.get("batBalls", 0):
                batters[bat_id] = bs
            if bat_id not in batter_order:
                batter_order.append(bat_id)

        bw = item.get("bowlerStriker") or {}
        bowl_id = bw.get("bowlId", 0)
        if bowl_id and bw.get("bowlName"):
            prev = bowlers.get(bowl_id)
            if prev is None or bw.get("bowlOvs", 0) >= prev.get("bowlOvs", 0):
                bowlers[bowl_id] = bw
            if bowl_id not in bowler_order:
                bowler_order.append(bowl_id)

    batting = [
        {
            "name":         batters[bid]["batName"],
            "runs":         batters[bid].get("batRuns", 0),
            "balls":        batters[bid].get("batBalls", 0),
            "fours":        batters[bid].get("batFours", 0),
            "sixes":        batters[bid].get("batSixes", 0),
            "strike_rate":  batters[bid].get("batStrikeRate", 0.0),
            "dots":         batters[bid].get("batDots", 0),
        }
        for bid in batter_order if bid in batters
    ]

    bowling = [
        {
            "name":     bowlers[bid]["bowlName"],
            "overs":    bowlers[bid].get("bowlOvs", 0),
            "runs":     bowlers[bid].get("bowlRuns", 0),
            "wickets":  bowlers[bid].get("bowlWkts", 0),
            "maidens":  bowlers[bid].get("bowlMaidens", 0),
            "wides":    bowlers[bid].get("bowlWides", 0),
            "noballs":  bowlers[bid].get("bowlNoballs", 0),
            "economy":  bowlers[bid].get("bowlEcon", 0.0),
        }
        for bid in bowler_order if bid in bowlers
    ]

    # True team total includes wides/no-ball runs that aren't in ball_events
    team_total = sum_innings_runs(items)

    # Dismissed batters: batsmanStriker on any WICKET ball is the dismissed batter
    dismissed: set[str] = set()
    for item in items:
        if "WICKET" in str(item.get("event", "")):
            bs = item.get("batsmanStriker") or {}
            name = bs.get("batName")
            if name:
                dismissed.add(name)

    # Current striker/bowler: most recent commentary item with these fields
    current_striker = None
    current_bowler  = None
    for item in reversed(items):
        if current_striker is None:
            bs = item.get("batsmanStriker") or {}
            if bs.get("batName"):
                current_striker = bs.get("batName")
        if current_bowler is None:
            bw = item.get("bowlerStriker") or {}
            if bw.get("bowlName"):
                current_bowler = bw.get("bowlName")
        if current_striker and current_bowler:
            break

    not_out_names = {b["batName"] for b in batters.values() if b.get("batName") and b["batName"] not in dismissed}

    return {
        "batting":         batting,
        "bowling":         bowling,
        "team_total":      team_total,
        "dismissed":       sorted(dismissed),
        "not_out":         sorted(not_out_names),
        "current_striker": current_striker,
        "current_bowler":  current_bowler,
    }


def ball_key(ball: dict) -> str:
    """
    Unique string key for a BallEvent dict.  Mirrors BallEvent.ball_key in
    engine/models.py so the poller's seen-set aligns with the engine's
    idempotency check.

        key = "innings:over"  e.g. "2:14.3"
    """
    return f"{ball['innings']}:{ball['over']:.1f}"
