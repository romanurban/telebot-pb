from datetime import datetime


async def handle_photo_message(
    message,
    *,
    bot,
    image_default_prompt,
    last_activity_time,
    messages_since_bot_reply,
    try_claim_message,
    ask_openai_image,
    mark_bot_replied,
):
    if message.from_user and message.from_user.is_bot:
        return
    if not await try_claim_message(message):
        return

    photo = message.photo[-1]
    photo_bytes = await bot.download(photo)
    image_bytes = photo_bytes.read()
    prompt = message.caption if message.caption else image_default_prompt
    chat_id = message.chat.id
    last_activity_time[chat_id] = datetime.now()
    messages_since_bot_reply[chat_id] = messages_since_bot_reply.get(chat_id, 0) + 1
    answer = await ask_openai_image(image_bytes, prompt, chat_id=chat_id)
    mark_bot_replied(chat_id)
    await message.reply(answer)
