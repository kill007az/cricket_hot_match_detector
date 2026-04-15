"""
engine_client.py — HTTP client for the Cricket Hot Match Engine API.

Wraps the three endpoints used by the poller:
    POST /match/init              → initialise a match session
    POST /match/{match_id}/ball   → process one legal delivery
    GET  /match/{match_id}/state  → fetch current chase state

Uses a persistent requests.Session (keep-alive) to avoid per-ball TCP
reconnect overhead — the same pattern as tests/simulate_hot_match.py.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)


class EngineClient:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self._base = base_url.rstrip("/")
        self._session = requests.Session()

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def is_alive(self, timeout: int = 3) -> bool:
        """Return True if the engine API is reachable."""
        try:
            self._session.get(f"{self._base}/docs", timeout=timeout)
            return True
        except requests.exceptions.ConnectionError:
            return False

    def is_match_known(self, match_id: str) -> bool:
        """Return True if the engine already has an active session for match_id."""
        try:
            resp = self._session.get(f"{self._base}/match/{match_id}/state", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Match lifecycle
    # ------------------------------------------------------------------

    def init_match(self, match_id: str, target: int, total_balls: int) -> dict:
        """
        Initialise a match session.

        target:      inn1 runs + 1
        total_balls: actual legal deliveries in inn1 (NOT overs * 6)
        """
        resp = self._session.post(
            f"{self._base}/match/init",
            json={
                "match_id":    match_id,
                "target":      target,
                "total_balls": total_balls,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Per-ball processing
    # ------------------------------------------------------------------

    def send_ball(self, match_id: str, ball: dict) -> dict:
        """
        Send one legal delivery to the engine.

        ball must be a dict with keys: {innings, over, runs, extras, wicket}
        matching the BallEventRequest schema in api/routes.py.

        Returns the EngineOutputResponse dict.
        """
        resp = self._session.post(
            f"{self._base}/match/{match_id}/ball",
            json=ball,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # State query
    # ------------------------------------------------------------------

    def get_state(self, match_id: str) -> dict:
        """Return the current MatchStateResponse dict for a match."""
        resp = self._session.get(
            f"{self._base}/match/{match_id}/state",
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
