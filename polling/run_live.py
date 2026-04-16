"""
run_live.py — CLI entry point for the live polling service.

Default behaviour (no arguments)
---------------------------------
Scrapes cricbuzz.com/live-cricket-scores to find any live IPL match,
derives team names and match-id automatically, and starts polling.
Works for any IPL fixture without touching any config.

    conda run -n cricket_hot python -m polling.run_live

Override behaviour
------------------
Supply --team1 / --team2 to pin a specific match when multiple IPL games
are live simultaneously.  --cb-id skips auto-discovery entirely.
--match-id overrides the auto-generated slug if needed.

    python -m polling.run_live --team1 CSK --team2 KKR
    python -m polling.run_live --cb-id 151763 --team1 CSK --team2 KKR
    python -m polling.run_live --match-id csk_vs_kkr_2026-04-14 --team1 CSK --team2 KKR

Match-id slug is generated as:  {team1_lower}_vs_{team2_lower}_{YYYY-MM-DD}
"""

import argparse
import json
import logging
import sys
import time
from datetime import date
from pathlib import Path

_COMPLETED_PATH = Path(__file__).resolve().parent.parent / "data" / "completed_matches.json"


def _load_completed_cb_ids() -> set[int]:
    if not _COMPLETED_PATH.exists():
        return set()
    try:
        data = json.loads(_COMPLETED_PATH.read_text(encoding="utf-8"))
        return set(data.get("completed_cb_ids", []))
    except Exception:
        return set()


def _save_completed_cb_id(cb_id: int, label: str = "") -> None:
    try:
        data = {"completed_cb_ids": [], "notes": {}}
        if _COMPLETED_PATH.exists():
            data = json.loads(_COMPLETED_PATH.read_text(encoding="utf-8"))
        ids = data.setdefault("completed_cb_ids", [])
        if cb_id not in ids:
            ids.append(cb_id)
        if label:
            data.setdefault("notes", {})[str(cb_id)] = label
        _COMPLETED_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logging.warning("Could not save completed match: %s", exc)

from polling.cricbuzz_client import CricbuzzClient
from polling.poller import LivePoller
from polling.schedule import find_next_ipl_match, find_next_match, format_match, seconds_until_match

_DISCOVERY_RETRY_SECS = 60   # fallback retry when no schedule data available
_PRE_MATCH_BUFFER_MINS = 15  # wake up this many minutes before scheduled start


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m polling.run_live",
        description=(
            "Live cricket match polling service.\n"
            "With no arguments, auto-discovers the current live IPL match."
        ),
    )
    p.add_argument(
        "--team1",
        default=None,
        help="Team 1 abbreviation (e.g. CSK). Auto-detected from Cricbuzz if omitted.",
    )
    p.add_argument(
        "--team2",
        default=None,
        help="Team 2 abbreviation (e.g. KKR). Auto-detected from Cricbuzz if omitted.",
    )
    p.add_argument(
        "--match-id",
        default=None,
        help=(
            "Slug used as engine match_id and data folder name. "
            "Auto-generated as {team1}_{team2}_{date} if omitted."
        ),
    )
    p.add_argument(
        "--cb-id",
        type=int,
        default=None,
        help=(
            "Cricbuzz numeric match ID (e.g. 151763). "
            "Optional — auto-discovered from cricbuzz.com if omitted."
        ),
    )
    p.add_argument(
        "--poll-interval",
        type=int,
        default=30,
        help="Seconds between Cricbuzz polls (default: 30)",
    )
    p.add_argument(
        "--engine-url",
        default="http://localhost:8000",
        help="Base URL of the engine API (default: http://localhost:8000)",
    )
    p.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Python logging level (default: WARNING)",
    )
    return p


