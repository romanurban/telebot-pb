import os
import logging
import random
from aiogram import Bot, Dispatcher
from aiogram.types import Message, ReactionTypeEmoji
from aiogram.enums import ParseMode
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram import F
import asyncio
import dotenv

dotenv.load_dotenv()

from agent_client import (
    openai_client,
    create_thread_with_system_prompt,
    ask_agent,
    USE_OPENROUTER,
)
import agent_client
from nudge import nudge_inactive_chats as run_nudge_loop, get_nudge_prompt as build_nudge_prompt
from bot_context import BotContext
from handlers.messages import handle_text_message
from handlers.photos import handle_photo_message
from handlers import bot_messages
from message_filter import thin_emoji, tidy_reply, fix_mentions, echo_problem, latin_problem
import base64
from tempfile import NamedTemporaryFile
import aiohttp
from datetime import datetime, timedelta
import io
from aiogram.types.input_file import BufferedInputFile, FSInputFile
import json
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession
from config import (
    TELEGRAM_TOKEN,
    OPENAI_API_KEY,
    BOT_USERNAME,
    SYSTEM_PROMPT_FILE,
    _NUDGE_MINUTES_BASE as NUDGE_MINUTES,
    get_nudge_minutes,
    IMAGE_GEN_MODEL,
    MCP_SERVER_URL,
    NUDGE_SYSTEM_PROMPTS,
    IMAGE_DEFAULT_PROMPT,
    CHAT_REACT_PROMPT,
    IMAGE_GEN_INPUT_PROMPT,
    NAME_MENTION_RE,
    CYRILLIC_ONLY,
    FIRST_NUDGE_PROMPT,
    FIRST_NUDGE_START,
    FIRST_NUDGE_END,
    FIRST_NUDGE_ENABLED,
    NUDGE_ENABLED_CHATS,
    NUDGE_PROMPT_HISTORY_LEN,
    nudge_prompt_history,
    BOT_TIMEZONE,
    ACTIVE_START,
    ACTIVE_END,
    MAX_UNMENTIONED_REPLIES,
    IMAGE_SEND_CHANCE,
    NUDGE_RESET_INTERVAL,
    NUDGE_CHECK_INTERVAL,
    RECENT_ACTIVITY_SECONDS,
    get_openai_images_client,
    validate_environment,
    is_active_hours,
)
from state import (
    last_activity_time,
    nudge_loop_started_at,
    last_bot_reply_time,
    bot_unmentioned_count,
    messages_since_bot_reply,
)
from shared_state import save_shared_last_activity, save_shared_bot_unmentioned
from claims import claim_key as _claims_claim_key, try_claim_message as _try_claim_message, cleanup_old_claims, CLAIM_DIR

_openai_images_client = get_openai_images_client()

def _needs_voice_tool(text: str) -> bool:
    """Return True if ``text`` requests a voice message."""
    tl = text.lower()
    words = tl.split()
    return any(word.startswith("голос") for word in words)

