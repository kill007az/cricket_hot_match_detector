"""
bot/alert_loop.py — Background asyncio coroutine that polls for match signals
and broadcasts proactive Telegram alerts.

Polling interval: 30 seconds.
Runs forever; exceptions are caught and logged so a single bad tick never
kills the loop.

Lifecycle alerts (fired once per event per match, LLM-summarised):
    INN1_STARTED  — first ball of inn1 recorded
    INN1_ENDED    — inn1 complete, inn2 has started
    INN2_STARTED  — first ball of inn2 recorded
    MATCH_ENDED   — match over (runs_needed <= 0 / wickets >= 10 / balls_remaining == 0)

Hotness alerts (existing):
    PRE_MATCH     — ball 1, win_prob 40–60%
    {signal_text} — in-game signal fired by engine
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx
from telegram.ext import Application

import bot.state as state

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 30  # seconds


def _orchestrator_url() -> str:
    return os.environ.get("ORCHESTRATOR_URL", "http://localhost:8080")


# ---------------------------------------------------------------------------
# LLM summariser (sync, runs in thread executor)
# ---------------------------------------------------------------------------

def _llm_summarise(prompt: str) -> str:
    """Call LLM synchronously for a one-shot alert summary."""
    try:
        from bot.llm import get_llm
        llm = get_llm()
        result = llm.invoke(prompt)
        c = result.content
        if isinstance(c, list):
            return " ".join(b["text"] for b in c if isinstance(b, dict) and b.get("type") == "text")
        return str(c)
    except Exception as exc:
        logger.warning("LLM summarise failed: %s", exc)
        return None  # caller falls back to template


# ---------------------------------------------------------------------------
# Alert builders
# ---------------------------------------------------------------------------

def _build_inn1_started(team1: str, team2: str, date: str, llm_text: str | None) -> str:
    if llm_text:
        return f"🏏 {llm_text}"
    return (
        f"🏏 MATCH STARTED — {team1} vs {team2}"
        + (f" ({date})" if date else "")
        + f"\n{team1} batting first. Inn1 underway!\n\nAsk me anything or use /status."
    )


def _build_inn1_ended(team1: str, team2: str, inn1_total: int, target: int, llm_text: str | None) -> str:
    if llm_text:
        return f"📊 {llm_text}"
    return (
        f"📊 INN1 COMPLETE — {team1} vs {team2}\n"
        f"{team1}: {inn1_total}  |  Target: {target}\n\n"
        f"Ask me anything or use /status."
    )


def _build_inn2_started(team1: str, team2: str, target: int,
                        win_prob: float, hotness: float, llm_text: str | None) -> str:
    if llm_text:
        return f"🎯 {llm_text}"
    return (
        f"🎯 CHASE STARTED — {team1} vs {team2}\n"
        f"{team2} need {target} to win.\n"
        f"Win prob: {win_prob:.1%}  |  Hotness: {hotness:.1%}\n\n"
        f"Ask me anything or use /status."
    )


def _build_match_ended(team1: str, team2: str, inn1_total: int,
                       inn2_runs: int, inn2_wkts: int, llm_text: str | None) -> str:
    if llm_text:
        return f"🏆 {llm_text}"
    return (
        f"🏆 MATCH OVER — {team1} vs {team2}\n"
        f"{team1}: {inn1_total}  |  {team2}: {inn2_runs}/{inn2_wkts}\n\n"
        f"Ask me anything about the match!"
    )


def _build_pre_match_alert(team1: str, team2: str, win_prob: float) -> str:
    return (
        f"📢 PRE-MATCH ALERT — {team1} vs {team2}\n"
        f"Win prob at ball 1: {win_prob:.1%} — 50/50 chase, worth watching!\n\n"
        f"Reply with /status or ask me anything."
    )


def _build_signal_alert(team1: str, team2: str, ball: int, signal_text: str,
                        win_prob: float, hotness: float,
                        runs_needed: int, balls_remaining: int, wickets: int) -> str:
    return (
        f"🔥 LIVE ALERT — {team1} vs {team2}\n"
        f"Ball {ball}: {signal_text}\n"
        f"Win prob: {win_prob:.1%} | Hotness: {hotness:.3f}\n"
        f"Need {runs_needed} off {balls_remaining} balls, {wickets} wickets down\n\n"
        f"Reply with /chart hotness or ask me anything."
    )


# ---------------------------------------------------------------------------
# Send helper
# ---------------------------------------------------------------------------

async def _send_to_all(app: Application, text: str) -> None:
    for chat_id in list(state.subscribed_chats):
        try:
            await app.bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            logger.warning("Failed to send alert to chat_id=%s: %s", chat_id, e)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def alert_loop(app: Application) -> None:
    """Run forever, polling /matches/current and sending alerts."""
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            try:
                await _tick(client, app)
            except Exception:
                logger.exception("alert_loop tick error — continuing")
            await asyncio.sleep(_POLL_INTERVAL)


async def _tick(client: httpx.AsyncClient, app: Application) -> None:
    if not state.subscribed_chats:
        return

    base = _orchestrator_url()

    # Fetch current match
    try:
        resp = await client.get(f"{base}/matches/current")
    except Exception as e:
        logger.debug("alert_loop: orchestrator unreachable: %s", e)
        return

    if resp.status_code == 404:
        return
    resp.raise_for_status()

    data      = resp.json()
    match_id  = data.get("match_id", "")
    team1     = data.get("team1", "?")
    team2     = data.get("team2", "?")
    date      = data.get("date", "")
    balls     = data.get("balls_seen", 0)   # inn2 balls
    last      = data.get("last_state", {})
    win_prob  = last.get("win_prob", 0) or 0
    hotness   = last.get("hotness", 0) or 0
    rr        = last.get("runs_needed", 0) or 0
    br        = last.get("balls_remaining", 0) or 0
    wk        = last.get("wickets", 0) or 0
    signals   = last.get("signals", [])

    # Fetch inn1 ball count (for lifecycle detection)
    inn1_balls = 0
    inn1_total = 0
    try:
        r1 = await client.get(f"{base}/matches/{match_id}/ball_events_inn1")
        if r1.status_code == 200:
            inn1_events = r1.json()
            inn1_balls  = len(inn1_events)
    except Exception:
        pass

    # Fetch inn1 scorecard for team total
    try:
        rs = await client.get(f"{base}/matches/{match_id}/scorecard/1")
        if rs.status_code == 200:
            inn1_total = rs.json().get("team_total", 0) or 0
    except Exception:
        pass

    target = inn1_total + 1 if inn1_total else 0

    # --- INN1_STARTED ---
    if inn1_balls >= 1:
        fp = f"{match_id}:INN1_STARTED"
        if not state.has_fingerprint(fp):
            state.add_fingerprint(fp)
            prompt = (
                f"Write a short, exciting 1–2 sentence Telegram alert (no emoji prefix — caller adds it) "
                f"that innings 1 has started: {team1} vs {team2} on {date}. "
                f"{team1} are batting first. Be concise and energetic."
            )
            llm_text = await asyncio.to_thread(_llm_summarise, prompt)
            msg = _build_inn1_started(team1, team2, date, llm_text)
            await _send_to_all(app, msg)
            logger.info("Sent INN1_STARTED alert for %s", match_id)

    # --- INN1_ENDED (inn1 complete + inn2 has started) ---
    if inn1_balls >= 6 and balls >= 1 and inn1_total > 0:
        fp = f"{match_id}:INN1_ENDED"
        if not state.has_fingerprint(fp):
            state.add_fingerprint(fp)
            prompt = (
                f"Write a short, exciting 1–2 sentence Telegram alert (no emoji prefix) "
                f"that {team1} have finished their innings with {inn1_total} runs in {team1} vs {team2}. "
                f"{team2} need {target} to win. Be concise."
            )
            llm_text = await asyncio.to_thread(_llm_summarise, prompt)
            msg = _build_inn1_ended(team1, team2, inn1_total, target, llm_text)
            await _send_to_all(app, msg)
            logger.info("Sent INN1_ENDED alert for %s (target=%d)", match_id, target)

    # --- INN2_STARTED ---
    if balls >= 1:
        fp = f"{match_id}:INN2_STARTED"
        if not state.has_fingerprint(fp):
            state.add_fingerprint(fp)
            prompt = (
                f"Write a short, exciting 1–2 sentence Telegram alert (no emoji prefix) "
                f"that {team2} have started chasing {target} against {team1}. "
                f"Opening win probability is {win_prob:.0%}. Be concise and energetic."
            )
            llm_text = await asyncio.to_thread(_llm_summarise, prompt)
            msg = _build_inn2_started(team1, team2, target, win_prob, hotness, llm_text)
            await _send_to_all(app, msg)
            logger.info("Sent INN2_STARTED alert for %s", match_id)

    # --- PRE_MATCH: tight chase at ball 1 ---
    if balls == 1 and 0.40 <= win_prob <= 0.60:
        fp = f"{match_id}:PRE_MATCH"
        if not state.has_fingerprint(fp):
            state.add_fingerprint(fp)
            msg = _build_pre_match_alert(team1, team2, win_prob)
            await _send_to_all(app, msg)
            logger.info("Sent PRE_MATCH alert for %s", match_id)

    # --- IN_GAME signals ---
    for signal_text in signals:
        fp = f"{match_id}:{signal_text}"
        if not state.has_fingerprint(fp):
            state.add_fingerprint(fp)
            msg = _build_signal_alert(team1, team2, balls, signal_text,
                                      win_prob, hotness, rr, br, wk)
            await _send_to_all(app, msg)
            logger.info("Sent IN_GAME alert for %s: %s", match_id, signal_text)

    # --- MATCH_ENDED ---
    match_over = balls >= 1 and (rr <= 0 or wk >= 10 or br == 0)
    if match_over:
        fp = f"{match_id}:MATCH_ENDED"
        if not state.has_fingerprint(fp):
            state.add_fingerprint(fp)
            # Compute inn2 score
            inn2_runs = 0
            try:
                r2 = await client.get(f"{base}/matches/{match_id}/ball_events")
                if r2.status_code == 200:
                    inn2_runs = sum(
                        b.get("runs", 0) + b.get("extras", 0) for b in r2.json()
                    )
                rs2 = await client.get(f"{base}/matches/{match_id}/scorecard/2")
                if rs2.status_code == 200:
                    sc2_total = rs2.json().get("team_total", 0)
                    if sc2_total:
                        inn2_runs = sc2_total
            except Exception:
                pass

            winner = team2 if rr <= 0 else team1
            margin = (rr - 1) if rr > 0 else (10 - wk)
            margin_str = f"{10 - wk} wickets" if rr <= 0 else f"{rr - 1} runs"

            prompt = (
                f"Write a short, exciting 1–2 sentence Telegram alert (no emoji prefix) "
                f"summarising this T20 match result: {team1} scored {inn1_total}, "
                f"{team2} scored {inn2_runs}/{wk}. {winner} won by {margin_str}. "
                f"Be concise and capture the drama."
            )
            llm_text = await asyncio.to_thread(_llm_summarise, prompt)
            msg = _build_match_ended(team1, team2, inn1_total, inn2_runs, wk, llm_text)
            await _send_to_all(app, msg)
            logger.info("Sent MATCH_ENDED alert for %s", match_id)
