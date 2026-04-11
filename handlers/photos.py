from datetime import datetime

from bot_context import BotContext


async def handle_photo_message(message, ctx: BotContext):
    if message.from_user and message.from_user.is_bot:
        return
    if not await ctx.try_claim_message(message):
        return

    photo = message.photo[-1]
    photo_bytes = await ctx.bot.download(photo)
    image_bytes = photo_bytes.read()
    prompt = message.caption if message.caption else ctx.image_default_prompt
    chat_id = message.chat.id
    ctx.last_activity_time[chat_id] = datetime.now()
    ctx.messages_since_bot_reply[chat_id] = ctx.messages_since_bot_reply.get(chat_id, 0) + 1
    ctx.bus_last_reply.pop(chat_id, None)
    answer = await ctx.ask_openai_image(image_bytes, prompt, chat_id=chat_id)
    ctx.mark_bot_replied(chat_id)
    await message.reply(answer)
