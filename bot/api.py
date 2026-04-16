"""
bot/api.py — lightweight aiohttp HTTP API served alongside the Telegram bot.

Endpoints
---------
POST /chat
    Body:  { "message": "...", "chat_id": "streamlit_abc123" }
    Reply: { "reply": "...", "charts": ["<base64-png>", ...] }

Started inside the existing asyncio event loop via bot/main.py post_init hook.
"""

from __future__ import annotations

import base64
import logging

from aiohttp import web

logger = logging.getLogger(__name__)


async def handle_chat(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    message = body.get("message", "").strip()
    if not message:
        return web.json_response({"error": "Empty message"}, status=400)

    chat_id = body.get("chat_id", "streamlit")
    logger.info("chat_api | chat=%s | message: %s", chat_id, message[:200])

    from bot.agent import run_agent  # late import avoids circular init

    text_parts: list[str] = []
    charts: list[str] = []

    async for chunk in run_agent(message, chat_id):
        if isinstance(chunk, bytes):
            charts.append(base64.b64encode(chunk).decode())
        elif chunk:
            text_parts.append(chunk)

    reply = "\n".join(text_parts)
    logger.info("chat_api | chat=%s | reply: %s", chat_id, reply[:200])
    return web.json_response({"reply": reply, "charts": charts})


async def start_api(host: str = "0.0.0.0", port: int = 8088) -> None:
    """Create and start the aiohttp server inside the current event loop."""
    app = web.Application()
    app.router.add_post("/chat", handle_chat)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Chat API listening on %s:%d", host, port)
