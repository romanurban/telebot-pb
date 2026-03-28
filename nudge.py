import asyncio
import logging
import random
from datetime import datetime

import agent_client
import bot_bus
from claims import try_claim_nudge


def get_random_nudge_prompt(nudge_system_prompts, nudge_prompt_history, history_len):
    """Return a random nudge prompt, avoiding recent repeats."""
    available_prompts = [p for p in nudge_system_prompts if p not in nudge_prompt_history]
    if not available_prompts:
        del nudge_prompt_history[:-1]
        available_prompts = [p for p in nudge_system_prompts if p not in nudge_prompt_history]
    prompt = random.choice(available_prompts)
    nudge_prompt_history.append(prompt)
    if len(nudge_prompt_history) > history_len:
        del nudge_prompt_history[:-history_len]
    return prompt


def get_nudge_prompt(
    *,
    bot_timezone,
    first_nudge_enabled,
    first_nudge_start,
    first_nudge_end,
    first_nudge_prompt,
    nudge_system_prompts,
    nudge_prompt_history,
    nudge_prompt_history_len,
):
    now = datetime.now(bot_timezone).time()
    if first_nudge_enabled and first_nudge_start <= now < first_nudge_end:
        return first_nudge_prompt
    return get_random_nudge_prompt(
        nudge_system_prompts,
        nudge_prompt_history,
        nudge_prompt_history_len,
    )


async def nudge_inactive_chats(
    *,
    force=False,
    force_chat_id=None,
    force_message=None,
    ask_agent,
    clean_openai_reply,
    mark_bot_replied,
    send_nudge_with_image,
    bot,
    bot_username,
    bot_timezone,
    active_start,
    active_end,
    get_nudge_minutes,
    nudge_enabled_chats,
    nudge_reset_interval,
    nudge_check_interval,
    last_activity_time,
    nudge_loop_started_at_ref,
    bot_unmentioned_count,
    get_nudge_prompt_for_chat,
):
    if force and force_chat_id is not None and force_message is not None:
        system_prompt = get_nudge_prompt_for_chat(force_chat_id)
        message_list = [{"role": "system", "content": system_prompt}]
        raw_answer = await ask_agent(message_list, chat_id=force_chat_id)
        answer = clean_openai_reply(raw_answer)
        mark_bot_replied(force_chat_id)
        await send_nudge_with_image(force_message, force_chat_id, answer, is_message=True)
        if answer:
            bot_bus.broadcast(force_chat_id, bot_username, answer)
        return

    last_reset = datetime.now()
    if nudge_loop_started_at_ref[0] is None:
        nudge_loop_started_at_ref[0] = datetime.now()
    logging.info(
        f"[nudge] Starting nudge loop. NUDGE_ENABLED_CHATS={nudge_enabled_chats}, "
        f"NUDGE_MINUTES=dynamic, Active hours: {active_start}-{active_end} {bot_timezone}"
    )

    while True:
        try:
            current_time = datetime.now(bot_timezone).time()
            if not (active_start <= current_time <= active_end):
                logging.info(f"[nudge] Outside active hours (current: {current_time}), sleeping...")
                await asyncio.sleep(nudge_check_interval)
                continue

            nudge_minutes = get_nudge_minutes()

            now = datetime.now()
            if (now - last_reset).total_seconds() >= nudge_reset_interval:
                bot_unmentioned_count.clear()
                last_reset = now

            all_chats = set(agent_client._histories.keys()) | set(last_activity_time.keys()) | set(nudge_enabled_chats)
            for chat_id in all_chats:
                if chat_id not in nudge_enabled_chats:
                    continue

                last_time = last_activity_time.get(chat_id)
                if last_time is None:
                    last_activity_time[chat_id] = now
                    continue

                minutes_passed = (now - last_time).total_seconds() / 60
                minutes_since_start = (now - nudge_loop_started_at_ref[0]).total_seconds() / 60
                if minutes_since_start < nudge_minutes:
                    continue
                if minutes_passed < nudge_minutes:
                    continue

                import time as _time
                last_bus_ts = bot_bus.last_message_time(chat_id)
                if last_bus_ts is not None:
                    bus_age_min = (_time.time() - last_bus_ts) / 60
                    if bus_age_min < nudge_minutes:
                        continue

                await asyncio.sleep(random.uniform(5, 60))
                last_bus_ts = bot_bus.last_message_time(chat_id)
                if last_bus_ts is not None:
                    bus_age_min = (_time.time() - last_bus_ts) / 60
                    if bus_age_min < nudge_minutes:
                        continue

                if not try_claim_nudge(chat_id, bot_username):
                    continue

                try:
                    agent_client.clear_history(chat_id)
                    system_prompt = get_nudge_prompt_for_chat(chat_id)
                    message_list = [{"role": "system", "content": system_prompt}]
                    raw_answer = await ask_agent(message_list, chat_id=chat_id)
                    answer = clean_openai_reply(raw_answer)
                    mark_bot_replied(chat_id)
                    await send_nudge_with_image(bot, chat_id, answer, caption="", is_message=False)
                    if answer:
                        bot_bus.broadcast(chat_id, bot_username, answer)
                except Exception as e:
                    logging.error(f"[nudge] Error sending nudge to chat {chat_id}: {e}", exc_info=True)
                    continue

            await asyncio.sleep(nudge_check_interval)
        except asyncio.CancelledError:
            logging.info('[nudge] Nudge loop cancelled, shutting down.')
            raise
        except Exception as e:
            logging.error(f"[nudge] Unexpected error in nudge loop: {e}", exc_info=True)
            await asyncio.sleep(nudge_check_interval)
