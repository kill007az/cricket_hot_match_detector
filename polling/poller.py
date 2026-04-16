"""
LivePoller — fully automated, no-touch polling loop.

Flow
----
Phase 1  Find match
    Poll the Cricbuzz live-match listing until the target match appears.

Phase 2  Wait for 2nd innings to start
    Fetch inn1 commentary once to count balls already bowled.
    Sleep ~35s × remaining balls (smart wait) to avoid polling during inn1.
    Then poll inn2 every 5 minutes until at least one legal ball appears.
    Moves to Phase 2.5 as soon as inn2 has started.

Phase 2.5  One-shot inn1 summary fetch
    Fetch the now-complete inn1 commentary in a single blocking call.
    Count legal balls and sum runs → POST /match/init to the engine.
    Raw inn1 commentary saved to disk.

Phase 3  Live inn2 polling loop
    Fetch inn2 commentary every poll_interval seconds.
    Parse all legal balls; send only NEW ones (unseen oversId keys) to engine.
    For each new ball: print a formatted status line and append to JSONL files.
    Stop automatically when wickets == 10 OR runs_needed == 0 (match over).

Stale data detection
    During Phase 3, if no new ball is received for _STALE_WARN_SECS seconds
    a WARNING is logged (network hiccup / Cricbuzz lag / drinks break).
    If silence continues past _STALE_CRITICAL_SECS a second louder warning fires.
    Counters reset as soon as a new ball arrives.

Data persistence  (data/live_polls/{match_id}/)
    raw_inn1_{HHMMSS}.json      full inn1 Cricbuzz response (one-shot)
    raw_inn2_a.json             ping-pong buffer A — latest inn2 Cricbuzz response
    raw_inn2_b.json             ping-pong buffer B — previous inn2 Cricbuzz response
    ball_events.jsonl           one sent BallEvent dict per line
    engine_outputs.jsonl        one EngineOutput dict per line
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from polling.adapter import ball_key, count_legal_balls, parse_legal_balls, sum_innings_runs
from polling.cricbuzz_client import CricbuzzClient
from polling.engine_client import EngineClient

logger = logging.getLogger(__name__)

_DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "live_polls"

# Stale-data thresholds (seconds without a new ball during active Phase 3)
_STALE_WARN_SECS     = 180   # 3 min  → WARNING
_STALE_CRITICAL_SECS = 600   # 10 min → second louder warning

# Strategic timeout suppression — detected via commentary text, not fixed overs.
# When a timeout item is found in commentary, the poller sleeps _TIMEOUT_SLEEP_SECS
# before resuming normal polling. Avoids spurious stale-data warnings.
_TIMEOUT_KEYWORDS    = {"strategic timeout", "timeout", "strategic break"}
_TIMEOUT_SLEEP_SECS  = 150   # 2.5 min timeout + small buffer

# Smart Phase 2 wait
_BALL_DURATION_SECS = 35   # approximate seconds per legal delivery
_INN1_TOTAL_BALLS   = 120  # standard T20; used to estimate remaining balls


class LivePoller:
    def __init__(
        self,
        match_id: str,
        team1: str,
        team2: str,
        poll_interval: int = 30,
        engine_url: str = "http://localhost:8000",
        cb_id: Optional[int] = None,
    ):
        self.match_id = match_id
        self.team1 = team1
        self.team2 = team2
        self.poll_interval = poll_interval

        self._cricbuzz = CricbuzzClient()
        self._engine = EngineClient(base_url=engine_url)

        self._match_dir = _DATA_ROOT / match_id
        self._match_dir.mkdir(parents=True, exist_ok=True)

        self._ball_events_file = self._match_dir / "ball_events.jsonl"
        self._engine_outputs_file = self._match_dir / "engine_outputs.jsonl"

        # Ping-pong buffer for raw inn2 responses (avoids accumulating 200+ files)
        self._raw_inn2_paths = [
            self._match_dir / "raw_inn2_a.json",
            self._match_dir / "raw_inn2_b.json",
        ]
        self._raw_inn2_idx: int = 0  # alternates 0/1 each poll

        # Set of ball_key strings already sent to the engine this session.
        # Populated from any existing ball_events.jsonl so the poller can
        # resume cleanly if restarted mid-match.
        self._seen: set[str] = self._load_seen_keys()

        # Cricbuzz integer match ID — provided directly or resolved in Phase 1
        self._cb_id: Optional[int] = cb_id

        # Stale-data tracking (Phase 3 only)
        self._last_new_ball_time: Optional[float] = None  # time.monotonic()
        self._stale_critical_fired: bool = False
        self._super_over: bool = False   # set True if super over detected
        self._super_over_balls: int = 0  # legal balls counted since super over started

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._check_engine()

        print(f"\n{'─'*65}")
        print(f"  Cricket Hot Match Detector — Live Poller")
        print(f"  Match:    {self.match_id}")
        print(f"  Teams:    {self.team1} vs {self.team2}")
        print(f"  Interval: {self.poll_interval}s")
        print(f"  Data dir: {self._match_dir}")
        print(f"{'─'*65}\n")

        self._phase1_find_match()
        self._phase2_wait_for_inn2()
        self._phase3_poll_inn2()

    # ------------------------------------------------------------------
    # Phase 1: find match
    # ------------------------------------------------------------------

    def _phase1_find_match(self) -> None:
        if self._cb_id is not None:
            print(f"[Phase 1] Using provided Cricbuzz ID = {self._cb_id}\n")
            return

        print(f"[Phase 1] Auto-discovering live match: {self.team1} vs {self.team2} ...")
        while True:
            cb_id = self._cricbuzz.find_live_match(self.team1, self.team2)
            if cb_id is not None:
                self._cb_id = cb_id
                print(f"[Phase 1] Found: cb_id = {self._cb_id}\n")
                return
            print(f"[Phase 1] Match not live yet — retrying in {self.poll_interval}s...")
            time.sleep(self.poll_interval)

    # ------------------------------------------------------------------
    # Phase 2: wait for 2nd innings
    # ------------------------------------------------------------------

    def _phase2_wait_for_inn2(self) -> None:
        print("[Phase 2] Waiting for 2nd innings to start...")

        # Iterative smart wait: keep re-fetching inn1 and sleeping until it's
        # done. Self-corrects if each estimate undershoots.
        self._smart_wait_for_inn1_complete()
        print("[Phase 2] Smart wait done — starting inn2 poll loop...")

        # Regular polling loop (handles overshoot, rain, called-off, etc.)
        # Poll every 5 minutes here — we only need to detect inn2 starting,
        # and missing a ball or two at the very start is acceptable.
        _INN2_WAIT_SECS = 300
        while True:
            items = self._cricbuzz.get_commentary(self._cb_id, innings=2)
            legal_balls = parse_legal_balls(items, innings=2)
            if legal_balls:
                print(f"[Phase 2] 2nd innings has started ({len(legal_balls)} ball(s) so far)\n")
                self._phase25_init_from_inn1()
                return
            print(f"[Phase 2] Inn2 not started yet — retrying in {_INN2_WAIT_SECS}s...")
            time.sleep(_INN2_WAIT_SECS)

    def _smart_wait_for_inn1_complete(self) -> None:
        """
        Iteratively fetch inn1, sleep remaining_balls × 35s, repeat until
        inn1 is complete (bowled >= _INN1_TOTAL_BALLS). Each iteration
        re-estimates remaining time so any undershoot self-corrects.
        Falls through immediately if inn1 is already done or on fetch error.
        """
        iteration = 0
        while True:
            try:
                items = self._cricbuzz.get_commentary(self._cb_id, innings=1)
                bowled = count_legal_balls(items)
                remaining = max(0, _INN1_TOTAL_BALLS - bowled)
            except Exception as exc:
                logger.warning(
                    "Could not fetch inn1 commentary (%s) — skipping smart wait", exc
                )
                return

            if remaining == 0:
                if iteration == 0:
                    print(f"[Phase 2] Inn1 already complete ({bowled} balls) — polling immediately")
                else:
                    print(f"[Phase 2] Inn1 complete ({bowled} balls) — proceeding to inn2 poll")
                return

            secs = remaining * _BALL_DURATION_SECS
            mins = secs / 60
            iteration += 1
            print(
                f"[Phase 2] Inn1: {bowled} balls bowled, ~{remaining} remaining"
                f" — sleeping {secs}s ({mins:.0f}m) [iteration {iteration}]"
            )
            time.sleep(secs)

    # ------------------------------------------------------------------
    # Phase 2.5: one-shot inn1 summary fetch → engine init
    # ------------------------------------------------------------------

    def _phase25_init_from_inn1(self) -> None:
        # Skip if we already initialised this match (poller restart mid-match)
        if self._engine.is_match_known(self.match_id):
            print("[Phase 2.5] Match already initialised in engine — skipping inn1 fetch\n")
            return

        print("[Phase 2.5] Fetching inn1 summary (one-shot)...")
        inn1_items = self._cricbuzz.get_commentary(self._cb_id, innings=1)

        # Persist raw inn1 response
        ts = _timestamp()
        raw_path = self._match_dir / f"raw_inn1_{ts}.json"
        raw_path.write_text(json.dumps(inn1_items, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[Phase 2.5] Inn1 raw commentary saved → {raw_path.name}")

        inn1_runs = sum_innings_runs(inn1_items)
        total_balls = count_legal_balls(inn1_items)
        target = inn1_runs + 1

        print(f"[Phase 2.5] Inn1: {inn1_runs} runs  |  {total_balls} legal balls  |  Target: {target}")

        result = self._engine.init_match(
            match_id=self.match_id,
            target=target,
            total_balls=total_balls,
        )
        print(f"[Phase 2.5] Engine init → {result.get('message', 'OK')}\n")

    # ------------------------------------------------------------------
    # Phase 3: live inn2 polling loop
    # ------------------------------------------------------------------

    def _phase3_poll_inn2(self) -> None:
        print("[Phase 3] Live inn2 polling loop started\n")
        self._last_new_ball_time = time.monotonic()  # start the stale clock from now
        print(
            f"  {'Over':<6}  {'Score':>7}  {'Runs':>4}  {'Xtr':>4}  {'Wkt':>4}  "
            f"{'Win%':>6}  {'Hot':>6}  {'Fcast':>6}  Signals"
        )
        print(f"  {'─'*78}")

        while True:
            items = self._cricbuzz.get_commentary(self._cb_id, innings=2)

            # Persist raw response — ping-pong between two files
            raw_path = self._raw_inn2_paths[self._raw_inn2_idx]
            raw_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
            self._raw_inn2_idx = 1 - self._raw_inn2_idx

            legal_balls = parse_legal_balls(items, innings=2)
            new_balls = [b for b in legal_balls if ball_key(b) not in self._seen]

            if new_balls:
                # Reset stale tracking whenever fresh data arrives
                self._last_new_ball_time = time.monotonic()
                self._stale_critical_fired = False

                for ball in new_balls:
                    bk = ball_key(ball)
                    output = self._engine.send_ball(self.match_id, ball)

                    self._seen.add(bk)
                    self._append_jsonl(self._ball_events_file, ball)
                    self._append_jsonl(self._engine_outputs_file, output)
                    self._print_ball(ball, output)

                    if self._super_over:
                        self._super_over_balls += 1

                    # Normal innings end (no super over in play)
                    if not self._super_over and (
                        output.get("runs_needed", 1) == 0
                        or output.get("wickets", 0) >= 10
                        or output.get("balls_remaining", 1) == 0
                    ):
                        print(f"\n[Phase 3] Match over — final state:")
                        self._print_final(output)
                        return

                    # Super over in play: end when decisive result or 12 balls done
                    if self._super_over and (
                        output.get("runs_needed", 1) == 0
                        or output.get("wickets", 0) >= 2
                        or self._super_over_balls >= 12
                    ):
                        if output.get("runs_needed", 1) == 0:
                            # Decisive result — match over
                            print(f"\n[Phase 3] Match over (super over) — final state:")
                            self._print_final(output)
                            return
                        else:
                            # Another tie — reset for next super over
                            print("[Phase 3] Super over tied — another super over incoming...")
                            self._super_over_balls = 0
            else:
                logger.debug("No new balls this poll (seen=%d total)", len(self._seen))
                if not self._super_over and self._detect_super_over(items):
                    self._super_over = True
                    print("[Phase 3] Super over detected — continuing to poll...")
                if self._detect_timeout(items):
                    print(f"[Phase 3] Strategic timeout detected — sleeping {_TIMEOUT_SLEEP_SECS}s...")
                    time.sleep(_TIMEOUT_SLEEP_SECS)
                    continue
                self._check_stale()

            time.sleep(self.poll_interval)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_super_over(items: list[dict]) -> bool:
        """Return True if any recent commentary item mentions a super over."""
        for item in items[:10]:
            event = str(item.get("event", "")).lower()
            text = str(item.get("commText", item.get("commentary", ""))).lower()
            if "super over" in event or "super over" in text:
                return True
        return False

    @staticmethod
    def _detect_timeout(items: list[dict]) -> bool:
        """Return True if any recent commentary item mentions a strategic timeout."""
        for item in items[:10]:  # only check the most recent items
            event = str(item.get("event", "")).lower()
            text = str(item.get("commText", item.get("commentary", ""))).lower()
            for keyword in _TIMEOUT_KEYWORDS:
                if keyword in event or keyword in text:
                    return True
        return False

    def _check_stale(self) -> None:
        """Log warnings if no new ball has been received for too long."""
        if self._last_new_ball_time is None:
            return

        elapsed = time.monotonic() - self._last_new_ball_time
        if elapsed >= _STALE_CRITICAL_SECS and not self._stale_critical_fired:
            logger.warning(
                "STALE DATA: no new ball received for %.0f minutes — "
                "possible Cricbuzz outage, network issue, or extended stoppage. "
                "match_id=%s  seen_balls=%d",
                elapsed / 60, self.match_id, len(self._seen),
            )
            self._stale_critical_fired = True
        elif _STALE_WARN_SECS <= elapsed < _STALE_CRITICAL_SECS:
            logger.warning(
                "No new ball received for %.0f minutes — "
                "Cricbuzz may be delayed or match is in a break. "
                "match_id=%s  seen_balls=%d",
                elapsed / 60, self.match_id, len(self._seen),
            )

    def _check_engine(self) -> None:
        if not self._engine.is_alive():
            print(
                "ERROR: Engine API not reachable at http://localhost:8000\n"
                "Start it with:  uvicorn engine.server:app --port 8000"
            )
            sys.exit(1)
        print("[OK] Engine API is up\n")

    def _load_seen_keys(self) -> set[str]:
        """Load previously sent ball keys from ball_events.jsonl (resume support)."""
        seen: set[str] = set()
        if self._ball_events_file.exists():
            for line in self._ball_events_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        b = json.loads(line)
                        seen.add(ball_key(b))
                    except Exception:
                        pass
        if seen:
            print(f"[Resume] Loaded {len(seen)} previously sent ball(s) from {self._ball_events_file.name}")
        return seen

    @staticmethod
    def _append_jsonl(path: Path, obj: dict) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    @staticmethod
    def _print_ball(ball: dict, output: dict) -> None:
        over_str = f"{int(ball['over'])}.{round((ball['over'] % 1) * 10):.0f}"
        wkt_str = "YES" if ball["wicket"] else "no"
        fc = output.get("forecast")
        fc_str = f"{fc*100:5.1f}%" if fc is not None else "   —  "
        sigs = " | ".join(output.get("signals", [])) if output.get("signals") else ""
        dup = " [dup]" if output.get("is_duplicate") else ""

        # Cumulative score: derived from runs_needed + balls_remaining in output
        wickets = output.get("wickets", 0)
        runs_needed = output.get("runs_needed", 0)
        balls_remaining = output.get("balls_remaining", 0)
        # target = runs_needed + runs_scored; we don't store target here so use
        # balls_remaining to infer total_balls and derive runs_scored indirectly.
        # Simplest: engine returns wickets directly, and we can show W/R-needed.
        score_str = f"{wickets}w/{runs_needed}rr"

        print(
            f"  {over_str:<6}  "
            f"{score_str:>7}  "
            f"{ball['runs']:>4}  "
            f"{ball['extras']:>4}  "
            f"{wkt_str:>4}  "
            f"{output.get('win_prob', 0)*100:>5.1f}%  "
            f"{output.get('hotness', 0)*100:>5.1f}%  "
            f"{fc_str:>7}  "
            f"{sigs}{dup}"
        )

    @staticmethod
    def _print_final(output: dict) -> None:
        print(
            f"  Balls left:   {output.get('balls_remaining', 0)}\n"
            f"  Runs needed:  {output.get('runs_needed', 0)}\n"
            f"  Wickets:      {output.get('wickets', 0)}\n"
        )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _timestamp() -> str:
    return datetime.now().strftime("%H%M%S")
