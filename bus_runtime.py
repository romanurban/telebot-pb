import asyncio
import logging
import os
import random
import re
from datetime import datetime

import bot_bus
from message_filter import clean_self_mentions, validate_bot_interaction


BOT_BUS_POLL_INTERVAL = 3
BOT_BUS_REPLY_COOLDOWN = 60


def _line_aligned_size(path: str) -> int:
    """Return the byte offset of the end of the last complete line in *path*.

    If the file ends with a newline (the normal case for JSONL), this equals
    the file size.  If a concurrent writer appended a partial line, we back
    up so the next poll starts at a full line boundary.
    """
    size = os.path.getsize(path)
    if size == 0:
        return 0
    with open(path, "rb") as f:
        f.seek(size - 1)
        if f.read(1) == b"\n":
            return size
        # Last line is incomplete — scan backwards for the previous newline.
        pos = size - 2
        while pos >= 0:
            f.seek(pos)
            if f.read(1) == b"\n":
                return pos + 1
            pos -= 1
    return 0


def initialize_bus_positions(bus_positions: dict[int, int]) -> None:
    """Initialize bot bus and seek existing files to end of last complete line."""
    bot_bus.init_bus()
    try:
        for fname in os.listdir(bot_bus.BOT_BUS_DIR):
            if not fname.endswith('.jsonl'):
                continue
            try:
                chat_id = int(fname[:-6])
            except ValueError:
                continue
            path = os.path.join(bot_bus.BOT_BUS_DIR, fname)
            bus_positions[chat_id] = _line_aligned_size(path)
    except FileNotFoundError:
        pass


def _should_probabilistic_reply(messages_since_bot_reply: int, had_recent_bot_reply: bool, is_nudge: bool = False) -> bool:
    # Always reply to nudges to start conversations
    if is_nudge:
        return True
    
    if had_recent_bot_reply:
        return True
    if messages_since_bot_reply <= 0:
        return random.random() < 0.1
    if messages_since_bot_reply == 1:
        return random.random() < 0.25
    if messages_since_bot_reply == 2:
        return random.random() < 0.5
    if messages_since_bot_reply == 3:
        return random.random() < 0.75
    return True


