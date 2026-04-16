"""
bot/state.py — Persistent bot state (subscribed chats + seen alert fingerprints).

State is written to disk on every mutation so it survives container restarts.
File path is controlled by the BOT_STATE_PATH env var (default: data/bot_state.json).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_STATE_PATH = Path(os.environ.get("BOT_STATE_PATH", "data/bot_state.json"))


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

subscribed_chats: set[int] = set()
seen_fps: list[str] = []   # ordered list so we can trim oldest entries


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load() -> None:
    """Load state from disk. Called once at startup."""
    global subscribed_chats, seen_fps
    if not _STATE_PATH.exists():
        return
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        subscribed_chats = set(data.get("subscribed_chats", []))
        seen_fps = list(data.get("seen_fps", []))
    except Exception:
        pass  # corrupt file — start fresh


def _save() -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(
        json.dumps(
            {
                "subscribed_chats": list(subscribed_chats),
                "seen_fps": seen_fps,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

def subscribe(chat_id: int) -> None:
    subscribed_chats.add(chat_id)
    _save()


def unsubscribe(chat_id: int) -> None:
    subscribed_chats.discard(chat_id)
    _save()


def has_fingerprint(fp: str) -> bool:
    return fp in seen_fps


def add_fingerprint(fp: str) -> None:
    if fp in seen_fps:
        return
    seen_fps.append(fp)
    _save()
