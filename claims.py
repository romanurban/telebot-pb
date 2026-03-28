import asyncio
import hashlib
import logging
import os
import time as _time
from datetime import datetime

from aiogram.types import Message, ReactionTypeEmoji


CLAIM_DIR = "/tmp/telebot_claims"
os.makedirs(CLAIM_DIR, exist_ok=True)


def claim_key(message: Message) -> str:
    """Build a claim key consistent across different bots."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    date = int(message.date.timestamp())
    text = message.text or message.caption or ""
    content_hash = hashlib.md5(text.encode()).hexdigest()[:8]
    return f"{chat_id}_{user_id}_{date}_{content_hash}"


def nudge_claim_key(chat_id: int, now: datetime | None = None) -> str:
    """Build a per-chat, per-day key for the first daily nudge lock."""
    day = (now or datetime.now()).strftime("%Y-%m-%d")
    return f"nudge_{chat_id}_{day}"


async def try_claim_message(bot, bot_username: str, message: Message, emoji: str = "👀") -> bool:
    """Try to claim a message via an atomic file lock."""
    key = claim_key(message)
    claim_path = os.path.join(CLAIM_DIR, key)
    logging.info(f"[claim] {bot_username} trying to claim key={key}")

    await asyncio.sleep(__import__("random").uniform(0.5, 3.0))

    try:
        fd = os.open(claim_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, bot_username.encode())
        os.close(fd)
    except FileExistsError:
        logging.info(f"[claim] {bot_username} LOST claim for key={key}")
        return False

    logging.info(f"[claim] {bot_username} WON claim for key={key}")

    try:
        await bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except Exception:
        pass

    return True


def try_claim_nudge(chat_id: int, bot_username: str, now: datetime | None = None) -> bool:
    """Atomically claim the first daily nudge for a chat."""
    key = nudge_claim_key(chat_id, now=now)
    claim_path = os.path.join(CLAIM_DIR, key)
    logging.info(f"[nudge-claim] {bot_username} trying to claim key={key}")
    try:
        fd = os.open(claim_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, bot_username.encode())
        os.close(fd)
        logging.info(f"[nudge-claim] {bot_username} WON claim for key={key}")
        return True
    except FileExistsError:
        logging.info(f"[nudge-claim] {bot_username} LOST claim for key={key}")
        return False


def cleanup_old_claims(max_age: int = 300):
    """Remove claim files older than ``max_age`` seconds."""
    now = _time.time()
    try:
        for name in os.listdir(CLAIM_DIR):
            path = os.path.join(CLAIM_DIR, name)
            try:
                if os.path.getmtime(path) < (now - max_age):
                    os.remove(path)
            except OSError:
                pass
    except OSError:
        pass
