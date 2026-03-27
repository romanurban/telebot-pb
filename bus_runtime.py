import asyncio
import logging
import os
import re
from datetime import datetime

import bot_bus


BOT_BUS_POLL_INTERVAL = 3
BOT_BUS_REPLY_COOLDOWN = 60


def initialize_bus_positions(bus_positions: dict[int, int]) -> None:
    """Initialize bot bus and seek existing files to EOF."""
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
            bus_positions[chat_id] = os.path.getsize(path)
    except FileNotFoundError:
        pass


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
    mark_bot_replied,
    parse_mode,
) -> None:
    """Poll the bot bus for messages from other bots."""
    mention_tag = f"@{bot_username}".lower()
    bare_username = bot_username.lower()

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
                    if not text:
                        continue

                    inject_external_message(chat_id, other_bot, text)
                    last_activity_time[chat_id] = datetime.now()

                    text_lower = text.lower()
                    mentioned = (
                        mention_tag in text_lower
                        or bare_username in text_lower
                        or (name_mention_re is not None and bool(name_mention_re.search(text)))
                    )
                    if not mentioned:
                        continue

                    now_ts = _time.time()
                    last_reply_ts = bus_last_reply.get(chat_id, 0)
                    if now_ts - last_reply_ts < BOT_BUS_REPLY_COOLDOWN:
                        logging.info(f"[bot_bus] Skipping reply in chat {chat_id} — cooldown")
                        continue

                    logging.info(f"[bot_bus] Bot {other_bot} mentioned us in chat {chat_id}")
                    prompt = re.sub(
                        re.escape(mention_tag), '', text,
                        count=1, flags=re.IGNORECASE,
                    ).strip()
                    answer = await ask_openai(prompt, username=other_bot, chat_id=chat_id)
                    if answer:
                        try:
                            await bot.send_message(chat_id, answer, parse_mode=parse_mode)
                        except Exception as e:
                            logging.error(f"[bot_bus] Failed to send reply to {chat_id}: {e}")
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