def _resolve_match(args, skip_cb_id: int | None = None) -> tuple[str, str, str]:
    """
    Return (team1_abbr, team2_abbr, match_id).

    If --team1/--team2 are not supplied, polls Cricbuzz until a live IPL
    match is found via HTML scraping.  match-id is auto-generated unless
    explicitly set.

    skip_cb_id: cb_id of the match that just finished — skip it if Cricbuzz
    still shows it as live (can happen briefly after the final ball).
    """
    client = CricbuzzClient()

    if args.team1 and args.team2:
        team1, team2 = args.team1.upper(), args.team2.upper()
    else:
        # --- Check for a currently live match first ---
        # This handles restarts mid-match correctly: if a match is already live
        # on Cricbuzz, use it immediately without consulting the schedule.
        completed = _load_completed_cb_ids()
        if skip_cb_id:
            completed.add(skip_cb_id)

        print("Checking for a currently live IPL match...")
        result = client.find_live_ipl_match()
        if result is not None:
            team1, team2, discovered_cb_id = result
            if discovered_cb_id in completed:
                print(f"Match {discovered_cb_id} already completed — skipping, looking for next...")
                result = None
            else:
                print(f"Found live match: {team1} vs {team2}  (cb_id={discovered_cb_id})")
                if args.cb_id is None:
                    args.cb_id = discovered_cb_id
        if result is None:
            # No match live right now — sleep until the next scheduled one
            t1 = args.team1.upper() if args.team1 else None
            t2 = args.team2.upper() if args.team2 else None
            next_match = find_next_match(t1, t2) if (t1 and t2) else find_next_ipl_match()

            if next_match:
                secs = seconds_until_match(next_match, pre_buffer_mins=_PRE_MATCH_BUFFER_MINS)
                if secs > 60:
                    print(f"Next match: {format_match(next_match)}")
                    print(
                        f"Sleeping until {_PRE_MATCH_BUFFER_MINS} min before start "
                        f"({secs/3600:.1f}h from now)..."
                    )
                    # Sleep in 5-minute chunks so PC wake-from-sleep doesn't throw off timing
                    while True:
                        remaining = seconds_until_match(next_match, pre_buffer_mins=_PRE_MATCH_BUFFER_MINS)
                        if remaining <= 0:
                            break
                        time.sleep(min(300, remaining))
                    print("Waking up — starting live discovery loop...")
                else:
                    print(f"Next match starting soon: {format_match(next_match)}")
            else:
                print("No schedule data — falling back to 60s retry loop.")

            # Live discovery loop (runs once match window is near)
            print("Auto-discovering live IPL match...")
            result = None
            while result is None:
                result = client.find_live_ipl_match()
                if result is None:
                    print(f"Not live yet — retrying in {_DISCOVERY_RETRY_SECS}s...")
                    time.sleep(_DISCOVERY_RETRY_SECS)
            team1, team2, discovered_cb_id = result
            print(f"Found: {team1} vs {team2}  (cb_id={discovered_cb_id})")
            if args.cb_id is None:
                args.cb_id = discovered_cb_id

    match_id = args.match_id or (
        f"{team1.lower()}_vs_{team2.lower()}_{date.today().isoformat()}"
    )
    return team1, team2, match_id


def main(argv=None) -> None:
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        stream=sys.stderr,
    )

    skip_cb_id = None
    while True:
        team1, team2, match_id = _resolve_match(args, skip_cb_id=skip_cb_id)

        poller = LivePoller(
            match_id=match_id,
            team1=team1,
            team2=team2,
            poll_interval=args.poll_interval,
            engine_url=args.engine_url,
            cb_id=args.cb_id,
        )
        poller.run()

        # Match finished — persist and skip this cb_id on next discovery
        skip_cb_id = poller._cb_id
        label = f"{team1} vs {team2} {date.today().isoformat()}"
        _save_completed_cb_id(skip_cb_id, label)
        print(f"\n[run_live] Match complete ({label}, cb_id={skip_cb_id}). Cooling down 10 min before next discovery...\n")
        time.sleep(600)
        args.team1    = None
        args.team2    = None
        args.cb_id    = None
        args.match_id = None


if __name__ == "__main__":
    main()
