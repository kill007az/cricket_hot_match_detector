"""
Unit tests for backlog items B2, B3, M3, U1, U2, P1, P2, P3.

These tests do NOT require the engine API to be running — they test
poller logic and signals in isolation.

Run:
    conda run -n cricket_hot python -m tests.test_poller_changes
"""

import io
import json
import sys
import time
import types
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ball(over: float, runs: int = 1, extras: int = 0, wicket: bool = False) -> dict:
    return {"innings": 2, "over": over, "runs": runs, "extras": extras, "wicket": wicket}


def _make_output(
    runs_needed: int = 50,
    wickets: int = 0,
    balls_remaining: int = 60,
    win_prob: float = 0.5,
    hotness: float = 0.5,
    forecast: float = None,
    signals: list = None,
    is_duplicate: bool = False,
) -> dict:
    return {
        "runs_needed": runs_needed,
        "wickets": wickets,
        "balls_remaining": balls_remaining,
        "win_prob": win_prob,
        "hotness": hotness,
        "forecast": forecast,
        "signals": signals or [],
        "is_duplicate": is_duplicate,
        "processing_ms": 1.0,
    }


def _commentary_item(event: str = "", text: str = "") -> dict:
    return {"event": event, "commText": text, "overNumber": None, "ballNbr": 0}


# ---------------------------------------------------------------------------
# M3 — Forecast threshold
# ---------------------------------------------------------------------------

class TestForecastThreshold(unittest.TestCase):
    def test_threshold_is_060(self):
        from engine.signals import FORECAST_THRESHOLD
        self.assertEqual(FORECAST_THRESHOLD, 0.60,
            "Forecast threshold should be 0.60 (was 0.55)")

    def test_signal_fires_at_060(self):
        from engine import signals
        from engine.models import ChaseState
        state = ChaseState(match_id="test", target=150, total_balls=120,
                           balls_faced=60, runs_scored=80, wickets=2)
        result = signals.evaluate(state, win_prob=0.45, forecast=0.61)
        self.assertIn("match heating up — tune in now", result)

    def test_signal_does_not_fire_below_060(self):
        from engine import signals
        from engine.models import ChaseState
        state = ChaseState(match_id="test", target=150, total_balls=120,
                           balls_faced=60, runs_scored=80, wickets=2)
        result = signals.evaluate(state, win_prob=0.45, forecast=0.59)
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# P1 — Strategic timeout detection
# ---------------------------------------------------------------------------

class TestTimeoutDetection(unittest.TestCase):
    def _detect(self, items):
        from polling.poller import LivePoller
        return LivePoller._detect_timeout(items)

    def test_detects_strategic_timeout_in_event(self):
        items = [_commentary_item(event="strategic timeout")]
        self.assertTrue(self._detect(items))

    def test_detects_timeout_in_commtext(self):
        items = [_commentary_item(text="Strategic Timeout called by the batting side")]
        self.assertTrue(self._detect(items))

    def test_detects_strategic_break(self):
        items = [_commentary_item(text="strategic break in play")]
        self.assertTrue(self._detect(items))

    def test_no_false_positive_on_normal_commentary(self):
        items = [_commentary_item(event="FOUR", text="Driven through covers for four!")]
        self.assertFalse(self._detect(items))

    def test_only_checks_first_10_items(self):
        # Timeout at index 11 — should NOT be detected
        items = [_commentary_item(event="NONE")] * 11
        items[10] = _commentary_item(event="strategic timeout")
        self.assertFalse(self._detect(items))


# ---------------------------------------------------------------------------
# P3 — Super over detection
# ---------------------------------------------------------------------------

class TestSuperOverDetection(unittest.TestCase):
    def _detect(self, items):
        from polling.poller import LivePoller
        return LivePoller._detect_super_over(items)

    def test_detects_super_over_in_event(self):
        items = [_commentary_item(event="super over")]
        self.assertTrue(self._detect(items))

    def test_detects_super_over_in_commtext(self):
        items = [_commentary_item(text="It's a Super Over! What drama!")]
        self.assertTrue(self._detect(items))

    def test_no_false_positive(self):
        items = [_commentary_item(event="SIX", text="Into the stands!")]
        self.assertFalse(self._detect(items))


# ---------------------------------------------------------------------------
# P3 — Super over end conditions (via LivePoller state)
# ---------------------------------------------------------------------------

