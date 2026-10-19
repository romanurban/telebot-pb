"""Native bot-to-bot handling (Telegram Bot API 10.0+).

With "Bot to Bot Communication Mode" enabled in BotFather and Group Privacy
off, every bot in the group receives the other bots' messages as ordinary
updates. This module decides whether to answer one and enforces the loop
limits Telegram requires us to implement ourselves:

* a bot always answers when a bot replies to it or mentions it;
* an unaddressed bot message (e.g. a nudge) gets at most ONE reacting bot,
  and only when it opens a chain;
* at most MAX_CHAIN bot messages in a row, then bots stay silent until a
  human writes or CHAIN_COOLDOWN passes;
* at least MIN_REPLY_GAP seconds between this bot's replies to bots.

Chain state is per process: each bot counts the bot messages it receives
plus its own messages (Telegram doesn't deliver a bot's own messages back).
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime

import agent_client
import claims
from aiogram.enums import ParseMode
from message_filter import clean_self_mentions, validate_bot_interaction

MAX_CHAIN = 6               # bot messages in a row before the chain is cut
CHAIN_COOLDOWN = 30 * 60    # seconds of silence after a cut; also resets a stale chain
MIN_REPLY_GAP = 5           # seconds between this bot's replies to other bots (MAX_CHAIN is the real loop guard)

_chain_len: dict[int, int] = {}
_chain_last_ts: dict[int, float] = {}
_blocked_until: dict[int, float] = {}
_last_b2b_reply: dict[int, float] = {}


def reset_state() -> None:
    for d in (_chain_len, _chain_last_ts, _blocked_until, _last_b2b_reply):
        d.clear()


def reset_chain(chat_id: int) -> None:
    """A human wrote: bots may talk again."""
    _chain_len[chat_id] = 0
    _blocked_until.pop(chat_id, None)


def count_bot_message(chat_id: int, now: float | None = None) -> int:
    """Count a bot message (ours or another bot's) in the current chain."""
    now = time.time() if now is None else now
    if now - _chain_last_ts.get(chat_id, 0) > CHAIN_COOLDOWN:
        _chain_len[chat_id] = 0  # stale chain: a new conversation (e.g. a nudge)
    _chain_last_ts[chat_id] = now
    _chain_len[chat_id] = _chain_len.get(chat_id, 0) + 1
    return _chain_len[chat_id]


def is_addressed(message, bot_username: str, name_mention_re=None) -> bool:
    """True if a bot message replies to us or mentions us."""
    reply_to = getattr(message, "reply_to_message", None)
    if reply_to is not None and reply_to.from_user is not None:
        if (reply_to.from_user.username or "").lower() == bot_username.lower():
            return True
    text = message.text or message.caption or ""
    if f"@{bot_username}".lower() in text.lower():
        return True
    return name_mention_re is not None and bool(name_mention_re.search(text))


def may_react(message, bot_username: str, *, addressed: bool, now: float | None = None) -> bool:
    """Count an incoming bot message and apply the loop limits to it."""
    chat_id = message.chat.id
    now = time.time() if now is None else now
    chain = count_bot_message(chat_id, now)

    if now < _blocked_until.get(chat_id, 0):
        return False
    if chain > MAX_CHAIN:
        _blocked_until[chat_id] = now + CHAIN_COOLDOWN
        logging.info(f"[b2b] {chain} bot messages in a row in {chat_id}: going quiet")
        return False
    if now - _last_b2b_reply.get(chat_id, 0) < MIN_REPLY_GAP:
        return False
    if addressed:
        return True
    # Unaddressed: only a chain opener (nudge / bot answering a human), one bot only.
    if chain != 1:
        return False
    return claims.try_claim_key(f"tg_{chat_id}_{message.message_id}", bot_username)


async def _answer(message, ctx, sender: str, prompt: str, addressed: bool) -> str:
    chat_id = message.chat.id
    if addressed:
        return await ctx.ask_openai(prompt, username=sender, chat_id=chat_id)
    raw = await ctx.ask_agent(
        [{"role": "user", "content": f"{sender}: {prompt}"},
         {"role": "system", "content": ctx.chat_react_prompt}],
        chat_id=chat_id,
    )
    return ctx.clean_openai_reply(raw)


async def handle_bot_text(message, ctx) -> None:
    chat_id = message.chat.id
    sender = message.from_user.username or str(message.from_user.id)
    text = message.text or ""
    ctx.last_activity_time[chat_id] = datetime.now()

    addressed = is_addressed(message, ctx.bot_username, ctx.name_mention_re)
    if not may_react(message, ctx.bot_username, addressed=addressed):
        # Still remember what was said, so later replies have context.
        agent_client.inject_external_message(chat_id, sender, text)
        return

    prompt = re.sub(re.escape(f"@{ctx.bot_username}"), "", text, flags=re.IGNORECASE).strip()
    try:
        answer = await _answer(message, ctx, sender, prompt, addressed)
    except Exception as e:
        logging.error(f"[b2b] LLM error in chat {chat_id}: {e}")
        return
    await _send_reply(message, ctx, answer)


async def handle_bot_photo(message, ctx) -> None:
    """React to a picture another bot posted, using vision. Silent if vision fails."""
    chat_id = message.chat.id
    addressed = is_addressed(message, ctx.bot_username, ctx.name_mention_re)
    if not may_react(message, ctx.bot_username, addressed=addressed):
        return
    photo_bytes = await ctx.bot.download(message.photo[-1])
    prompt = message.caption or ctx.image_default_prompt
    answer = await ctx.ask_openai_image(photo_bytes.read(), prompt, chat_id=chat_id)
    await _send_reply(message, ctx, answer)


async def _send_reply(message, ctx, answer: str) -> None:
    chat_id = message.chat.id
    answer = clean_self_mentions(answer or "", ctx.bot_username)
    if not validate_bot_interaction(answer, ctx.bot_username):
        return
    try:
        # Reply-quote: the other bot receives it as a reply to its message.
        await message.reply(answer, parse_mode=ParseMode.HTML)
    except Exception as e:
        logging.error(f"[b2b] failed to reply in chat {chat_id}: {e}")
        return
    _last_b2b_reply[chat_id] = time.time()
    ctx.mark_bot_replied(chat_id)  # also counts our message in the chain (main.mark_bot_replied)
