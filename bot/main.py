"""
bot/main.py — Telegram bot entry point.

Run with:  python -m bot.main

Commands
--------
/start              Register chat for alerts; print command list
/stop               Unregister chat from alerts
/status             Current match summary (no LLM)
/chart <type>       Chart: winprob | hotness | forecast (no LLM)
/signals            Signal timeline (no LLM)
/turning            Top 5 turning points (no LLM)
/balls [n]          Last N balls table (no LLM, default 20)
/matches            List all recorded matches (no LLM)
<free text>         LangGraph ReAct agent (Gemini Flash)

Environment variables
---------------------
TELEGRAM_TOKEN      Required
GOOGLE_API_KEY      Required (Gemini)
ORCHESTRATOR_URL    Default: http://orchestrator:8080
BOT_STATE_PATH      Default: data/bot_state.json
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import sys

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import bot.state as state
from bot.alert_loop import alert_loop
from bot.tools import (
    get_ball_by_ball_table,
    get_hotness_curve,
    get_key_turning_points,
    get_match_status,
    get_signal_timeline,
    get_win_prob_curve,
    get_forecast_overlay,
    list_matches,
    _chart_cache,
)

logger = logging.getLogger(__name__)

_TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

_WELCOME = (
    "👋 Cricket Hot Match Bot\n\n"
    "Commands:\n"
    "  /status         — current match summary\n"
    "  /chart winprob  — win probability chart\n"
    "  /chart hotness  — hotness chart\n"
    "  /chart forecast — forecast overlay chart\n"
    "  /signals        — signal timeline\n"
    "  /turning        — top turning points\n"
    "  /balls [n]      — last N balls table (default 20)\n"
    "  /matches        — list all recorded matches\n"
    "  /stop           — unsubscribe from alerts\n\n"
    "Or just ask me anything about the match!"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _send_chart(update: Update, png: bytes) -> None:
    await update.message.reply_photo(photo=io.BytesIO(png))


async def _reply(update: Update, text: str) -> None:
    # Telegram message limit is 4096 chars; split if needed
    for i in range(0, len(text), 4096):
        await update.message.reply_text(text[i:i + 4096])


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state.subscribe(update.effective_chat.id)
    await _reply(update, "✅ Subscribed to match alerts.\n\n" + _WELCOME)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state.unsubscribe(update.effective_chat.id)
    await _reply(update, "🔕 Unsubscribed from match alerts.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await asyncio.to_thread(get_match_status.invoke, {})
    await _reply(update, result)


async def cmd_chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chart_type = (context.args[0].lower() if context.args else "hotness")
    _chart_cache.clear()

    if chart_type == "winprob":
        result = await asyncio.to_thread(get_win_prob_curve.invoke, {})
    elif chart_type == "hotness":
        result = await asyncio.to_thread(get_hotness_curve.invoke, {})
    elif chart_type == "forecast":
        result = await asyncio.to_thread(get_forecast_overlay.invoke, {})
    else:
        await _reply(update, "Unknown chart type. Use: winprob | hotness | forecast")
        return

    await _reply(update, result)
    for png in _chart_cache.values():
        await _send_chart(update, png)


async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await asyncio.to_thread(get_signal_timeline.invoke, {})
    await _reply(update, result)


async def cmd_turning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await asyncio.to_thread(get_key_turning_points.invoke, {"top_n": 5})
    await _reply(update, result)


async def cmd_balls(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    n = 20
    if context.args:
        try:
            n = int(context.args[0])
        except ValueError:
            pass
    result = await asyncio.to_thread(get_ball_by_ball_table.invoke, {"last_n": n})
    await _reply(update, f"```\n{result}\n```")


async def cmd_matches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await asyncio.to_thread(list_matches.invoke, {})
    await _reply(update, result)


# ---------------------------------------------------------------------------
# Free-text handler (LangGraph agent)
# ---------------------------------------------------------------------------

async def free_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from bot.agent import run_agent

    text = update.message.text or ""
    if not text.strip():
        return

    logger.info("bot | chat=%s | message: %s", update.effective_chat.id, text[:200])

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    async for chunk in run_agent(text, update.effective_chat.id):
        if isinstance(chunk, bytes):
            await _send_chart(update, chunk)
        elif chunk:
            logger.info("bot | chat=%s | reply: %s", update.effective_chat.id, chunk[:200])
            await _reply(update, chunk)


# ---------------------------------------------------------------------------
# Startup hook — launch alert loop as background task
# ---------------------------------------------------------------------------

async def post_init(application: Application) -> None:
    state.load()
    asyncio.create_task(alert_loop(application))
    logger.info("Alert loop started. Subscribed chats: %d", len(state.subscribed_chats))

    from bot.api import start_api
    await start_api()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        stream=sys.stderr,
    )

    if not _TELEGRAM_TOKEN:
        print("ERROR: TELEGRAM_TOKEN environment variable not set.", file=sys.stderr)
        sys.exit(1)

    app = (
        ApplicationBuilder()
        .token(_TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("stop",    cmd_stop))
    app.add_handler(CommandHandler("status",  cmd_status))
    app.add_handler(CommandHandler("chart",   cmd_chart))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("turning", cmd_turning))
    app.add_handler(CommandHandler("balls",   cmd_balls))
    app.add_handler(CommandHandler("matches", cmd_matches))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text_handler))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
