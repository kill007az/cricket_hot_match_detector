"""
Streamlit live match dashboard.

Talks exclusively to the orchestrator API (ORCHESTRATOR_URL env var).
No file access, no direct engine calls.

Auto-refreshes every REFRESH_SECS seconds.
"""

from __future__ import annotations

import base64
import os
import time
import uuid
from typing import Optional

import pandas as pd
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8080")
REFRESH_SECS     = 10

st.set_page_config(
    page_title="Cricket Hot Match Detector",
    page_icon="🏏",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Orchestrator API client
# ---------------------------------------------------------------------------

def _get(path: str, timeout: int = 5) -> Optional[dict | list]:
    try:
        r = requests.get(f"{ORCHESTRATOR_URL}{path}", timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _post(path: str, body: dict, timeout: int = 60) -> Optional[dict]:
    try:
        r = requests.post(f"{ORCHESTRATOR_URL}{path}", json=body, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def fetch_health()         -> Optional[dict]: return _get("/health")
def fetch_current_match()  -> Optional[dict]: return _get("/matches/current")
def fetch_history(match_id: str) -> Optional[list]: return _get(f"/matches/{match_id}/history")
def fetch_signals(match_id: str) -> Optional[list]: return _get(f"/matches/{match_id}/signals")
def fetch_bot_status()     -> Optional[dict]: return _get("/bot/status")

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def build_df(history: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(history)
    # forecast is None before ball 60 — keep as NaN so line_chart renders a gap
    df["forecast"] = pd.to_numeric(df["forecast"], errors="coerce")
    return df.set_index("ball")

# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def render_waiting(reason: str = "") -> None:
    st.title("🏏 Cricket Hot Match Detector")
    msg = "Waiting for a match to start."
    if reason:
        msg += f"\n\n_{reason}_"
    msg += f"\n\nAuto-refreshes every {REFRESH_SECS}s."
    st.info(msg)


def render_error(detail: str) -> None:
    st.title("🏏 Cricket Hot Match Detector")
    st.error(f"Cannot reach orchestrator at `{ORCHESTRATOR_URL}`\n\n{detail}")


def render_dashboard(match: dict, history: list[dict], signals: list[dict]) -> None:
    last = match.get("last_state", {})
    match_id = match.get("match_id", "")
    team1    = match.get("team1", "?")
    team2    = match.get("team2", "?")
    date     = match.get("date", "")
    balls    = match.get("balls_seen", len(history))

    # --- Header ---
    st.title(f"🏏 {team1} vs {team2}")
    st.caption(f"{date}  ·  {balls} balls  ·  auto-refreshes every {REFRESH_SECS}s")

    # --- Key metrics ---
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Runs Needed",     last.get("runs_needed",    "—"))
    col2.metric("Wickets",         f"{last.get('wickets', 0)} / 10")
    col3.metric("Balls Remaining", last.get("balls_remaining","—"))
    col4.metric("Win Probability", f"{last.get('win_prob', 0):.1%}" if last else "—")
    col5.metric("Hotness",         f"{last.get('hotness', 0):.3f}" if last else "—")

    st.divider()

    if not history:
        st.info("No ball data yet — waiting for the 2nd innings to start.")
        return

    df = build_df(history)

    # --- Charts ---
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Win Probability")
        st.line_chart(
            df[["win_prob"]],
            y_label="Win Probability",
            color=["#1f77b4"],
        )

    with col_right:
        st.subheader("Hotness + Forecast")
        st.line_chart(
            df[["hotness", "forecast"]],
            y_label="Score",
            color=["#d62728", "#ff7f0e"],
        )

    st.divider()

    # --- Signals feed ---
    st.subheader("Signals")
    if signals:
        for event in signals[-5:]:   # most recent 5 signal events
            ball_num = event.get("ball", "?")
            for sig in event.get("signals", []):
                icon = "🔥" if "heat" in sig.lower() else "📢"
                st.warning(f"**Ball {ball_num}** — {icon} {sig}")
    else:
        st.caption("No signals fired yet.")

    # --- Health sidebar ---
    with st.sidebar:
        st.header("System")
        health = fetch_health()
        if health:
            engine_ok = health.get("engine_reachable", False)
            st.metric("Engine",          "✅ up" if engine_ok else "❌ down")
            st.metric("Matches tracked", health.get("matches_tracked", 0))
        else:
            st.warning("Orchestrator not reachable")
        st.caption(f"Orchestrator: `{ORCHESTRATOR_URL}`")
        st.caption(f"Match: `{match_id}`")

        st.divider()
        st.header("🤖 Telegram Bot")
        bot = fetch_bot_status()
        if bot and bot.get("running"):
            st.metric("Subscribers",  bot.get("subscribers", 0))
            st.metric("Alerts sent",  bot.get("alerts_sent", 0))
        elif bot:
            st.caption("Bot not started yet — no state file found.")
        else:
            st.caption("Bot status unavailable.")


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "session_id" not in st.session_state:
    st.session_state.session_id = f"streamlit_{uuid.uuid4().hex[:8]}"

if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

tab_dash, tab_chat = st.tabs(["📊 Dashboard", "💬 Chat"])

with tab_dash:
    health = fetch_health()

    if health is None:
        render_error("Check that the orchestrator container is running.")
    else:
        current = fetch_current_match()

        if current is None:
            render_waiting("No active match found in the orchestrator.")
        else:
            match_id = current.get("match_id", "")
            history  = fetch_history(match_id) or []
            signals  = fetch_signals(match_id) or []
            render_dashboard(current, history, signals)

with tab_chat:
    st.header("💬 Match Analyst")
    st.caption("Ask anything about the live match — powered by the same agent as the Telegram bot.")

    # Render history
    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])
            for chart_b64 in msg.get("charts", []):
                st.image(base64.b64decode(chart_b64))

    # Input
    if prompt := st.chat_input("Ask about the match…"):
        st.session_state.chat_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                resp = _post(
                    "/chat",
                    {"message": prompt, "chat_id": st.session_state.session_id},
                    timeout=60,
                )
            if resp is None:
                reply = "⚠️ Could not reach the bot agent. Is it running?"
                charts = []
            else:
                reply  = resp.get("reply", "")
                charts = resp.get("charts", [])

            st.write(reply)
            for chart_b64 in charts:
                st.image(base64.b64decode(chart_b64))

        st.session_state.chat_messages.append(
            {"role": "assistant", "content": reply, "charts": charts}
        )
        st.rerun()

# Auto-refresh (dashboard data only — chat state survives via session_state)
time.sleep(REFRESH_SECS)
st.rerun()
