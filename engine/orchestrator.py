"""
EngineOrchestrator: coordinates the full per-ball pipeline.

Pipeline order (per feature spec §5.8):
  1. Idempotency check  — skip if ball already processed
  2. State update       — StateUpdater
  3. Feature extraction — FeatureExtractor
  4. Win probability    — WinProbModel
  5. Hotness            — HotnessCalculator
  6. Forecast           — HotnessForecaster (gated: ball >= 60 AND >= 12-ball history)
  7. Signal evaluation  — SignalEvaluator
  8. Emit EngineOutput  — commit state, return output

Match sessions are held in memory keyed by match_id.
Models are loaded once at construction time.
"""

import dataclasses
import time
from pathlib import Path
from typing import Dict

from engine import features as feat_mod
from engine import hotness as hotness_mod
from engine import signals as signals_mod
from engine import state as state_mod
from engine.forecaster import HotnessForecaster
from engine.models import (
    BallEvent,
    ChaseState,
    EngineOutput,
    HotnessState,
    MatchSession,
)
from engine.win_prob import WinProbModel


_STEPS = ["state_update", "feature_extract", "win_prob", "hotness", "forecast", "signals"]


class EngineOrchestrator:
    def __init__(self, models_dir: Path):
        self._wp_model = WinProbModel(models_dir / "win_prob_nn.pt")
        self._forecaster = HotnessForecaster(models_dir / "hotness_forecaster.pt")
        self._sessions: Dict[str, MatchSession] = {}

        # Cumulative per-step timing (ms) across all processed balls
        self._step_totals: Dict[str, float] = {s: 0.0 for s in _STEPS}
        self._ball_count: int = 0

    # ------------------------------------------------------------------
    # Match lifecycle
    # ------------------------------------------------------------------

    def init_match(self, match_id: str, target: int, total_balls: int) -> None:
        """
        Initialise (or re-initialise) a match session.

        target:      runs needed to win (innings 1 score + 1)
        total_balls: actual legal balls bowled in innings 1 (NOT info.overs * 6)
        """
        self._sessions[match_id] = MatchSession(
            chase_state=ChaseState(
                match_id=match_id,
                target=target,
                total_balls=total_balls,
            ),
            hotness_state=HotnessState(),
        )

    def has_match(self, match_id: str) -> bool:
        return match_id in self._sessions

    def get_session(self, match_id: str) -> MatchSession:
        if match_id not in self._sessions:
            raise KeyError(f"Match '{match_id}' not initialised. Call init_match first.")
        return self._sessions[match_id]

    # ------------------------------------------------------------------
    # Per-ball processing
    # ------------------------------------------------------------------

    def process_ball(self, event: BallEvent) -> EngineOutput:
        """
        Process one legal delivery and return the engine output.

        Idempotent: duplicate events return the last output unchanged
        with is_duplicate=True.
        """
        session = self.get_session(event.match_id)

        # --- 1. Idempotency ---
        if event.ball_key in session.processed_balls:
            return dataclasses.replace(session.last_output, is_duplicate=True)

        step_ms: Dict[str, float] = {}
        ball_start = time.perf_counter()

        # --- 2. State update ---
        t0 = time.perf_counter()
        new_state = state_mod.update(session.chase_state, event)
        step_ms["state_update"] = (time.perf_counter() - t0) * 1000

        # --- 3. Feature extraction ---
        t0 = time.perf_counter()
        features = feat_mod.extract(new_state)
        step_ms["feature_extract"] = (time.perf_counter() - t0) * 1000

        # --- 4. Win probability ---
        t0 = time.perf_counter()
        win_prob = self._wp_model.predict(features)
        step_ms["win_prob"] = (time.perf_counter() - t0) * 1000

        # --- 5. Hotness ---
        # Pass history BEFORE appending current value (momentum uses [t-6])
        t0 = time.perf_counter()
        hotness = hotness_mod.compute(win_prob, session.hotness_state.win_prob_history)
        session.hotness_state.win_prob_history.append(win_prob)
        session.hotness_state.hotness_history.append(hotness)
        step_ms["hotness"] = (time.perf_counter() - t0) * 1000

        # --- 6. Forecast (gated) ---
        t0 = time.perf_counter()
        forecast = None
        if (
            new_state.balls_faced >= 60
            and len(session.hotness_state.hotness_history) >= 12
        ):
            forecast = self._forecaster.predict(
                session.hotness_state.hotness_history,
                new_state.balls_remaining,
            )
        step_ms["forecast"] = (time.perf_counter() - t0) * 1000

        # --- 7. Signal evaluation ---
        t0 = time.perf_counter()
        fired_signals = signals_mod.evaluate(new_state, win_prob, forecast)
        step_ms["signals"] = (time.perf_counter() - t0) * 1000

        total_ms = (time.perf_counter() - ball_start) * 1000

        # Accumulate for aggregate stats
        for step, ms in step_ms.items():
            self._step_totals[step] += ms
        self._ball_count += 1

        # --- 8. Commit and emit ---
        output = EngineOutput(
            match_id=event.match_id,
            win_prob=win_prob,
            hotness=hotness,
            forecast=forecast,
            runs_needed=new_state.runs_needed,
            balls_remaining=new_state.balls_remaining,
            wickets=new_state.wickets,
            signals=fired_signals,
            processing_ms=total_ms,
        )

        session.chase_state = new_state
        session.processed_balls.add(event.ball_key)
        session.last_output = output

        return output

    # ------------------------------------------------------------------
    # Latency diagnostics
    # ------------------------------------------------------------------

    def get_latency_stats(self) -> dict:
        """Returns per-step mean latency and share across all processed balls."""
        n = max(self._ball_count, 1)
        step_means = {s: self._step_totals[s] / n for s in _STEPS}
        total_mean = sum(step_means.values())
        return {
            "ball_count": self._ball_count,
            "total_mean_ms": round(total_mean, 4),
            "steps": {
                s: {
                    "mean_ms": round(step_means[s], 4),
                    "share_pct": round(100 * step_means[s] / total_mean, 1) if total_mean > 0 else 0,
                }
                for s in _STEPS
            },
        }
