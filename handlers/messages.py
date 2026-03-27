import logging
import os
import random
import re
from datetime import datetime

import agent_client
import bot_bus
from aiogram.enums import ParseMode
from aiogram.types.input_file import BufferedInputFile, FSInputFile


async def handle_text_message(
    message,
    *,
    bot_username,
    name_mention_re,
    image_default_prompt,
    chat_react_prompt,
    max_unmentioned_replies,
    recent_activity_seconds,
    last_activity_time,
    messages_since_bot_reply,
    bot_unmentioned_count,
    last_bot_reply_time,
    try_claim_message,
    nudge_inactive_chats,
    get_picture_of_the_day,
    style_caption,
    retrieve_joke,
    retrieve_fact,
    generate_voice_file,
    ask_openai,
    ask_agent,
    clean_openai_reply,
    mark_bot_replied,
    extract_voice_file,
    extract_json_image,
    needs_voice_tool,
):
    if message.from_user and message.from_user.is_bot:
        return
    if not message.text:
        return

    chat_id = message.chat.id
    username = message.from_user.username or str(message.from_user.id)

    inactivity_clear_minutes = 30
    last_time = last_activity_time.get(chat_id)
    last_activity_time[chat_id] = datetime.now()
    messages_since_bot_reply[chat_id] = messages_since_bot_reply.get(chat_id, 0) + 1

    if last_time and (datetime.now() - last_time).total_seconds() / 60 >= inactivity_clear_minutes:
        agent_client.clear_history(chat_id)
        logging.info(f"[handle] Cleared stale history for chat {chat_id} after inactivity")

    command = message.text.strip().split()[0].split('@')[0].lower()
    if command == '/nudge':
        raw_command = message.text.strip().split()[0].lower()
        if '@' not in raw_command:
            if not await try_claim_message(message):
                return
        await nudge_inactive_chats(force=True, force_chat_id=chat_id, force_message=message)
        return

    if command == '/potd':
        if not await try_claim_message(message):
            return
        date_arg = message.text.strip().split(maxsplit=1)
        date = date_arg[1] if len(date_arg) > 1 else ''
        try:
            img_data, caption = await get_picture_of_the_day(date)
            styled = await style_caption(caption, chat_id=chat_id)
            photo = BufferedInputFile(img_data, filename='potd.jpg') if isinstance(img_data, bytes) else FSInputFile(img_data)
            await message.answer_photo(photo, caption=styled)
            if isinstance(img_data, str):
                try:
                    os.remove(img_data)
                except Exception:
                    pass
            mark_bot_replied(chat_id)
        except Exception as e:
            await message.answer(f'Error: {e}')
        return

    if command == '/meme':
        if not await try_claim_message(message):
            return
        try:
            path = await retrieve_joke()
            await message.answer_photo(FSInputFile(path))
            mark_bot_replied(chat_id)
            try:
                os.remove(path)
            except Exception:
                pass
        except Exception as e:
            await message.answer(f'Error: {e}')
        return

    if command == '/fact':
        if not await try_claim_message(message):
            return
        try:
            fact = await retrieve_fact()
            await message.answer(fact)
            mark_bot_replied(chat_id)
        except Exception as e:
            await message.answer(f'Error: {e}')
        return

    if command == '/voice':
        if not await try_claim_message(message):
            return
        parts = message.text.strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer('Usage: /voice <text>')
            return
        text = parts[1]
        try:
            path = await generate_voice_file(text)
            await message.answer_voice(FSInputFile(path))
            mark_bot_replied(chat_id)
            try:
                os.remove(path)
            except Exception:
                pass
        except Exception as e:
            await message.answer(f'Error: {e}')
        return

    mention_tag = f'@{bot_username}'.lower()
    mentioned = mention_tag in message.text.lower() or (name_mention_re is not None and bool(name_mention_re.search(message.text)))
    tool_choice = 'generate_voice' if (mentioned and needs_voice_tool(message.text)) else None

    if mentioned:
        prompt = re.sub(re.escape(mention_tag), '', message.text, count=1, flags=re.IGNORECASE).strip()
        answer = await ask_openai(prompt, username=username, chat_id=chat_id, tool_choice=tool_choice)
        voice = await extract_voice_file(answer)
        if voice is None and tool_choice == 'generate_voice':
            try:
                path = await generate_voice_file(answer)
                voice = (path, '')
            except Exception:
                voice = None

        if voice:
            path, text = voice
            await message.answer_voice(FSInputFile(path))
            mark_bot_replied(chat_id)
            try:
                os.remove(path)
            except Exception:
                pass
            answer = text

        json_img = await extract_json_image(answer)
        if json_img:
            img_data, raw_caption = json_img
            styled = raw_caption
            if raw_caption:
                try:
                    styled = await style_caption(raw_caption, chat_id=chat_id)
                except Exception:
                    pass
            photo = BufferedInputFile(img_data, filename='assistant.jpg') if isinstance(img_data, bytes) else FSInputFile(img_data)
            await message.answer_photo(photo, caption=styled)
            if isinstance(img_data, str):
                try:
                    os.remove(img_data)
                except Exception:
                    pass
            mark_bot_replied(chat_id)
        else:
            mark_bot_replied(chat_id)
            await message.answer(answer, parse_mode=ParseMode.HTML)

        if answer:
            bot_bus.broadcast(chat_id, bot_username, answer)
        bot_unmentioned_count[chat_id] = 0
        return

    if re.search(r'@\w+bot\b', message.text, re.IGNORECASE):
        return

    non_bot_count = messages_since_bot_reply.get(chat_id, 0)
    bot_unmentioned = bot_unmentioned_count.get(chat_id, 0)
    if bot_unmentioned >= max_unmentioned_replies:
        return

    should_respond = False
    now = datetime.now()
    last_bot_time = last_bot_reply_time.get(chat_id)
    if last_bot_time and (now - last_bot_time).total_seconds() <= recent_activity_seconds:
        should_respond = True
    else:
        if non_bot_count == 0:
            should_respond = random.random() < 0.1
        elif non_bot_count == 1:
            should_respond = random.random() < 0.25
        elif non_bot_count == 2:
            should_respond = random.random() < 0.5
        elif non_bot_count == 3:
            should_respond = random.random() < 0.75
        else:
            should_respond = True

    if not should_respond:
        return

    bot_unmentioned_count[chat_id] = bot_unmentioned + 1
    formatted_msg = f'{username}: {message.text}'
    message_list = [
        {'role': 'user', 'content': formatted_msg},
        {'role': 'system', 'content': chat_react_prompt},
    ]
    try:
        raw_answer = await ask_agent(message_list, chat_id=chat_id, tool_choice=tool_choice)
        answer = clean_openai_reply(raw_answer)
    except Exception as e:
        answer = f'LLM error: {e}'

    voice = await extract_voice_file(answer)
    if voice:
        path, text = voice
        await message.answer_voice(FSInputFile(path))
        mark_bot_replied(chat_id)
        try:
            os.remove(path)
        except Exception:
            pass
        answer = text

    json_img = await extract_json_image(answer)
    if json_img:
        img_data, raw_caption = json_img
        styled = raw_caption
        if raw_caption:
            try:
                styled = await style_caption(raw_caption, chat_id=chat_id)
            except Exception:
                pass
        photo = BufferedInputFile(img_data, filename='assistant.jpg') if isinstance(img_data, bytes) else FSInputFile(img_data)
        await message.answer_photo(photo, caption=styled)
        if isinstance(img_data, str):
            try:
                os.remove(img_data)
            except Exception:
                pass
        mark_bot_replied(chat_id)
    else:
        mark_bot_replied(chat_id)
        await message.answer(answer, parse_mode=ParseMode.HTML)

    if answer:
        bot_bus.broadcast(chat_id, bot_username, answer)