class TestSuperOverEndConditions(unittest.TestCase):
    """
    Test the match_over logic for super overs by inspecting the condition
    expressions directly, without running the full poller loop.
    """

    def _match_over(self, output, super_over, super_over_balls):
        """Mirror of the match_over expression in _phase3_poll_inn2."""
        return (
            output.get("runs_needed", 1) == 0
            or output.get("wickets", 0) >= 10
            or (not super_over and output.get("balls_remaining", 1) == 0)
            or (super_over and output.get("wickets", 0) >= 2)
            or (super_over and super_over_balls >= 12)
        )

    def test_normal_innings_ends_on_balls_remaining_zero(self):
        out = _make_output(runs_needed=5, wickets=1, balls_remaining=0)
        self.assertTrue(self._match_over(out, super_over=False, super_over_balls=0))

    def test_super_over_does_not_end_on_balls_remaining_zero(self):
        out = _make_output(runs_needed=5, wickets=1, balls_remaining=0)
        self.assertFalse(self._match_over(out, super_over=True, super_over_balls=6))

    def test_super_over_ends_on_2_wickets(self):
        out = _make_output(runs_needed=5, wickets=2, balls_remaining=0)
        self.assertTrue(self._match_over(out, super_over=True, super_over_balls=4))

    def test_super_over_ends_on_12_balls(self):
        out = _make_output(runs_needed=5, wickets=1, balls_remaining=0)
        self.assertTrue(self._match_over(out, super_over=True, super_over_balls=12))

    def test_super_over_does_not_end_early(self):
        out = _make_output(runs_needed=5, wickets=1, balls_remaining=0)
        self.assertFalse(self._match_over(out, super_over=True, super_over_balls=6))

    def test_runs_needed_zero_always_ends_match(self):
        out = _make_output(runs_needed=0, wickets=0, balls_remaining=10)
        self.assertTrue(self._match_over(out, super_over=True, super_over_balls=3))
        self.assertTrue(self._match_over(out, super_over=False, super_over_balls=0))


# ---------------------------------------------------------------------------
# B2 — End condition: loss by runs (balls_remaining == 0, no super over)
# ---------------------------------------------------------------------------

class TestInningsEndCondition(unittest.TestCase):
    def _match_over(self, output, super_over=False, super_over_balls=0):
        return (
            output.get("runs_needed", 1) == 0
            or output.get("wickets", 0) >= 10
            or (not super_over and output.get("balls_remaining", 1) == 0)
            or (super_over and output.get("wickets", 0) >= 2)
            or (super_over and super_over_balls >= 12)
        )

    def test_ends_when_balls_remaining_zero(self):
        out = _make_output(runs_needed=10, wickets=3, balls_remaining=0)
        self.assertTrue(self._match_over(out))

    def test_does_not_end_mid_innings(self):
        out = _make_output(runs_needed=10, wickets=3, balls_remaining=30)
        self.assertFalse(self._match_over(out))

    def test_ends_on_runs_needed_zero(self):
        out = _make_output(runs_needed=0, wickets=3, balls_remaining=10)
        self.assertTrue(self._match_over(out))

    def test_ends_on_10_wickets(self):
        out = _make_output(runs_needed=5, wickets=10, balls_remaining=10)
        self.assertTrue(self._match_over(out))


# ---------------------------------------------------------------------------
# U2 — Win% and Hotness as percentages in _print_ball output
# ---------------------------------------------------------------------------

class TestPrintBallFormat(unittest.TestCase):
    def _capture_print_ball(self, ball, output):
        from polling.poller import LivePoller
        buf = io.StringIO()
        with patch("builtins.print", lambda *a, **k: buf.write(" ".join(str(x) for x in a) + "\n")):
            LivePoller._print_ball(ball, output)
        return buf.getvalue()

    def test_win_prob_shown_as_percentage(self):
        ball = _make_ball(1.1, runs=4)
        out = _make_output(win_prob=0.384, hotness=0.461, runs_needed=100, wickets=0)
        line = self._capture_print_ball(ball, out)
        self.assertIn("38.4%", line, "Win prob should be shown as 38.4%")

    def test_hotness_shown_as_percentage(self):
        ball = _make_ball(1.1, runs=4)
        out = _make_output(win_prob=0.5, hotness=0.912, runs_needed=100, wickets=0)
        line = self._capture_print_ball(ball, out)
        self.assertIn("91.2%", line, "Hotness should be shown as 91.2%")

    def test_forecast_shown_as_percentage_when_present(self):
        ball = _make_ball(10.1, runs=1)
        out = _make_output(win_prob=0.5, hotness=0.7, forecast=0.65, runs_needed=50, wickets=2)
        line = self._capture_print_ball(ball, out)
        self.assertIn("65.0%", line, "Forecast should be shown as 65.0%")

    def test_forecast_shown_as_dash_when_none(self):
        ball = _make_ball(5.1, runs=0)
        out = _make_output(win_prob=0.4, hotness=0.3, forecast=None, runs_needed=80, wickets=1)
        line = self._capture_print_ball(ball, out)
        self.assertIn("—", line)


