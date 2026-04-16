"""
bot/llm.py — LLM factory for the bot.

Single place to configure the language model. Import get_llm() wherever
an LLM instance is needed — agent.py, future summarisers, etc.

Current backend: Gemini Flash (free tier) via langchain-google-genai.
GOOGLE_API_KEY is read from the environment.
"""

from __future__ import annotations

import os

from langchain_google_genai import ChatGoogleGenerativeAI

_GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
_MODEL          = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
_TEMP           = 0


def get_llm() -> ChatGoogleGenerativeAI:
    """Return a configured Gemini Flash LLM instance."""
    return ChatGoogleGenerativeAI(
        model=_MODEL,
        temperature=_TEMP,
        google_api_key=_GOOGLE_API_KEY,
    )
