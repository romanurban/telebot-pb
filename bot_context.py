"""BotContext dataclass — single object replacing parameter explosion in handlers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


@dataclass
class BotContext:
    """Groups config values, mutable state dicts, and callable dependencies
    that handlers need, so every handler signature is just (message, ctx)."""

    # ── Config (immutable for the lifetime of the process) ──────────────
    bot_username: str
    name_mention_re: re.Pattern | None
    image_default_prompt: str
    chat_react_prompt: str
    max_unmentioned_replies: int
    recent_activity_seconds: int

    # ── Mutable state dicts (shared references) ────────────────────────
    last_activity_time: dict[int, Any] = field(default_factory=dict)
    messages_since_bot_reply: dict[int, int] = field(default_factory=dict)
    bot_unmentioned_count: dict[int, int] = field(default_factory=dict)
    last_bot_reply_time: dict[int, Any] = field(default_factory=dict)
    bus_last_reply: dict[int, float] = field(default_factory=dict)

    # ── Callable dependencies ──────────────────────────────────────────
    try_claim_message: Any = None
    nudge_inactive_chats: Any = None
    get_picture_of_the_day: Any = None
    style_caption: Any = None
    retrieve_joke: Any = None
    retrieve_fact: Any = None
    generate_voice_file: Any = None
    ask_openai: Any = None
    ask_agent: Any = None
    ask_openai_image: Any = None
    clean_openai_reply: Any = None
    mark_bot_replied: Any = None
    extract_voice_file: Any = None
    extract_json_image: Any = None
    needs_voice_tool: Any = None

    # ── Photo handler needs the bot instance ───────────────────────────
    bot: Any = None