# === GLOBAL STATE ===
# Note: chat histories now managed in agent_client._histories

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Per-request noise: one line per poll / HTTP call drowned the conversation.
for _noisy in ("aiogram.event", "httpx", "openai._base_client", "mcp.client.sse"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()


class OutgoingLog(BaseRequestMiddleware):
    """Log every message the bot posts, so a conversation can be reviewed later."""

    async def __call__(self, make_request, bot, method):
        text = getattr(method, "text", None) or getattr(method, "caption", None)
        kind = type(method).__name__
        if kind.startswith("Send") and kind != "SendChatAction":
            chat_id = getattr(method, "chat_id", "?")
            logging.info(f"[out] {chat_id} {kind}: {(text or '').replace(chr(10), ' / ')[:500]}")
        return await make_request(bot, method)


bot.session.middleware(OutgoingLog())


PHOTO_MARK = "прислал(а) в чат картинку"


def check_reply(reply: str, answered: str | None) -> str | None:
    """Mechanical guard for what prompt rules alone don't stop on the free models."""
    import re
    if re.search(r"^\s*\{|https?://|/tmp/", reply or ""):
        return None  # tool payload (meme URL, image JSON, voice file): not chat prose
    text = clean_openai_reply(reply)
    if answered and PHOTO_MARK in answered:
        answered = None  # reusing words of a picture description is expected
    if CYRILLIC_ONLY:
        problem = latin_problem(text)
        if problem:
            return problem
    return echo_problem(text, answered)


agent_client.reply_checker = check_reply


def validate_environment():
    import config as _config

    _config.TELEGRAM_TOKEN = TELEGRAM_TOKEN
    _config.OPENAI_API_KEY = OPENAI_API_KEY
    _config.BOT_USERNAME = BOT_USERNAME
    _config.SYSTEM_PROMPT_FILE = SYSTEM_PROMPT_FILE
    return _config.validate_environment()


def is_active_hours():
    return ACTIVE_START <= datetime.now(BOT_TIMEZONE).time() <= ACTIVE_END


def get_random_nudge_prompt():
    from nudge import get_random_nudge_prompt as _get_random_nudge_prompt

    return _get_random_nudge_prompt(
        NUDGE_SYSTEM_PROMPTS,
        nudge_prompt_history,
        NUDGE_PROMPT_HISTORY_LEN,
    )


def get_nudge_prompt(chat_id: int) -> str:
    _ = chat_id
    return build_nudge_prompt(
        bot_timezone=BOT_TIMEZONE,
        first_nudge_enabled=FIRST_NUDGE_ENABLED,
        first_nudge_start=FIRST_NUDGE_START,
        first_nudge_end=FIRST_NUDGE_END,
        first_nudge_prompt=FIRST_NUDGE_PROMPT,
        nudge_system_prompts=NUDGE_SYSTEM_PROMPTS,
        nudge_prompt_history=nudge_prompt_history,
        nudge_prompt_history_len=NUDGE_PROMPT_HISTORY_LEN,
    )


def _claim_key(message: Message) -> str:
    return _claims_claim_key(message)


async def try_claim_message(message: Message, emoji: str = "👀") -> bool:
    import claims as _claims

    _claims.CLAIM_DIR = CLAIM_DIR
    return await _try_claim_message(bot, BOT_USERNAME, message, emoji=emoji)


async def ask_openai_contents(chat_id: int, contents, role="user", *, tool_choice: str | None = None) -> str:
    """Send prepared message contents to the agent.

    ``tool_choice`` can be used to force a specific tool for this message.
    """
    try:
        message_list = [{"role": role, "content": contents}]
        reply = await ask_agent(message_list, chat_id=chat_id, tool_choice=tool_choice)
        return clean_openai_reply(reply)
    except Exception as e:
        # Never post the error into the chat; callers treat "" as "say nothing".
        logging.error(f"[ask_openai_contents] LLM error in chat {chat_id}: {e}")
        return ""


async def ask_openai(
    prompt: str,
    role="user",
    username="user",
    *,
    chat_id: int,
    tool_choice: str | None = None,
) -> str:
    """Send a message to the OpenAI assistant with proper structure (no string concatenation).

    Note: History is now managed automatically by agent_client, not passed as a parameter.
    """
    # Format the message with username prefix
    formatted_prompt = f"{username}: {prompt}"
    logging.debug(f"[ask_openai] {chat_id} <- {formatted_prompt}")
    return await ask_openai_contents(chat_id, formatted_prompt, role=role, tool_choice=tool_choice)


# The vision model only describes. The caption is someone's comment on the
# picture, not an instruction for the describer (it used to be passed as the
# prompt, so "нормальный прикол" produced "Да, отличный прикол! 😄").
VISION_DESCRIBE_PROMPT = (
    "Опиши по-русски нейтрально и конкретно, что на изображении: объекты, люди, животные, "
    "обстановка, и дословно весь видимый текст. Без оценок, шуток и форматирования, до 120 слов."
)


def _photo_message_text(caption: str | None, sender: str | None, description: str | None = None) -> str:
    who = sender or "кто-то"
    parts = [f"{who} {PHOTO_MARK}."]
    if description:
        parts.append(f"На картинке: {description}")
    if caption:
        parts.append(f"Подпись: {caption}")
    parts.append(IMAGE_DEFAULT_PROMPT)
    return "\n".join(parts)


async def ask_openai_image(
    image_bytes: bytes,
    caption: str | None = None,
    *,
    chat_id: int,
    sender: str | None = None,
) -> str:
    """Let the bot react to a picture (and its caption, if any)."""
    if USE_OPENROUTER:
        tmp_file = None
        try:
            with NamedTemporaryFile(delete=False, dir="/tmp", suffix=".jpg") as f:
                f.write(image_bytes)
                tmp_file = f.name
            # Call MCP tool directly (agent can't invoke MCP tools via OpenRouter)
            description = await analyze_image_via_mcp(tmp_file, VISION_DESCRIBE_PROMPT)
            return await ask_openai_contents(chat_id, _photo_message_text(caption, sender, description))
        except Exception as e:
            # Couldn't see the image -> stay silent rather than guess or post the error
            logging.error(f"[ask_openai_image] vision failed, not reacting: {e}")
            return ""
        finally:
            if tmp_file:
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass
    try:
        image_file = io.BytesIO(image_bytes)
        image_file.name = "image.jpg"
        file_response = await openai_client.files.create(
            file=image_file,
            purpose="vision",
        )
        file_id = file_response.id
        contents = [
            {"type": "input_text", "text": _photo_message_text(caption, sender)},
            {"type": "input_image", "file_id": file_id},
        ]
        return await ask_openai_contents(chat_id, contents)
    except Exception as e:
        logging.error(f"[ask_openai_image] vision failed, not reacting: {e}")
        return ""


async def download_image_to_tmp(url: str) -> str:
    """Download an image from ``url`` and save it to ``/tmp``.

    Returns the file path of the saved image.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            data = await resp.read()

    # Strip query string before extracting extension
    path_part = url.split("?")[0].split("#")[0]
    suffix = os.path.splitext(path_part)[1] or ".jpg"
    with NamedTemporaryFile(delete=False, dir="/tmp", suffix=suffix) as f:
        f.write(data)
        return f.name


async def _extract_json_image(reply: str):
    """Return image information if ``reply`` includes a JSON payload.

    Supports:
    - ``{"image": "<base64>"}``
    - ``{"image_url": "http://..."}`` or ``{"url": "http://..."}``
    - ``{"command": "/meme"}``
    Returns a tuple ``(data, caption)`` where ``data`` is either bytes or a file
    path to an image in ``/tmp``. If the reply contains text after the JSON
    block, that text will be used as the caption when no ``caption`` field is
    present.
    """
    import re

    trailing_text = ""

    try:
        data = json.loads(reply)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", reply, re.DOTALL)
        if not m:
            # Check for a bare image URL in the text
            url_match = re.search(r"https?://\S+", reply)
            if url_match:
                url = url_match.group(0).rstrip(").,'\"")
                if re.search(r"\.(?:jpe?g|png|gif)(?:\?|$)", url, re.I):
                    path = await download_image_to_tmp(url)
                    caption = reply[url_match.end() :].strip()
                    return path, caption
            # Check for a local temporary file path like /tmp/tmp123.jpg
            path_match = re.search(r"(/tmp/[^\s]+\.(?:jpe?g|png|gif))", reply)
            if path_match and os.path.exists(path_match.group(1)):
                caption = reply[path_match.end() :].strip()
                return path_match.group(1), caption
            return None
        trailing_text = reply[m.end() :].strip()
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    else:
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return None

    if isinstance(data, dict) and data.get("type") == "text" and isinstance(data.get("text"), str):
        try:
            inner = json.loads(data["text"])
            if isinstance(inner, dict):
                data = inner
        except json.JSONDecodeError:
            pass

    if not isinstance(data, dict):
        return None

    if "image" in data:
        val = data["image"]
        if isinstance(val, str) and val.startswith("http"):
            path = await download_image_to_tmp(val)
            caption = str(data.get("caption", "")) or trailing_text
            return path, caption
        try:
            img_bytes = base64.b64decode(val)
        except Exception:
            return None
        caption = str(data.get("caption", "")) or trailing_text
        return img_bytes, caption

    if "image_url" in data or "url" in data:
        url = data.get("image_url") or data.get("url")
        path = await download_image_to_tmp(url)
        caption = str(data.get("caption", "")) or trailing_text
        return path, caption

    if data.get("command") == "/meme":
        path = await retrieve_joke()
        return path, ""

    return None


async def _extract_voice_file(reply: str):
    """Return voice path and remaining text if ``reply`` mentions a local audio file.

    Supports JSON payloads like ``{"voice": "<base64>"}`` or plain paths such as
    ``/tmp/tmp123.ogg`` in the text. The function returns a tuple ``(path, text)``
    where ``text`` is the original message without the file reference.
    """
    import re

    trailing_text = ""
    try:
        data = json.loads(reply)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", reply, re.DOTALL)
        if not m:
            path_match = re.search(r"(/tmp/[^\s]+\.(?:mp3|ogg|wav|m4a))", reply)
            if path_match:
                path = path_match.group(1).rstrip(").,'\"")
                if os.path.exists(path):
                    text = (reply[: path_match.start()] + reply[path_match.end() :]).strip()
                    return path, text
            return None
        trailing_text = reply[m.end() :].strip()
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    else:
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return None

    if isinstance(data, dict) and data.get("type") == "text" and isinstance(data.get("text"), str):
        try:
            inner = json.loads(data["text"])
            if isinstance(inner, dict):
                data = inner
        except json.JSONDecodeError:
            pass

    if not isinstance(data, dict):
        return None

    if "voice" in data:
        val = data["voice"]
        if isinstance(val, str) and val.startswith("http"):
            path = await download_image_to_tmp(val)
            text = str(data.get("caption", "")) or trailing_text
            return path, text
        if isinstance(val, str) and os.path.exists(val):
            text = str(data.get("caption", "")) or trailing_text
            return val, text
        try:
            audio_bytes = base64.b64decode(val)
        except Exception:
            return None
        with NamedTemporaryFile(delete=False, dir="/tmp", suffix=".ogg") as f:
            f.write(audio_bytes)
            path = f.name
        text = str(data.get("caption", "")) or trailing_text
        return path, text

    if "voice_url" in data or "url" in data:
        url = data.get("voice_url") or data.get("url")
        path = await download_image_to_tmp(url)
        text = str(data.get("caption", "")) or trailing_text
        return path, text

    if "path" in data and os.path.exists(data["path"]):
        text = str(data.get("caption", "")) or trailing_text
        return data["path"], text

    return None


async def send_nudge_with_image(target, chat_id, answer, caption="", is_message=True):
    """Send ``answer`` as a nudge and optionally attach an image."""

    # Check for a JSON command requesting a fact before other processing.
    command_payload = None
    trailing_text = ""
    try:
        command_payload = json.loads(answer)
    except json.JSONDecodeError:
        import re

        m = re.search(r"\{.*\}", answer, re.DOTALL)
        if m:
            trailing_text = answer[m.end() :].strip()
            try:
                command_payload = json.loads(m.group(0))
            except json.JSONDecodeError:
                command_payload = None
    if isinstance(command_payload, str):
        try:
            command_payload = json.loads(command_payload)
        except json.JSONDecodeError:
            command_payload = None
    if isinstance(command_payload, dict) and command_payload.get("command") == "/fact":
        fact = await retrieve_fact()
        extra = str(command_payload.get("caption", "")).strip()
        if extra and trailing_text:
            extra = f"{extra}\n\n{trailing_text}"
        elif trailing_text:
            extra = trailing_text
        answer = f"{fact}\n\n{extra}".strip() if extra else fact

    # If the reply contains a voice file path, send the voice first
    voice = await _extract_voice_file(answer)
    if voice:
        path, text = voice
        voice_file = FSInputFile(path)
        try:
            if is_message:
                await target.answer_voice(voice_file)
            else:
                await target.send_voice(chat_id, voice_file)
        except Exception as e:
            logging.error(f"Failed to send nudge voice to chat {chat_id}: {e}")
        try:
            os.remove(path)
        except Exception:
            pass
        answer = text

    # If the reply contains a JSON payload with an image, send that directly
    json_img = await _extract_json_image(answer)
    if json_img:
        img_data, raw_caption = json_img
        if raw_caption:
            try:
                styled = await style_caption(raw_caption, chat_id=chat_id)
            except Exception:
                styled = raw_caption
        else:
            styled = ""
        if isinstance(img_data, bytes):
            photo = BufferedInputFile(img_data, filename="assistant.jpg")
        else:
            photo = FSInputFile(img_data)
        try:
            if is_message:
                await target.answer_photo(photo, caption=styled)
            else:
                await target.send_photo(chat_id, photo, caption=styled)
        except Exception as e:
            logging.error(f"Failed to send nudge image to chat {chat_id}: {e}")
        if isinstance(img_data, str):
            try:
                os.remove(img_data)
            except Exception:
                pass
        return

    # Picture first, then post it WITH the text as caption: sent separately it
    # arrived a minute late, after the other bots had already answered the text.
    image_bytes = None
    if random.random() < IMAGE_SEND_CHANCE and len(answer) <= 1000:
        image_bytes = await generate_image_from_observation(answer, chat_id=chat_id)
    if image_bytes:
        image_file = BufferedInputFile(image_bytes, filename="observation.png")
        try:
            if is_message:
                await target.answer_photo(image_file, caption=answer, parse_mode=ParseMode.HTML)
            else:
                await target.send_photo(chat_id, image_file, caption=answer, parse_mode=ParseMode.HTML)
            return
        except Exception as e:
            logging.error(f"Failed to send nudge image to chat {chat_id}: {e}; sending text only")

    try:
        if is_message:
            await target.answer(answer, parse_mode=ParseMode.HTML)
        else:
            await target.send_message(chat_id, answer, parse_mode=ParseMode.HTML)
    except Exception as e:
        logging.error(f"Failed to send nudge to chat {chat_id}: {e}")


async def _caption_for_picture(description: str, kind: str) -> str:
    """A short comment in the bot's own voice for a picture it is about to post."""
    raw = await agent_client.ask_stateless(
        f"Ты сам выкладываешь в чат {kind}. Вот что на картинке: {description}\n"
        "Напиши короткую подпись от себя, 3–12 слов, в своём стиле. Не пересказывай картинку. "
        "Только подпись, без кавычек.",
        persona=True,
    )
    caption = clean_openai_reply(raw or "").strip().strip('"«»')
    if CYRILLIC_ONLY and latin_problem(caption):
        return ""
    return caption


async def send_media_nudge(target, chat_id: int, prompt: str, *, is_message: bool) -> str | None:
    """Post a meme / picture of the day for a nudge that asks for one.

    The free models often called the tool but dropped the image link, so the
    chat got "Прикол. Или просто картинка." with no picture. Here the code
    fetches the image itself and asks the model only for a caption.
    Returns the posted caption (or a marker) if it posted, "" if this was a
    picture nudge that failed (the caller must then switch to a text-only
    prompt; otherwise the model announced «Картинка дня…» with no picture),
    and None for an ordinary text nudge.
    """
    if "get_news" in prompt:
        return await send_news_nudge(target, chat_id, prompt, is_message=is_message)
    if "retrieve_joke" in prompt:
        kind, getter = "мем", retrieve_joke
    elif "get_picture_of_the_day" in prompt:
        kind, getter = "картинку дня", get_picture_of_the_day
    else:
        return None
    path = None
    try:
        got = await getter()
        path, source_caption = (got if isinstance(got, tuple) else (got, ""))
        try:
            description = await analyze_image_via_mcp(path, VISION_DESCRIBE_PROMPT)
            caption = await _caption_for_picture(description, kind)
        except Exception as e:
            logging.warning(f"[nudge-media] no caption for {kind}: {e}")
            caption = ""  # can't see it: post the picture without our comment
        if not caption and source_caption:
            caption = await style_caption(source_caption, chat_id=chat_id)
        photo = FSInputFile(path)
        if is_message:
            await target.answer_photo(photo, caption=caption or None)
        else:
            await target.send_photo(chat_id, photo, caption=caption or None)
        return caption or f"[{kind}]"
    except Exception as e:
        logging.error(f"[nudge-media] could not post {kind} in {chat_id}: {e!r}")
        return ""
    finally:
        if path and isinstance(path, str):
            try:
                os.remove(path)
            except OSError:
                pass


async def fetch_news(region: str = "any") -> list[dict]:
    async with sse_client(MCP_SERVER_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resp = await session.call_tool("get_news", {"region": region, "limit": 8})
            if not resp.content or getattr(resp, "isError", False):
                raise ValueError("no news returned")
            return json.loads(resp.content[0].text)


NEWS_PICK_PROMPT = (
    "Ты делишься с друзьями в чате одной свежей новостью. Вот заголовки:\n{items}\n\n"
    "Выбери ОДНУ, о которой интересно поболтать: курьёз, Латвия, спорт, технологии, наука, культура, "
    "деньги и цены, политика без трагедий. Не бери новости о смертях, войне, обстрелах, катастрофах, "
    "терактах и болезнях. Напиши к ней свой короткий комментарий (5–20 слов) в своём стиле — "
    "реакцию, а не пересказ заголовка.{mention}\n"
    'Ответь строго JSON одной строкой: {{"i": номер_новости, "comment": "твой комментарий"}}'
)


async def send_news_nudge(target, chat_id: int, prompt: str, *, is_message: bool) -> str:
    """Post a real news link with the bot's comment; "" if it couldn't (caller falls back to text).

    The code fetches the feeds and posts the link itself, so the model can't
    invent news or drop the URL; it only picks an item and writes the comment.
    """
    import re
    from nudge import load_recent_news, remember_news

    region = "lv" if "Латви" in prompt else ("world" if "мир" in prompt.lower() else "any")
    try:
        seen = {n["link"] for n in load_recent_news(chat_id)}
        items = [n for n in await fetch_news(region) if n["link"] not in seen]
        if not items:
            return ""
        random.shuffle(items)
        items = items[:12]
        listing = "\n".join(f"{i}. [{n['source']}] {n['title']}" for i, n in enumerate(items))
        others = [f"@{b}" for b in KNOWN_BOTS if b != BOT_USERNAME]
        mention = (f" Иногда (не всегда) можно обратиться к одному из друзей по нику: {', '.join(others)}."
                   if others else "")
        raw = await agent_client.ask_stateless(NEWS_PICK_PROMPT.format(items=listing, mention=mention), persona=True)
        m = re.search(r"\{.*\}", raw or "", re.S)
        data = json.loads(m.group(0)) if m else {}
        idx = int(data.get("i", -1))
        comment = clean_openai_reply(str(data.get("comment", ""))).strip().strip('"«»')
        if not (0 <= idx < len(items)) or not comment:
            logging.warning(f"[news] unusable pick: {raw!r}")
            return ""
        item = items[idx]
        text = f"{comment}\n{item['link']}"
        if is_message:
            await target.answer(text)
        else:
            await target.send_message(chat_id, text)
        remember_news(chat_id, item)
        logging.info(f"[news] {item['source']}: {item['title']}")
        return f"{comment} ({item['title']})"
    except Exception as e:
        logging.error(f"[news] could not post news in {chat_id}: {e!r}")
        return ""


_bot_ctx: BotContext | None = None


def _get_bot_ctx() -> BotContext:
    global _bot_ctx
    if _bot_ctx is None:
        _bot_ctx = BotContext(
            bot_username=BOT_USERNAME,
            name_mention_re=NAME_MENTION_RE,
            image_default_prompt=IMAGE_DEFAULT_PROMPT,
            chat_react_prompt=CHAT_REACT_PROMPT,
            max_unmentioned_replies=MAX_UNMENTIONED_REPLIES,
            recent_activity_seconds=RECENT_ACTIVITY_SECONDS,
            last_activity_time=last_activity_time,
            messages_since_bot_reply=messages_since_bot_reply,
            bot_unmentioned_count=bot_unmentioned_count,
            last_bot_reply_time=last_bot_reply_time,
            try_claim_message=try_claim_message,
            nudge_inactive_chats=nudge_inactive_chats,
            get_picture_of_the_day=get_picture_of_the_day,
            style_caption=style_caption,
            retrieve_joke=retrieve_joke,
            retrieve_fact=retrieve_fact,
            generate_voice_file=generate_voice_file,
            ask_openai=ask_openai,
            ask_agent=ask_agent,
            ask_openai_image=ask_openai_image,
            clean_openai_reply=clean_openai_reply,
            mark_bot_replied=mark_bot_replied,
            extract_voice_file=_extract_voice_file,
            extract_json_image=_extract_json_image,
            needs_voice_tool=_needs_voice_tool,
            bot=bot,
        )
    return _bot_ctx


@dp.message(F.text)
async def handle_message(message: Message):
    await handle_text_message(message, _get_bot_ctx())


@dp.message(F.photo)
async def handle_photo(message: Message):
    await handle_photo_message(message, _get_bot_ctx())


async def get_picture_of_the_day(date: str = "") -> tuple[str | bytes, str]:
    """Fetch picture of the day via MCP tool and return image path and caption.

    The MCP server now returns a direct URL, which is downloaded and saved to
    ``/tmp``. The file path is returned along with the caption.
    """
    async with sse_client(MCP_SERVER_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resp = await session.call_tool(
                "get_picture_of_the_day", {"date": date}
            )
            data = json.loads(resp.content[0].text)
            url = data.get("url") or data.get("image_url")
            caption = data.get("caption", "")
            if not url:
                raise ValueError("no url returned")
            path = await download_image_to_tmp(url)
            return path, caption


async def retrieve_joke() -> str:
    """Fetch a random meme image via the MCP tool.

    The MCP server returns the original image URL. This function downloads the
    image to ``/tmp`` and returns the local file path.
    """

    async with sse_client(MCP_SERVER_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resp = await session.call_tool("retrieve_joke", {})
            if not resp.content:
                raise ValueError("no data returned")
            url = resp.content[0].text

    path = await download_image_to_tmp(url)
    return path


async def retrieve_fact() -> str:
    """Fetch a random fact via the MCP tool and return the text."""

    async with sse_client(MCP_SERVER_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resp = await session.call_tool("retrieve_fact", {})
            if not resp.content:
                raise ValueError("no data returned")
            return resp.content[0].text.strip()


async def analyze_image_via_mcp(image_path: str, prompt: str) -> str:
    """Analyze an image via the MCP tool and return the text description."""

    async with sse_client(MCP_SERVER_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resp = await session.call_tool(
                "analyze_image", {"image_path": image_path, "prompt": prompt}
            )
            if not resp.content:
                raise ValueError("no data returned from analyze_image")
            result = resp.content[0].text.strip()
            logging.info(f"[vision] {result[:300]!r}")
            return result


async def generate_voice_file(text: str) -> str:
    """Generate a voice message via MCP and return the local file path."""

    logging.debug("generate_voice_file: requesting voice for text %r", text)
    async with sse_client(MCP_SERVER_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resp = await session.call_tool("generate_voice", {"text": text})
            if not resp.content:
                raise ValueError("no data returned")
            path = resp.content[0].text
    logging.debug("generate_voice_file: received path %s", path)
    return path


async def style_caption(caption: str, *, chat_id: int) -> str:
    """Rewrite ``caption`` in the bot's style. Stateless: the request itself never
    lands in the chat history (it used to show up there as a "user" message)."""
    _ = chat_id
    try:
        styled = clean_openai_reply(await agent_client.ask_stateless(
            "Перепиши подпись к картинке своими словами в своём стиле, сохранив смысл. "
            "Верни только новую подпись, одной строкой.",
            caption,
            persona=True,
        ))
    except Exception as e:
        logging.warning(f"[style_caption] {e}")
        styled = ""
    return styled or caption


POLLINATIONS_URL = "https://image.pollinations.ai/prompt/{prompt}"  # classic endpoint: no key needed (gen.* requires one)
POLLINATIONS_ATTEMPTS = 3
POLLINATIONS_RETRY_WAIT = 12  # anonymous tier answers 429/503 when busy


async def make_image_prompt(observation: str) -> str:
    """Turn a chat message into a short English text-to-image prompt.

    Stateless and persona-free, so it never lands in any chat history.
    """
    instruction = (
        f"{IMAGE_GEN_INPUT_PROMPT.format(observation=observation)}\n\n"
        "Верни ТОЛЬКО описание картинки на английском языке одной строкой, до 40 слов: "
        "что изображено, обстановка, стиль. Без текста и надписей на картинке. "
        "Ничего не генерируй сам и ничего не добавляй от себя."
    )
    raw = await agent_client.ask_stateless(instruction)
    lines = [l.strip().strip('"\'«»').strip() for l in (raw or "").splitlines()]
    lines = [l for l in lines if l]
    prompt = lines[0][:400] if lines else ""
    if not _looks_like_english_prompt(prompt):
        logging.warning(f"[image-gen] rejected non-prompt output: {prompt!r}")
        return ""
    return prompt


def _looks_like_english_prompt(text: str) -> bool:
    """A real image prompt is English; a refusal or in-character chat reply isn't."""
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 15:
        return False
    latin = sum(1 for c in letters if c.isascii())
    return latin / len(letters) >= 0.9 and "@" not in text


async def fetch_pollinations_image(prompt: str) -> bytes | None:
    """Generate an image with Pollinations' free anonymous API (no key, no cost)."""
    from urllib.parse import quote

    url = POLLINATIONS_URL.format(prompt=quote(prompt, safe=""))
    params = {"width": "1024", "height": "1024", "nologo": "true", "seed": str(random.randint(1, 10**9))}
    timeout = aiohttp.ClientTimeout(total=120)
    for attempt in range(1, POLLINATIONS_ATTEMPTS + 1):
        try:
            async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
                async with session.get(url, params=params) as resp:
                    ctype = resp.headers.get("content-type", "")
                    if resp.status == 200 and ctype.startswith("image/"):
                        return await resp.read()
                    logging.warning(f"[image-gen] Pollinations attempt {attempt}: HTTP {resp.status} {ctype}")
        except Exception as e:
            logging.warning(f"[image-gen] Pollinations attempt {attempt} failed: {e}")
        if attempt < POLLINATIONS_ATTEMPTS:
            await asyncio.sleep(POLLINATIONS_RETRY_WAIT)
    return None


async def generate_image_from_observation(observation: str, chat_id: int | None = None) -> bytes | None:
    """Generate a picture for ``observation`` with a free model; None on any failure.

    On success the picture's description is put on the bot bus so the other
    bots know what was posted (they can't see images sent by bots).
    """
    try:
        prompt = await make_image_prompt(observation)
        if not prompt:
            return None
        logging.info(f"[image-gen] prompt: {prompt!r}")
        image = await fetch_pollinations_image(prompt)
        return image
    except Exception as e:
        logging.error(f"Image generation error: {e}")
        return None


async def nudge_inactive_chats(
    force: bool = False, force_chat_id: int = None, force_message=None
):
    import state as _state

    nudge_started_ref = [_state.nudge_loop_started_at]

    def _get_nudge_prompt(_chat_id: int) -> str:
        return build_nudge_prompt(
            bot_timezone=BOT_TIMEZONE,
            first_nudge_enabled=FIRST_NUDGE_ENABLED,
            first_nudge_start=FIRST_NUDGE_START,
            first_nudge_end=FIRST_NUDGE_END,
            first_nudge_prompt=FIRST_NUDGE_PROMPT,
            nudge_system_prompts=NUDGE_SYSTEM_PROMPTS,
            nudge_prompt_history=nudge_prompt_history,
            nudge_prompt_history_len=NUDGE_PROMPT_HISTORY_LEN,
        )

    try:
        await run_nudge_loop(
            force=force,
            force_chat_id=force_chat_id,
            force_message=force_message,
            ask_agent=ask_agent,
            clean_openai_reply=clean_openai_reply,
            mark_bot_replied=mark_bot_replied,
            send_nudge_with_image=send_nudge_with_image,
            bot=bot,
            bot_username=BOT_USERNAME,
            bot_timezone=BOT_TIMEZONE,
            active_start=ACTIVE_START,
            active_end=ACTIVE_END,
            get_nudge_minutes=get_nudge_minutes,
            nudge_enabled_chats=NUDGE_ENABLED_CHATS,
            nudge_reset_interval=NUDGE_RESET_INTERVAL,
            nudge_check_interval=NUDGE_CHECK_INTERVAL,
            last_activity_time=last_activity_time,
            nudge_loop_started_at_ref=nudge_started_ref,
            bot_unmentioned_count=bot_unmentioned_count,
            get_nudge_prompt_for_chat=_get_nudge_prompt,
            send_media_nudge=send_media_nudge,
        )
    finally:
        _state.nudge_loop_started_at = nudge_started_ref[0]


def mark_bot_replied(chat_id):
    """Record that the bot sent a message in ``chat_id``."""
    now = datetime.now()
    last_activity_time[chat_id] = now
    last_bot_reply_time[chat_id] = now
    messages_since_bot_reply[chat_id] = 0
    bot_messages.count_bot_message(chat_id)  # our own messages aren't delivered back to us
    save_shared_last_activity(last_activity_time)
    save_shared_bot_unmentioned(bot_unmentioned_count)


def clean_openai_reply(text: str) -> str:
    """Remove tagged sections like {24:0†foo.json}, 【4:5†foo.json】 or
    【0:tagged_jura_messages.json】 from OpenAI replies."""
    import re

    pattern = r"[\{【]\d+:[^】}]+[】\}]"
    text = re.sub(pattern, "", text or "").strip()
    return thin_emoji(tidy_reply(fix_mentions(text, KNOWN_BOTS)))


COMMON_RULES_FILE = os.path.join("prompts", "_common", "system_rules.yaml")
KNOWN_BOTS = sorted(
    d for d in (os.listdir("prompts") if os.path.isdir("prompts") else [])
    if d.endswith("_bot") and d != "default_bot"
)


def load_system_prompt() -> str:
    """Return the bot's system prompt plus the rules shared by all personas."""
    if not os.path.exists(SYSTEM_PROMPT_FILE):
        return ""
    with open(SYSTEM_PROMPT_FILE, "r", encoding="utf-8") as f:
        prompt = f.read()
    if os.path.exists(COMMON_RULES_FILE):
        with open(COMMON_RULES_FILE, "r", encoding="utf-8") as f:
            prompt = f"{prompt.rstrip()}\n\n{f.read()}"
    if BOT_USERNAME:
        prompt += (
            f"\n\nТвой ник в Telegram: @{BOT_USERNAME}. Сообщения с этим ником обращены к тебе. "
            "Себя по нику не упоминай, ники других пиши точно латиницей, как они есть."
        )
    return prompt


# History loading and scheduled summarization removed - now handled by agent_client


async def periodic_history_save():
    """Periodically save chat histories to disk."""
    try:
        while True:
            await asyncio.sleep(300)  # Save every 5 minutes
            agent_client.save_histories_to_disk()
            cleanup_old_claims()
    except asyncio.CancelledError:
        logging.info("[history_save] Cancelled, saving before exit.")
        agent_client.save_histories_to_disk()
        raise


async def startup() -> None:
    """Initialize system prompt thread and start polling."""
    # Validate environment before starting
    validate_environment()

    # Initialize the agent with system prompt (also patches any loaded histories)
    system_prompt = load_system_prompt()
    agent_client.load_histories_from_disk()
    if system_prompt:
        await create_thread_with_system_prompt(system_prompt, BOT_USERNAME)

    # Start background tasks
    asyncio.create_task(nudge_inactive_chats())
    asyncio.create_task(periodic_history_save())

    try:
        await dp.start_polling(bot)
    finally:
        agent_client.save_histories_to_disk()
        await agent_client.shutdown()


def main() -> None:
    asyncio.run(startup())


if __name__ == "__main__":
    main()
