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


NUDGE_DIR = "/tmp/telebot_nudges"   # outside CLAIM_DIR: cleanup_old_claims wipes that every 5 min
NUDGE_MIN_GAP = 60 * 60             # seconds between nudges in one chat, across all bots


def try_claim_nudge(chat_id: int, bot_username: str, now: datetime | None = None,
                    min_gap: int = NUDGE_MIN_GAP) -> bool:
    """Claim the right to nudge ``chat_id``; True for one bot per ``min_gap`` window.

    The old per-day key lived in CLAIM_DIR, which is cleaned every 5 minutes,
    so the same "daily" key could be won again and again. Here the time of the
    last nudge is kept in its own directory and checked under a short lock.
    """
    ts = (now or datetime.now()).timestamp()
    os.makedirs(NUDGE_DIR, exist_ok=True)
    lock = os.path.join(NUDGE_DIR, f"{chat_id}.lock")
    last_file = os.path.join(NUDGE_DIR, f"{chat_id}.last")
    try:
        if os.path.exists(lock) and _time.time() - os.path.getmtime(lock) > 60:
            os.remove(lock)  # stale lock from a crashed process
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except (FileExistsError, OSError):
        logging.info(f"[nudge-claim] {bot_username} LOST (another bot is claiming) chat={chat_id}")
        return False
    try:
        last = 0.0
        try:
            with open(last_file) as f:
                last = float(f.read().split()[0])
        except (OSError, ValueError, IndexError):
            pass
        if ts - last < min_gap:
            logging.info(f"[nudge-claim] {bot_username} LOST: last nudge {int(ts - last)}s ago in chat={chat_id}")
            return False
        with open(last_file, "w") as f:
            f.write(f"{ts} {bot_username}")
        logging.info(f"[nudge-claim] {bot_username} WON chat={chat_id}")
        return True
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def release_nudge(chat_id: int, bot_username: str) -> None:
    """Give the nudge slot back when our nudge produced nothing, so another bot
    can open a thread now instead of the chat staying silent for NUDGE_MIN_GAP."""
    last_file = os.path.join(NUDGE_DIR, f"{chat_id}.last")
    try:
        with open(last_file) as f:
            parts = f.read().split()
        if len(parts) > 1 and parts[1] == bot_username:
            with open(last_file, "w") as f:
                f.write("0 released")
            logging.info(f"[nudge-claim] {bot_username} released chat={chat_id}")
    except OSError:
        pass


def last_nudger(chat_id: int) -> str | None:
    """Username of the bot that nudged ``chat_id`` last, if known."""
    try:
        with open(os.path.join(NUDGE_DIR, f"{chat_id}.last")) as f:
            parts = f.read().split()
        return parts[1] if len(parts) > 1 else None
    except OSError:
        return None


def try_claim_key(key: str, bot_username: str) -> bool:
    """Atomically claim an arbitrary key; True for exactly one bot."""
    claim_path = os.path.join(CLAIM_DIR, key)
    try:
        fd = os.open(claim_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, bot_username.encode())
        os.close(fd)
        return True
    except FileExistsError:
        logging.info(f"[claim] {bot_username} LOST claim for key={key}")
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