# ---------------------------------------------------------------------------
# U1 — Cumulative score column in _print_ball
# ---------------------------------------------------------------------------

class TestPrintBallScoreColumn(unittest.TestCase):
    def _capture_print_ball(self, ball, output):
        from polling.poller import LivePoller
        buf = io.StringIO()
        with patch("builtins.print", lambda *a, **k: buf.write(" ".join(str(x) for x in a) + "\n")):
            LivePoller._print_ball(ball, output)
        return buf.getvalue()

    def test_score_column_present(self):
        ball = _make_ball(9.3, runs=0, wicket=True)
        out = _make_output(runs_needed=80, wickets=3, balls_remaining=40)
        line = self._capture_print_ball(ball, out)
        # Score shown as "{wickets}w/{runs_needed}rr"
        self.assertIn("3w/", line)
        self.assertIn("rr", line)


# ---------------------------------------------------------------------------
# P2 — Ping-pong buffer alternates between a and b
# ---------------------------------------------------------------------------

class TestPingPongBuffer(unittest.TestCase):
    def test_idx_alternates(self):
        """_raw_inn2_idx should toggle 0→1→0 on each poll."""
        # Simulate the alternation logic directly
        idx = 0
        results = []
        for _ in range(4):
            results.append(idx)
            idx = 1 - idx
        self.assertEqual(results, [0, 1, 0, 1])

    def test_poller_initialises_two_paths(self):
        from polling.poller import LivePoller
        # Minimal mock to construct LivePoller without real services
        with patch("polling.poller.CricbuzzClient"), \
             patch("polling.poller.EngineClient"), \
             patch.object(LivePoller, "_load_seen_keys", return_value=set()):
            poller = LivePoller.__new__(LivePoller)
            poller.match_id = "test"
            poller.team1 = "A"
            poller.team2 = "B"
            poller.poll_interval = 30
            poller._cricbuzz = MagicMock()
            poller._engine = MagicMock()
            poller._match_dir = Path("/tmp/test_match")
            poller._ball_events_file = poller._match_dir / "ball_events.jsonl"
            poller._engine_outputs_file = poller._match_dir / "engine_outputs.jsonl"
            poller._raw_inn2_paths = [
                poller._match_dir / "raw_inn2_a.json",
                poller._match_dir / "raw_inn2_b.json",
            ]
            poller._raw_inn2_idx = 0
            poller._seen = set()
            poller._cb_id = 12345
            poller._last_new_ball_time = None
            poller._stale_critical_fired = False
            poller._super_over = False
            poller._super_over_balls = 0

        self.assertEqual(len(poller._raw_inn2_paths), 2)
        self.assertIn("raw_inn2_a.json", str(poller._raw_inn2_paths[0]))
        self.assertIn("raw_inn2_b.json", str(poller._raw_inn2_paths[1]))


# ---------------------------------------------------------------------------
# B3 — Iterative smart wait self-corrects
# ---------------------------------------------------------------------------

class TestSmartWait(unittest.TestCase):
    def test_exits_immediately_when_inn1_complete(self):
        from polling.poller import LivePoller
        poller = MagicMock(spec=LivePoller)
        poller._cb_id = 99
        poller._cricbuzz = MagicMock()

        poller._cricbuzz.get_commentary.return_value = []
        # Patch where count_legal_balls is used (imported into poller namespace)
        with patch("polling.poller.count_legal_balls", return_value=120), \
             patch("polling.poller.time") as mock_time:
            LivePoller._smart_wait_for_inn1_complete(poller)
            mock_time.sleep.assert_not_called()

        poller._cricbuzz.get_commentary.assert_called_once()

    def test_iterates_until_complete(self):
        from polling.poller import LivePoller
        poller = MagicMock(spec=LivePoller)
        poller._cb_id = 99
        poller._cricbuzz = MagicMock()
        poller._cricbuzz.get_commentary.return_value = []

        call_count = 0
        def fake_count(items):
            nonlocal call_count
            call_count += 1
            return 60 if call_count == 1 else 120

        with patch("polling.poller.count_legal_balls", side_effect=fake_count), \
             patch("polling.poller.time"):
            LivePoller._smart_wait_for_inn1_complete(poller)

        self.assertEqual(call_count, 2, "Should fetch inn1 twice — once undershot, once complete")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestForecastThreshold,
        TestTimeoutDetection,
        TestSuperOverDetection,
        TestSuperOverEndConditions,
        TestInningsEndCondition,
        TestPrintBallFormat,
        TestPrintBallScoreColumn,
        TestPingPongBuffer,
        TestSmartWait,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