async def poll_bot_bus(
    *,
    bot,
    bot_username: str,
    bus_positions: dict[int, int],
    bus_last_reply: dict[int, float],
    last_activity_time: dict,
    name_mention_re,
    inject_external_message,
    ask_openai,
    ask_agent,
    clean_openai_reply,
    mark_bot_replied,
    parse_mode,
    messages_since_bot_reply: dict | None = None,
    bot_unmentioned_count: dict | None = None,
    last_bot_reply_time: dict | None = None,
    max_unmentioned_replies: int = 3,
    recent_activity_seconds: int = 30,
    chat_react_prompt: str = '',
) -> None:
    """Poll the bot bus for messages from other bots."""
    mention_tag = f"@{bot_username}".lower()
    bare_username = bot_username.lower()
    messages_since_bot_reply = messages_since_bot_reply or {}
    bot_unmentioned_count = bot_unmentioned_count or {}
    last_bot_reply_time = last_bot_reply_time or {}

    while True:
        try:
            try:
                files = os.listdir(bot_bus.BOT_BUS_DIR)
            except FileNotFoundError:
                await asyncio.sleep(BOT_BUS_POLL_INTERVAL)
                continue

            for fname in files:
                if not fname.endswith('.jsonl'):
                    continue
                try:
                    chat_id = int(fname[:-6])
                except ValueError:
                    continue

                last_pos = bus_positions.get(chat_id, 0)
                messages, new_pos = bot_bus.poll(chat_id, bot_username, last_pos)
                bus_positions[chat_id] = new_pos

                import time as _time

                for msg in messages:
                    other_bot = msg.get('bot', '')
                    text = msg.get('text', '')
                    via_bus = bool(msg.get('via_bus'))
                    if not text:
                        continue

                    inject_external_message(chat_id, other_bot, text)
                    last_activity_time[chat_id] = datetime.now()
                    messages_since_bot_reply[chat_id] = messages_since_bot_reply.get(chat_id, 0) + 1

                    text_lower = text.lower()
                    mentioned = (
                        mention_tag in text_lower
                        or bare_username in text_lower
                        or (name_mention_re is not None and bool(name_mention_re.search(text)))
                    )

                    now_ts = _time.time()
                    last_reply_ts = bus_last_reply.get(chat_id, 0)
                    # Shorter cooldown for nudges to encourage conversation
                    cooldown = BOT_BUS_REPLY_COOLDOWN // 3 if not via_bus else BOT_BUS_REPLY_COOLDOWN
                    if now_ts - last_reply_ts < cooldown:
                        logging.info(f"[bot_bus] Skipping reply in chat {chat_id} — cooldown (using {cooldown}s)")
                        continue

                    if mentioned:
                        logging.info(f"[bot_bus] Bot {other_bot} mentioned us in chat {chat_id}")
                        prompt = re.sub(
                            re.escape(mention_tag), '', text,
                            count=1, flags=re.IGNORECASE,
                        ).strip()
                        answer = await ask_openai(prompt, username=other_bot, chat_id=chat_id)
                        if answer:
                            # Clean self-mentions and validate
                            answer = clean_self_mentions(answer, bot_username)
                            if validate_bot_interaction(answer, bot_username):
                                try:
                                    await bot.send_message(chat_id, answer, parse_mode=parse_mode)
                                except Exception as e:
                                    logging.error(f"[bot_bus] Failed to send reply to {chat_id}: {e}")
                                mark_bot_replied(chat_id)
                                bus_last_reply[chat_id] = _time.time()
                                bot_bus.broadcast(chat_id, bot_username, answer, via_bus=True)
                        continue

                    # Check if this is a nudge (message not via_bus = fresh nudge)
                    is_nudge = not via_bus
                    
                    if via_bus:
                        continue

                    bot_unmentioned = bot_unmentioned_count.get(chat_id, 0)
                    # For nudges, allow more replies to get conversation started
                    reply_limit = max_unmentioned_replies * 2 if is_nudge else max_unmentioned_replies
                    if bot_unmentioned >= reply_limit:
                        continue

                    recent_reply = False
                    last_bot_time = last_bot_reply_time.get(chat_id)
                    if last_bot_time and (datetime.now() - last_bot_time).total_seconds() <= recent_activity_seconds:
                        recent_reply = True

                    non_bot_count = messages_since_bot_reply.get(chat_id, 0)
                    if not _should_probabilistic_reply(non_bot_count, recent_reply, is_nudge):
                        continue

                    bot_unmentioned_count[chat_id] = bot_unmentioned + 1
                    formatted_msg = f'{other_bot}: {text}'
                    message_list = [
                        {'role': 'user', 'content': formatted_msg},
                        {'role': 'system', 'content': chat_react_prompt},
                    ]
                    raw_answer = await ask_agent(message_list, chat_id=chat_id)
                    answer = clean_openai_reply(raw_answer)
                    if answer:
                        # Clean self-mentions and validate
                        answer = clean_self_mentions(answer, bot_username)
                        if validate_bot_interaction(answer, bot_username):
                            try:
                                await bot.send_message(chat_id, answer, parse_mode=parse_mode)
                            except Exception as e:
                                logging.error(f"[bot_bus] Failed to send probabilistic reply to {chat_id}: {e}")
                            mark_bot_replied(chat_id)
                            bus_last_reply[chat_id] = _time.time()
                            bot_bus.broadcast(chat_id, bot_username, answer, via_bus=True)

            await asyncio.sleep(BOT_BUS_POLL_INTERVAL)
        except asyncio.CancelledError:
            logging.info('[bot_bus] Poll loop cancelled.')
            raise
        except Exception as e:
            logging.error(f"[bot_bus] Error in poll loop: {e}", exc_info=True)
            await asyncio.sleep(BOT_BUS_POLL_INTERVAL)
