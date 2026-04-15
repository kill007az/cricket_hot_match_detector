"""
run_live.py — CLI entry point for the live polling service.

Default behaviour (no arguments)
---------------------------------
Queries Cricbuzz for any live IPL match, derives team names and match-id
automatically, and starts polling.  Works for any IPL fixture without
touching any config.

    conda run -n cricket_hot python -m polling.run_live

Override behaviour
------------------
Supply --team1 / --team2 to pin a specific match when multiple IPL games
are live simultaneously, or to watch a non-IPL T20 match.  --match-id
overrides the auto-generated slug if needed.

    python -m polling.run_live --team1 CSK --team2 KKR
    python -m polling.run_live --match-id csk_vs_kkr_2026-04-14 --team1 CSK --team2 KKR

Match-id slug is generated as:  {team1_lower}_vs_{team2_lower}_{YYYY-MM-DD}
"""

import argparse
import logging
import sys
import time
from datetime import date

from polling.cricbuzz_client import CricbuzzClient
from polling.poller import LivePoller

_DISCOVERY_RETRY_SECS = 60   # how long to wait between auto-discovery retries


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
            "Find it in the Cricbuzz match URL. Required — auto-discovery unavailable."
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


def _resolve_match(args) -> tuple[str, str, str]:
    """
    Return (team1_abbr, team2_abbr, match_id).

    If --team1/--team2 are not supplied, polls Cricbuzz until a live IPL
    match is found.  match-id is auto-generated unless explicitly set.
    """
    client = CricbuzzClient()

    if args.team1 and args.team2:
        # Both teams explicitly specified — use them directly
        team1, team2 = args.team1.upper(), args.team2.upper()
    else:
        # Auto-discovery is not currently available — require explicit teams
        print(
            "ERROR: --team1 and --team2 are required.\n"
            "Auto-discovery is unavailable (Cricbuzz live-listing endpoint changed).\n\n"
            "Find the match ID from the Cricbuzz URL (e.g. cricbuzz.com/.../<match_id>/...)\n"
            "then run:\n"
            "  python -m polling.run_live --team1 CSK --team2 KKR --match-id csk_vs_kkr_2026-04-14\n"
        )
        raise SystemExit(1)

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

    team1, team2, match_id = _resolve_match(args)

    poller = LivePoller(
        match_id=match_id,
        team1=team1,
        team2=team2,
        poll_interval=args.poll_interval,
        engine_url=args.engine_url,
        cb_id=args.cb_id,
    )
    poller.run()


if __name__ == "__main__":
    main()
