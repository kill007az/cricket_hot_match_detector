"""
bot/agent.py — LangGraph ReAct agent backed by Gemini Flash.

run_agent() is an async generator that yields:
  - str  : text chunks to send as a Telegram message
  - bytes: PNG image bytes to send as a Telegram photo

The agent has per-chat session memory via MemorySaver — each chat_id gets its
own conversation thread, so follow-up messages ("Sure", "4", "that one") work.

Chart bytes are communicated via tools._chart_cache side-channel:
  tools clear the cache before each call, chart tools deposit bytes,
  run_agent yields them after the agent finishes.

Broken-thread recovery:
  If a tool call was interrupted mid-execution (e.g. container restart, exception
  during streaming), MemorySaver stores an AIMessage with pending tool_calls but
  no corresponding ToolMessage. LangGraph raises InvalidUpdateError on the next
  invocation of that thread. run_agent detects this, clears the thread state via
  _clear_thread(), and retries immediately with a clean slate.
"""

from __future__ import annotations

import asyncio
import random
from typing import AsyncGenerator

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from bot.llm import get_llm
from bot.tools import ALL_TOOLS, _chart_cache

_MAX_RETRIES = 3
_BASE_DELAY  = 2.0   # seconds

_SYSTEM_PROMPT = """You are a cricket match analyst for a live IPL match detector.
You have tools to fetch live match data, generate charts, analyse ball-by-ball history,
and run arbitrary Python code against match data.

Rules you must follow:
- Always assume the user is asking about the CURRENT match unless they explicitly name another.
- NEVER ask clarifying questions. Always use tool default parameters and act immediately.
- If the user asks for "a chart" or "the graph" without specifying type, call get_win_prob_curve.
- If asked for multiple charts (e.g. "hotness and forecast"), call all relevant chart tools in one step.
- For any analytical question not covered by a specific tool (e.g. "how many sixes", "which over had most runs"),
  use run_python — write concise Python against the pre-loaded `history` and `ball_events` variables and print the answer.
- If a question is ambiguous, make a reasonable assumption and answer it — do not ask the user.
- Be concise. Report numbers as percentages (e.g. 42.3%) not decimals.
- When a chart tool is called, say "Chart sent." — do not describe it pixel-by-pixel.
- Both innings are tracked. Use innings=1 for first innings, innings=2 (default) for the chase.
- For "who scored" / "batting card" / "bowling figures" questions use get_batting_card or get_bowling_card with the appropriate innings.
- For inn1 stats questions use get_batting_summary(innings=1), get_match_scorecard(innings=1), or run_python with ball_events_inn1.
- If no match is active, say so clearly and stop."""

# Agent with per-chat session memory
_checkpointer = MemorySaver()
_agent = create_react_agent(get_llm(), ALL_TOOLS, checkpointer=_checkpointer)

_BROKEN_THREAD_MARKERS = (
    "tool calls that do not have",   # LangGraph InvalidUpdateError
    "ToolMessage",
)


def _is_broken_thread_error(exc: Exception) -> bool:
    msg = str(exc)
    return any(m in msg for m in _BROKEN_THREAD_MARKERS)


def _clear_thread(thread_id: str) -> None:
    """Delete a thread from MemorySaver so the next call starts fresh."""
    try:
        storage = _checkpointer.storage
        # MemorySaver stores checkpoints under thread_id at the top level
        storage.pop(thread_id, None)
        # Also clear writes buffer if present
        if hasattr(_checkpointer, "writes"):
            _checkpointer.writes.pop(thread_id, None)
    except Exception:
        pass


async def run_agent(message: str, chat_id: int) -> AsyncGenerator[str | bytes, None]:
    """
    Stream agent response for a user message.
    chat_id is used as the LangGraph thread_id for per-chat session memory.
    Yields str (text) and bytes (chart PNGs) interleaved.
    """
    _chart_cache.clear()

    thread_id = str(chat_id)
    config = {
        "configurable": {"thread_id": thread_id},
        "system": _SYSTEM_PROMPT,
    }

    final_text = ""
    last_exc: Exception | None = None
    cleared_thread = False

    for attempt in range(_MAX_RETRIES):
        try:
            async for event in _agent.astream(
                {"messages": [HumanMessage(content=message)]},
                config=config,
            ):
                if "agent" in event:
                    for msg in event["agent"]["messages"]:
                        if not hasattr(msg, "content"):
                            continue
                        c = msg.content
                        if isinstance(c, str):
                            final_text = c
                        elif isinstance(c, list):
                            # Content-block format: [{type: text, text: ...}, ...]
                            final_text = " ".join(
                                b["text"] for b in c if isinstance(b, dict) and b.get("type") == "text"
                            )
            last_exc = None
            break
        except Exception as e:
            last_exc = e
            # Broken thread state: pending tool call with no ToolMessage response.
            # Clear the thread once and retry immediately — no backoff needed.
            if _is_broken_thread_error(e) and not cleared_thread:
                cleared_thread = True
                _clear_thread(thread_id)
                continue
            if attempt < _MAX_RETRIES - 1:
                delay = _BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                await asyncio.sleep(delay)

    if last_exc is not None:
        yield f"Sorry, I encountered an error after {_MAX_RETRIES} attempts: {last_exc}"
        return

    if final_text:
        yield final_text

    for png in _chart_cache.values():
        yield png
