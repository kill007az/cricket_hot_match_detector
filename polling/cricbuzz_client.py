"""
CricbuzzClient: thin HTTP client for Cricbuzz's unofficial JSON API.

Endpoints used:
  GET /api/mcenter/{cb_id}/full-commentary/{innings}
      → ball-by-ball commentary for a given innings (1 or 2).
        Cricbuzz returns items newest-first inside commentaryList; we reverse
        before returning so callers receive chronological order.

  Live match discovery: auto-discovery is not currently supported (the live
  listing endpoint has changed).  Pass --match-id explicitly to run_live.py,
  or check the Cricbuzz match URL for the numeric ID (e.g. 151763).

Note: these endpoints return plain JSON and work without authentication.
Browser-like headers are required to avoid 403s.

Retry / backoff policy
----------------------
All GET requests go through _get_with_retry():

    Retryable:   5xx, ConnectionError, Timeout  →  exponential backoff + jitter
    Rate-limit:  429                             →  fixed 60s backoff before retry
    Hard fail:   4xx (except 429)               →  no retry, raise immediately
    Max retries: 3 attempts total (1 initial + 2 retries)

If all retries are exhausted the exception propagates to the caller.  The poller
is written to treat a failed poll cycle as a soft miss and retry on the next
interval, so a single failed fetch does not abort the match.
"""

from __future__ import annotations

import logging
import random
import re
import time
from typing import Optional

import requests
from requests.exceptions import ConnectionError as ReqConnError
from requests.exceptions import Timeout as ReqTimeout

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------

_MAX_RETRIES   = 3        # total attempts (1 initial + 2 retries)
_BASE_DELAY    = 2.0      # seconds — first retry wait
_MAX_DELAY     = 30.0     # seconds — cap on exponential growth
_JITTER_FACTOR = 0.2      # ±20% random jitter applied to each delay
_RATE_LIMIT_DELAY = 60.0  # seconds to wait after a 429


def _backoff(attempt: int) -> float:
    """Exponential backoff with jitter: base * 2^attempt ± jitter, capped at max."""
    delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
    jitter = delay * _JITTER_FACTOR * (2 * random.random() - 1)  # ±jitter_factor
    return max(0.0, delay + jitter)

# ---------------------------------------------------------------------------
# Team name aliases (common abbreviation → names Cricbuzz uses in team objects)
# ---------------------------------------------------------------------------

TEAM_ALIASES: dict[str, list[str]] = {
    "CSK":  ["Chennai Super Kings", "Chennai"],
    "KKR":  ["Kolkata Knight Riders", "Kolkata"],
    "MI":   ["Mumbai Indians", "Mumbai"],
    "RCB":  ["Royal Challengers Bengaluru", "Royal Challengers Bangalore", "Bengaluru"],
    "DC":   ["Delhi Capitals", "Delhi"],
    "GT":   ["Gujarat Titans", "Gujarat"],
    "RR":   ["Rajasthan Royals", "Rajasthan"],
    "SRH":  ["Sunrisers Hyderabad", "Hyderabad"],
    "LSG":  ["Lucknow Super Giants", "Lucknow"],
    "PBKS": ["Punjab Kings", "Punjab"],
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer":          "https://www.cricbuzz.com/",
    "Accept":           "application/json, text/plain, */*",
    "Accept-Language":  "en-US,en;q=0.9",
    "Accept-Encoding":  "gzip, deflate, br",
    "Origin":           "https://www.cricbuzz.com",
    "sec-fetch-dest":   "empty",
    "sec-fetch-mode":   "cors",
    "sec-fetch-site":   "same-origin",
    "Connection":       "keep-alive",
}

# Minimum seconds between requests — polite pacing to avoid triggering rate limits
_MIN_REQUEST_INTERVAL = 1.0

_BASE = "https://www.cricbuzz.com"


def _aliases(abbr: str) -> list[str]:
    """Return all known name variants for a team abbreviation (case-insensitive lookup)."""
    return TEAM_ALIASES.get(abbr.upper(), [abbr])


def _name_to_abbr(full_name: str) -> str:
    """
    Reverse-lookup: map a Cricbuzz team name back to the short abbreviation.
    Falls back to the first word uppercased if no alias matches.
    """
    name_lower = full_name.lower()
    for abbr, aliases in TEAM_ALIASES.items():
        if any(a.lower() in name_lower or name_lower in a.lower() for a in aliases):
            return abbr
    return full_name.split()[0].upper()


