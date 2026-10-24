import asyncio
import logging
from shared_state import load_shared_last_activity, save_shared_last_activity, load_shared_bot_unmentioned, save_shared_bot_unmentioned
import random
from datetime import datetime

import json
import os

import agent_client
import claims
from claims import try_claim_nudge
from message_filter import clean_self_mentions, validate_bot_interaction


NUDGE_TASK_FRAME = (
    "СЛУЖЕБНАЯ ЗАДАЧА — это НЕ сообщение из чата, тебе никто ничего не писал, "
    "отвечать на эту задачу не нужно. В чате тишина, ты сам начинаешь разговор. "
    "История чата пуста: не ссылайся на прошлые разговоры, чужие идеи или «вчерашние темы» — "
    "если задача упоминает что-то такое, придумай конкретику сам. "
    "Если задача написана как реплика (например, с @упоминанием другого бота), "
    "это пример того, что ТЫ пишешь в чат от своего лица, а не сообщение тебе. "
    "Без конструкций «не X, а Y» и сравнений «X — как Y». "
    "Сообщение должно быть понятной законченной мыслью, которую человек сразу поймёт без пояснений. "
    "Выдай только готовое сообщение для чата.\n\n"
    "Задача: {prompt}"
)
RECENT_FRAME = (
    "\n\nНедавно в чате уже писали (ты или другие участники):\n{items}\n"
    "Не повторяй эти темы, факты, события и шутки — выбери что-то другое. "
    "Не бери и ту же тему другими словами: если недавно было про погоду, холод, район, автобусы — "
    "пиши о чём-то совсем другом. "
    "Если задача ведёт к тому же самому (тот же факт дня, та же тема), возьми из результата инструмента другое "
    "или выбери другую тему сам."
)
RECENT_KEEP = 10


def _recent_file(chat_id: int) -> str:
    return os.path.join(claims.NUDGE_DIR, f"{chat_id}.recent.json")


def load_recent_nudges(chat_id: int) -> list[str]:
    """Openers posted in this chat lately, by any bot (the history is cleared before a nudge)."""
    try:
        with open(_recent_file(chat_id), encoding="utf-8") as f:
            items = json.load(f)
        return [str(x) for x in items][-RECENT_KEEP:]
    except (OSError, ValueError):
        return []


def remember_nudge(chat_id: int, text: str) -> None:
    if not text:
        return
    items = load_recent_nudges(chat_id) + [text.strip()[:300]]
    try:
        os.makedirs(claims.NUDGE_DIR, exist_ok=True)
        tmp = _recent_file(chat_id) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items[-RECENT_KEEP:], f, ensure_ascii=False)
        os.replace(tmp, _recent_file(chat_id))
    except OSError as e:
        logging.warning(f"[nudge] could not save recent nudges: {e}")


def build_nudge_messages(prompt, recent=None):
    """Wrap a nudge prompt so the model executes it instead of replying to it.

    Kept as a system message so it is never stored in the chat history.
    ``recent`` lists openers already posted, so the same fact of the day
    doesn't come back a few hours later.
    """
    content = NUDGE_TASK_FRAME.format(prompt=prompt)
    if recent:
        content += RECENT_FRAME.format(items="\n".join(f"- {r}" for r in recent))
    return [{"role": "system", "content": content}]


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
    send_media_nudge=None,
):
    if force and force_chat_id is not None and force_message is not None:
        system_prompt = get_nudge_prompt_for_chat(force_chat_id)
        if send_media_nudge is not None:
            posted = await send_media_nudge(force_message, force_chat_id, system_prompt, is_message=True)
            if posted is not None:
                remember_nudge(force_chat_id, posted)
                mark_bot_replied(force_chat_id)
                return
        message_list = build_nudge_messages(system_prompt, load_recent_nudges(force_chat_id))
        raw_answer = await ask_agent(message_list, chat_id=force_chat_id)
        answer = clean_openai_reply(raw_answer)
        remember_nudge(force_chat_id, answer)
        mark_bot_replied(force_chat_id)
        await send_nudge_with_image(force_message, force_chat_id, answer, is_message=True)
        return

    last_reset = datetime.now()
    if nudge_loop_started_at_ref[0] is None:
        nudge_loop_started_at_ref[0] = datetime.now()
    logging.info(
        f"[nudge] Starting nudge loop. NUDGE_ENABLED_CHATS={nudge_enabled_chats}, "
        f"NUDGE_MINUTES=dynamic, Active hours: {active_start}-{active_end} {bot_timezone}"
    )

    quiet_logged = False
    while True:
        try:
            current_time = datetime.now(bot_timezone).time()
            if not (active_start <= current_time <= active_end):
                if not quiet_logged:  # log the transition once, not every minute all night
                    logging.info(f"[nudge] Outside active hours ({current_time:%H:%M}), sleeping until {active_start}")
                    quiet_logged = True
                await asyncio.sleep(nudge_check_interval)
                continue
            quiet_logged = False

            nudge_minutes = get_nudge_minutes()

            now = datetime.now()
            # Load shared state from file to sync with other bot processes
            shared_last_activity = load_shared_last_activity()
            for chat_id, shared_time in shared_last_activity.items():
                local_time = last_activity_time.get(chat_id)
                if local_time is None or shared_time > local_time:
                    last_activity_time[chat_id] = shared_time

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

                # Re-check after a random pause: another bot (or a person) may have spoken.
                # Bots see each other's messages natively, so shared last-activity covers them.
                await asyncio.sleep(random.uniform(5, 60))
                fresh = load_shared_last_activity().get(chat_id)
                if fresh and (datetime.now() - fresh).total_seconds() / 60 < nudge_minutes:
                    continue

                # Let another bot open the next thread if we opened the last one.
                if claims.last_nudger(chat_id) == bot_username:
                    await asyncio.sleep(random.uniform(90, 240))
                    fresh = load_shared_last_activity().get(chat_id)
                    if fresh and (datetime.now() - fresh).total_seconds() / 60 < nudge_minutes:
                        continue

                if not try_claim_nudge(chat_id, bot_username):
                    continue

                try:
                    agent_client.clear_history(chat_id)
                    system_prompt = get_nudge_prompt_for_chat(chat_id)
                    if send_media_nudge is not None:
                        posted = await send_media_nudge(bot, chat_id, system_prompt, is_message=False)
                        if posted is not None:
                            remember_nudge(chat_id, posted)
                            mark_bot_replied(chat_id)
                            continue
                    message_list = build_nudge_messages(system_prompt, load_recent_nudges(chat_id))
                    raw_answer = await ask_agent(message_list, chat_id=chat_id)
                    answer = clean_openai_reply(raw_answer)
                    
                    # Clean self-mentions and validate
                    answer = clean_self_mentions(answer, bot_username)
                    if not validate_bot_interaction(answer, bot_username):
                        logging.info(f"[nudge] Skipping invalid message in chat {chat_id}")
                        continue

                    remember_nudge(chat_id, answer)
                    mark_bot_replied(chat_id)
                    await send_nudge_with_image(bot, chat_id, answer, caption="", is_message=False)
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
