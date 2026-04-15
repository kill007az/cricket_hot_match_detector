"""
run.py — single entry point: starts the engine and polling service together.

    conda run -n cricket_hot python run.py

What it does
------------
1. Spawns the FastAPI engine (uvicorn) as a background subprocess.
2. Waits until the engine is healthy (GET /docs returns 200).
3. Auto-discovers the live IPL match from Cricbuzz, or uses the teams
   supplied via --team1 / --team2.
4. Runs the live poller in the foreground until the match ends.
5. On exit (normal finish or Ctrl+C), cleanly terminates the engine process.

Arguments
---------
  --team1 / --team2     Pin a specific match (optional — auto-detected if omitted)
  --match-id            Override the auto-generated slug (optional)
  --poll-interval N     Seconds between Cricbuzz polls (default: 30)
  --port N              Engine API port (default: 8000)
  --log-level           DEBUG / INFO / WARNING / ERROR (default: WARNING)
"""

from __future__ import annotations

import argparse
import logging
import signal
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import requests

from polling.cricbuzz_client import CricbuzzClient
from polling.poller import LivePoller

logger = logging.getLogger(__name__)

_PROJECT_ROOT  = Path(__file__).resolve().parent
_ENGINE_MODULE = "engine.server:app"
_HEALTH_PATH   = "/docs"
_HEALTH_TIMEOUT     = 60   # seconds to wait for engine to become ready
_HEALTH_POLL_SECS   = 2    # how often to check
_DISCOVERY_RETRY_SECS = 60


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python run.py",
        description=(
            "Cricket Hot Match Detector — unified launcher.\n"
            "Starts the engine and live poller in one command."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--team1",         default=None, help="Team 1 abbreviation (e.g. CSK). Required.")
    p.add_argument("--team2",         default=None, help="Team 2 abbreviation (e.g. KKR). Required.")
    p.add_argument("--match-id",      default=None, help="Engine match_id / data folder slug. Auto-generated if omitted.")
    p.add_argument("--cb-id",         type=int, default=None, help="Cricbuzz numeric match ID (e.g. 151763). Find in Cricbuzz match URL. Required.")
    p.add_argument("--poll-interval", type=int, default=30,   help="Seconds between Cricbuzz polls (default: 30)")
    p.add_argument("--port",          type=int, default=8000,  help="Engine API port (default: 8000)")
    p.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: WARNING)",
    )
    return p


# ---------------------------------------------------------------------------
# Engine subprocess management
# ---------------------------------------------------------------------------

def _start_engine(port: int) -> subprocess.Popen:
    """Spawn uvicorn in a background subprocess. Returns the Popen handle."""
    cmd = [
        sys.executable, "-m", "uvicorn",
        _ENGINE_MODULE,
        "--port", str(port),
        "--log-level", "warning",
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(_PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    logger.info("Engine subprocess started (pid=%d)", proc.pid)
    return proc


def _wait_for_engine(port: int) -> None:
    """Block until the engine responds on /docs, or raise TimeoutError."""
    url = f"http://localhost:{port}{_HEALTH_PATH}"
    deadline = time.monotonic() + _HEALTH_TIMEOUT
    print(f"[Engine] Waiting for engine to be ready on port {port}...", end="", flush=True)
    while time.monotonic() < deadline:
        try:
            r = requests.get(url, timeout=2)
            if r.status_code == 200:
                print(" ready.\n")
                return
        except requests.exceptions.ConnectionError:
            pass
        print(".", end="", flush=True)
        time.sleep(_HEALTH_POLL_SECS)
    print()
    raise TimeoutError(
        f"Engine did not become ready within {_HEALTH_TIMEOUT}s. "
        f"Check the process logs."
    )


def _stop_engine(proc: subprocess.Popen) -> None:
    """Gracefully terminate the engine subprocess."""
    if proc.poll() is not None:
        return  # already exited
    print("\n[Engine] Shutting down engine...")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    print("[Engine] Engine stopped.")


# ---------------------------------------------------------------------------
# Match resolution (mirrors run_live.py logic)
# ---------------------------------------------------------------------------

def _resolve_match(args, engine_url: str) -> tuple[str, str, str]:
    """Return (team1_abbr, team2_abbr, match_id)."""
    client = CricbuzzClient()

    if args.team1 and args.team2:
        team1, team2 = args.team1.upper(), args.team2.upper()
    else:
        print(
            "ERROR: --team1 and --team2 are required.\n"
            "Auto-discovery is unavailable (Cricbuzz live-listing endpoint changed).\n\n"
            "Find the match ID from the Cricbuzz URL, then run:\n"
            "  python run.py --team1 CSK --team2 KKR --match-id csk_vs_kkr_2026-04-14\n"
        )
        raise SystemExit(1)

    match_id = args.match_id or f"{team1.lower()}_vs_{team2.lower()}_{date.today().isoformat()}"
    return team1, team2, match_id


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None) -> None:
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        stream=sys.stderr,
    )

    engine_url = f"http://localhost:{args.port}"
    engine_proc: subprocess.Popen | None = None

    # Ensure engine subprocess is always cleaned up
    def _cleanup(signum=None, frame=None):
        if engine_proc is not None:
            _stop_engine(engine_proc)
        sys.exit(0)

    signal.signal(signal.SIGINT,  _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    try:
        # --- Start engine ---
        engine_proc = _start_engine(args.port)
        _wait_for_engine(args.port)

        # --- Resolve match ---
        team1, team2, match_id = _resolve_match(args, engine_url)

        # --- Run poller ---
        poller = LivePoller(
            match_id=match_id,
            team1=team1,
            team2=team2,
            poll_interval=args.poll_interval,
            engine_url=engine_url,
            cb_id=args.cb_id,
        )
        poller.run()

    finally:
        if engine_proc is not None:
            _stop_engine(engine_proc)


if __name__ == "__main__":
    main()