class CricbuzzClient:
    def __init__(self, timeout: int = 10):
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)
        self._timeout = timeout
        self._last_request_time: float = 0.0

    # ------------------------------------------------------------------
    # Internal: retrying GET
    # ------------------------------------------------------------------

    def _get_with_retry(self, url: str, params: dict | None = None) -> requests.Response:
        """
        GET url with automatic retry / backoff.

        Retryable conditions:   5xx, ConnectionError, Timeout
        Rate-limit (429):       backs off for _RATE_LIMIT_DELAY seconds then retries
        Hard 4xx (non-429):     raises immediately — no retry
        Exhausted retries:      raises the last exception
        """
        # Polite pacing between requests
        elapsed = time.time() - self._last_request_time
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._session.get(url, params=params, timeout=self._timeout)

                if resp.status_code == 429:
                    logger.warning(
                        "Rate-limited by Cricbuzz (429) on attempt %d/%d — "
                        "backing off %.0fs",
                        attempt + 1, _MAX_RETRIES, _RATE_LIMIT_DELAY,
                    )
                    time.sleep(_RATE_LIMIT_DELAY)
                    last_exc = requests.HTTPError(response=resp)
                    continue

                if 500 <= resp.status_code < 600:
                    delay = _backoff(attempt)
                    logger.warning(
                        "Cricbuzz returned %d on attempt %d/%d — retrying in %.1fs",
                        resp.status_code, attempt + 1, _MAX_RETRIES, delay,
                    )
                    time.sleep(delay)
                    last_exc = requests.HTTPError(response=resp)
                    continue

                resp.raise_for_status()  # hard-fail on 4xx (except 429 handled above)
                self._last_request_time = time.time()
                return resp

            except (ReqConnError, ReqTimeout) as exc:
                delay = _backoff(attempt)
                logger.warning(
                    "Network error on attempt %d/%d (%s) — retrying in %.1fs",
                    attempt + 1, _MAX_RETRIES, exc, delay,
                )
                time.sleep(delay)
                last_exc = exc

        raise last_exc or RuntimeError(f"All {_MAX_RETRIES} retries exhausted for {url}")

    # ------------------------------------------------------------------
    # Match discovery
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Internal: live match discovery via HTML scraping
    # ------------------------------------------------------------------

    _LIVE_SCORES_URL = "https://www.cricbuzz.com/cricket-match/live-scores"

    # Matches the anchor tag containing a match link, capturing title and href.
    # title="Team1 vs Team2, Nth Match - <status>" href="/live-cricket-scores/{id}/{slug}"
    _MATCH_ANCHOR_RE = re.compile(
        r'title="([^"]+?)\s+-\s+([^"]+?)"\s+href="/live-cricket-scores/(\d+)/([a-z0-9-]+)"'
    )

    # Status strings that mean the match has not yet started or is already over.
    # Anything else (score strings, "In Progress", etc.) is considered live.
    _NOT_LIVE_STATUSES = ("preview", "won", "lost", "drawn", "tied", "abandoned", "no result")

    def _fetch_live_matches(self) -> list[tuple[int, str, str]]:
        """
        Scrape the Cricbuzz live scores page and return a list of
        (cb_id, team1_slug, team2_slug) for every match that is currently
        in progress (i.e. not a preview and not completed).

        team1_slug / team2_slug are the raw slug tokens, e.g. "csk", "kkr".
        """
        url = self._LIVE_SCORES_URL
        try:
            resp = self._get_with_retry(url)
        except Exception as exc:
            logger.warning("Failed to fetch live scores page: %s", exc)
            return []

        seen: set[int] = set()
        results: list[tuple[int, str, str]] = []

        for _title, status, cb_id_str, slug in self._MATCH_ANCHOR_RE.findall(resp.text):
            cb_id = int(cb_id_str)
            if cb_id in seen:
                continue
            seen.add(cb_id)

            status_lower = status.strip().lower()
            if any(s in status_lower for s in self._NOT_LIVE_STATUSES):
                logger.debug(
                    "Skipping cb_id=%s (status: %s)", cb_id, status.strip()
                )
                continue

            # slug format: "{t1}-vs-{t2}-{rest}"
            parts = slug.split("-vs-", 1)
            if len(parts) != 2:
                continue
            team1_slug = parts[0]
            team2_slug = parts[1].split("-")[0]
            results.append((cb_id, team1_slug, team2_slug))
            logger.debug(
                "Found live match: cb_id=%s  %s vs %s  (status: %s)",
                cb_id, team1_slug, team2_slug, status.strip(),
            )

        return results

    @staticmethod
    def _matches_any(name: str, aliases: list[str]) -> bool:
        name_lower = name.lower()
        return any(a.lower() in name_lower or name_lower in a.lower() for a in aliases)

    @staticmethod
    def _slug_to_abbr(slug: str) -> str:
        """
        Map a URL slug token (e.g. 'csk', 'lsg') to a known abbreviation,
        or return the slug uppercased if no match found.
        """
        slug_upper = slug.upper()
        # Direct match first (most slugs are already the abbreviation)
        if slug_upper in TEAM_ALIASES:
            return slug_upper
        # Fallback: check if slug appears in any alias
        for abbr, aliases in TEAM_ALIASES.items():
            if any(slug.lower() in a.lower() for a in aliases):
                return abbr
        return slug_upper

    def find_live_match(self, team1: str, team2: str) -> Optional[int]:
        """
        Find a live match for the given team abbreviations.
        Returns the Cricbuzz cb_id or None if not found.
        """
        t1_aliases = [team1.upper()] + _aliases(team1)
        t2_aliases = [team2.upper()] + _aliases(team2)

        for cb_id, t1_slug, t2_slug in self._fetch_live_matches():
            t1_abbr = self._slug_to_abbr(t1_slug)
            t2_abbr = self._slug_to_abbr(t2_slug)
            t1_match = (
                self._matches_any(t1_slug, t1_aliases)
                or self._matches_any(t1_abbr, t1_aliases)
            )
            t2_match = (
                self._matches_any(t2_slug, t2_aliases)
                or self._matches_any(t2_abbr, t2_aliases)
            )
            if t1_match and t2_match:
                logger.info("Found match: %s vs %s  cb_id=%s", team1, team2, cb_id)
                return cb_id

        logger.debug("No live match found for %s vs %s", team1, team2)
        return None

    def find_live_ipl_match(self) -> Optional[tuple[str, str, int]]:
        """
        Auto-discover any live IPL match.
        Returns (team1_abbr, team2_abbr, cb_id) or None if no IPL match is live.
        """
        for cb_id, t1_slug, t2_slug in self._fetch_live_matches():
            slug = f"{t1_slug}-vs-{t2_slug}"
            # Check if this looks like an IPL match via the full slug in the page
            # We re-check by fetching the page again — instead, filter via slug length
            # heuristic: IPL team slugs are short (csk, kkr, mi, rcb, etc.)
            t1_abbr = self._slug_to_abbr(t1_slug)
            t2_abbr = self._slug_to_abbr(t2_slug)
            if t1_abbr in TEAM_ALIASES and t2_abbr in TEAM_ALIASES:
                logger.info(
                    "Found live IPL match: %s vs %s  cb_id=%s", t1_abbr, t2_abbr, cb_id
                )
                return t1_abbr, t2_abbr, cb_id

        logger.debug("No live IPL match found")
        return None

    # ------------------------------------------------------------------
    # Commentary
    # ------------------------------------------------------------------

    def get_commentary(self, cb_id: int, innings: int) -> list[dict]:
        """
        Fetch all commentary items for the given innings (1 or 2).

        Cricbuzz paginates with a `timestamp` cursor.  We walk all pages and
        return a flat list of raw commentary items (newest-first order as
        returned by Cricbuzz — adapter.py handles chronological sorting).

        Each item is a raw Cricbuzz commentary dict; see adapter.py for the
        fields we care about.
        """
        items: list[dict] = []
        url = f"{_BASE}/api/mcenter/{cb_id}/full-commentary/{innings}"
        params: dict = {}

        while True:
            try:
                resp = self._get_with_retry(url, params=params or None)
                data = resp.json()
            except Exception as exc:
                logger.warning(
                    "commentary fetch failed after retries (cb_id=%s inn=%s): %s",
                    cb_id, innings, exc,
                )
                break

            # New response structure: {"commentary": [{"inningsId": N, "commentaryList": [...]}]}
            # commentaryList items are newest-first.
            comm_wrapper = data.get("commentary", [])
            if not comm_wrapper:
                break

            # Extract the commentaryList from the innings wrapper
            comm_list: list[dict] = []
            for innings_obj in comm_wrapper:
                if isinstance(innings_obj, dict):
                    comm_list.extend(innings_obj.get("commentaryList", []))

            if not comm_list:
                break

            items.extend(comm_list)

            # Pagination: if a minTimestamp is provided, fetch older pages.
            min_ts = data.get("minTimestamp")
            if not min_ts:
                break
            if params.get("timestamp") == min_ts:
                break
            params["timestamp"] = min_ts

        return items

    # ------------------------------------------------------------------
    # First innings summary (single blocking call — called once inn2 starts)
    # ------------------------------------------------------------------

    def get_inn1_summary(self, cb_id: int) -> tuple[int, int]:
        """
        Fetch the completed first innings and return (total_runs, total_legal_balls).

        total_legal_balls is counted from actual deliveries (not overs * 6) so
        rain-reduced matches are handled correctly — mirrors the same logic used
        in tests/simulate_hot_match.py and the Cricsheet notebooks.

        Raises RuntimeError if the innings cannot be parsed.
        """
        from polling.adapter import count_legal_balls, sum_innings_runs

        items = self.get_commentary(cb_id, innings=1)
        if not items:
            raise RuntimeError(f"Inn1 commentary empty for cb_id={cb_id}")

        total_runs = sum_innings_runs(items)
        total_balls = count_legal_balls(items)

        if total_balls == 0:
            raise RuntimeError(f"No legal balls found in inn1 for cb_id={cb_id}")

        logger.info("Inn1 summary: runs=%d  legal_balls=%d", total_runs, total_balls)
        return total_runs, total_balls
