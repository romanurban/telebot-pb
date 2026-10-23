import hashlib
import logging
import os
import re
from datetime import datetime

import agent_client
from aiogram.enums import ParseMode
from aiogram.types.input_file import BufferedInputFile, FSInputFile
import claims
from bot_context import BotContext
from handlers import bot_messages

HUMAN_REPLY_PERCENT = 70  # chance that one bot answers an unaddressed human message


async def handle_text_message(message, ctx: BotContext):
    if not message.text:
        return
    if message.from_user and message.from_user.is_bot:
        # Other bots' messages (Bot API 10.0 bot-to-bot delivery)
        await bot_messages.handle_bot_text(message, ctx)
        return

    chat_id = message.chat.id
    bot_messages.reset_chain(chat_id)  # a human wrote: bots may talk again
    username = message.from_user.username or str(message.from_user.id)
    logging.info(f"[in] {chat_id} {username}: {message.text.replace(chr(10), ' / ')[:500]}")

    inactivity_clear_minutes = 30
    last_time = ctx.last_activity_time.get(chat_id)
    ctx.last_activity_time[chat_id] = datetime.now()
    ctx.messages_since_bot_reply[chat_id] = ctx.messages_since_bot_reply.get(chat_id, 0) + 1

    if last_time and (datetime.now() - last_time).total_seconds() / 60 >= inactivity_clear_minutes:
        agent_client.clear_history(chat_id)
        logging.info(f"[handle] Cleared stale history for chat {chat_id} after inactivity")

    command = message.text.strip().split()[0].split('@')[0].lower()
    if command == '/nudge':
        raw_command = message.text.strip().split()[0].lower()
        if '@' not in raw_command:
            if not await ctx.try_claim_message(message):
                return
        await ctx.nudge_inactive_chats(force=True, force_chat_id=chat_id, force_message=message)
        return

    if command == '/potd':
        if not await ctx.try_claim_message(message):
            return
        date_arg = message.text.strip().split(maxsplit=1)
        date = date_arg[1] if len(date_arg) > 1 else ''
        try:
            img_data, caption = await ctx.get_picture_of_the_day(date)
            styled = await ctx.style_caption(caption, chat_id=chat_id)
            photo = BufferedInputFile(img_data, filename='potd.jpg') if isinstance(img_data, bytes) else FSInputFile(img_data)
            await message.answer_photo(photo, caption=styled)
            if isinstance(img_data, str):
                try:
                    os.remove(img_data)
                except Exception:
                    pass
            ctx.mark_bot_replied(chat_id)
        except Exception as e:
            await message.answer(f'Error: {e}')
        return

    if command == '/meme':
        if not await ctx.try_claim_message(message):
            return
        try:
            path = await ctx.retrieve_joke()
            await message.answer_photo(FSInputFile(path))
            ctx.mark_bot_replied(chat_id)
            try:
                os.remove(path)
            except Exception:
                pass
        except Exception as e:
            await message.answer(f'Error: {e}')
        return

    if command == '/fact':
        if not await ctx.try_claim_message(message):
            return
        try:
            fact = await ctx.retrieve_fact()
            await message.answer(fact)
            ctx.mark_bot_replied(chat_id)
        except Exception as e:
            await message.answer(f'Error: {e}')
        return

    if command == '/voice':
        if not await ctx.try_claim_message(message):
            return
        parts = message.text.strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer('Usage: /voice <text>')
            return
        text = parts[1]
        try:
            path = await ctx.generate_voice_file(text)
            await message.answer_voice(FSInputFile(path))
            ctx.mark_bot_replied(chat_id)
            try:
                os.remove(path)
            except Exception:
                pass
        except Exception as e:
            await message.answer(f'Error: {e}')
        return

    mention_tag = f'@{ctx.bot_username}'.lower()
    mentioned = mention_tag in message.text.lower() or (ctx.name_mention_re is not None and bool(ctx.name_mention_re.search(message.text)))
    tool_choice = 'generate_voice' if (mentioned and ctx.needs_voice_tool(message.text)) else None

    if mentioned:
        prompt = message.text.strip()  # keep our @handle: cutting it out left gaps the model copied
        answer = await ctx.ask_openai(prompt, username=username, chat_id=chat_id, tool_choice=tool_choice)
        if not answer:
            return  # LLM failed: say nothing rather than post an error
        voice = await ctx.extract_voice_file(answer)
        if voice is None and tool_choice == 'generate_voice':
            try:
                path = await ctx.generate_voice_file(answer)
                voice = (path, '')
            except Exception:
                voice = None

        if voice:
            path, text = voice
            await message.answer_voice(FSInputFile(path))
            ctx.mark_bot_replied(chat_id)
            try:
                os.remove(path)
            except Exception:
                pass
            answer = text

        json_img = await ctx.extract_json_image(answer)
        if json_img:
            img_data, raw_caption = json_img
            styled = raw_caption
            if raw_caption:
                try:
                    styled = await ctx.style_caption(raw_caption, chat_id=chat_id)
                except Exception:
                    pass
            photo = BufferedInputFile(img_data, filename='assistant.jpg') if isinstance(img_data, bytes) else FSInputFile(img_data)
            await message.answer_photo(photo, caption=styled)
            if isinstance(img_data, str):
                try:
                    os.remove(img_data)
                except Exception:
                    pass
            ctx.mark_bot_replied(chat_id)
        else:
            ctx.mark_bot_replied(chat_id)
            await message.answer(answer, parse_mode=ParseMode.HTML)

        ctx.bot_unmentioned_count[chat_id] = 0
        return

    if re.search(r'@\w+bot\b', message.text, re.IGNORECASE):
        return

    bot_unmentioned = ctx.bot_unmentioned_count.get(chat_id, 0)
    if bot_unmentioned >= ctx.max_unmentioned_replies:
        return

    # One bot answers a person most of the time. Every bot computes the same
    # coin from the message itself, then a claim picks the single responder
    # (the others may still join afterwards through the bot-to-bot rules).
    # If this bot spoke moments ago, the person is talking with it: skip the coin.
    key = claims.claim_key(message)
    last_bot_time = ctx.last_bot_reply_time.get(chat_id)
    in_conversation = bool(
        last_bot_time and (datetime.now() - last_bot_time).total_seconds() <= ctx.recent_activity_seconds
    )
    if not in_conversation and int(hashlib.md5(key.encode()).hexdigest(), 16) % 100 >= HUMAN_REPLY_PERCENT:
        return
    if not claims.try_claim_key(f"human_{key}", ctx.bot_username):
        return

    ctx.bot_unmentioned_count[chat_id] = bot_unmentioned + 1
    formatted_msg = f'{username}: {message.text}'
    message_list = [
        {'role': 'user', 'content': formatted_msg},
        {'role': 'system', 'content': ctx.chat_react_prompt},
    ]
    try:
        raw_answer = await ctx.ask_agent(message_list, chat_id=chat_id, tool_choice=tool_choice)
        answer = ctx.clean_openai_reply(raw_answer)
    except Exception as e:
        logging.error(f"[handle] LLM error in chat {chat_id}: {e}")
        return
    if not answer:
        return

    voice = await ctx.extract_voice_file(answer)
    if voice:
        path, text = voice
        await message.answer_voice(FSInputFile(path))
        ctx.mark_bot_replied(chat_id)
        try:
            os.remove(path)
        except Exception:
            pass
        answer = text

    json_img = await ctx.extract_json_image(answer)
    if json_img:
        img_data, raw_caption = json_img
        styled = raw_caption
        if raw_caption:
            try:
                styled = await ctx.style_caption(raw_caption, chat_id=chat_id)
            except Exception:
                pass
        photo = BufferedInputFile(img_data, filename='assistant.jpg') if isinstance(img_data, bytes) else FSInputFile(img_data)
        await message.answer_photo(photo, caption=styled)
        if isinstance(img_data, str):
            try:
                os.remove(img_data)
            except Exception:
                pass
        ctx.mark_bot_replied(chat_id)
    else:
        ctx.mark_bot_replied(chat_id)
        await message.answer(answer, parse_mode=ParseMode.HTML)

