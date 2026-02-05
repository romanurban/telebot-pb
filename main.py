import os
import re
import logging
from aiogram import Bot, Dispatcher
from aiogram.types import Message, ReactionTypeEmoji
from aiogram.enums import ParseMode
from aiogram import F
import asyncio
import dotenv

dotenv.load_dotenv()

from agent_client import (
    openai_client,
    create_thread_with_system_prompt,
    ask_agent,
)
import agent_client
import base64
from tempfile import NamedTemporaryFile
import random
import aiohttp
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
import io
from aiogram.types.input_file import BufferedInputFile, FSInputFile
import yaml
import json
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession
from openai import AsyncOpenAI

# Standard OpenAI client for image generation (not the Agents SDK wrapper)
_openai_images_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))

# === CONSTANTS & ENVIRONMENT VARIABLES ===

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
# Load the system prompt from a YAML file named after the bot in the prompts folder
SYSTEM_PROMPT_FILE = os.path.join("prompts", BOT_USERNAME, "system_prompt.yaml")
BOT_PROMPTS_FILE = os.path.join("prompts", BOT_USERNAME, "bot_prompts.yaml")
DEFAULT_BOT_PROMPTS_FILE = os.path.join("prompts", "default_bot", "bot_prompts.yaml")

NUDGE_MINUTES = int(
    os.getenv("NUDGE_MINUTES", 120)
)  # Minutes of inactivity before nudge (default 2 hours)
IMAGE_GEN_MODEL = os.getenv("IMAGE_GEN_MODEL", "gpt-image-1.5")
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8888/sse")

# Load default prompts from the example bot and override them with bot-specific
# values if present.
_prompt_data = {}
if os.path.exists(DEFAULT_BOT_PROMPTS_FILE):
    with open(DEFAULT_BOT_PROMPTS_FILE, "r", encoding="utf-8") as f:
        _prompt_data.update(yaml.safe_load(f) or {})

if os.path.exists(BOT_PROMPTS_FILE):
    with open(BOT_PROMPTS_FILE, "r", encoding="utf-8") as f:
        _prompt_data.update(yaml.safe_load(f) or {})

NUDGE_SYSTEM_PROMPTS = _prompt_data.get("nudge_system_prompts", [])
IMAGE_DEFAULT_PROMPT = _prompt_data.get("image_default_prompt", "")
CHAT_REACT_PROMPT = _prompt_data.get("chat_react_prompt", "")
IMAGE_GEN_INPUT_PROMPT = _prompt_data.get("image_gen_input_prompt", "")
_name_patterns = _prompt_data.get("name_mention_patterns", [])
NAME_MENTION_RE = re.compile("|".join(_name_patterns), re.IGNORECASE) if _name_patterns else None

def _needs_voice_tool(text: str) -> bool:
    """Return True if ``text`` requests a voice message."""
    tl = text.lower()
    words = tl.split()
    return any(word.startswith("голос") for word in words)

FIRST_NUDGE_PROMPT = NUDGE_SYSTEM_PROMPTS[0] if NUDGE_SYSTEM_PROMPTS else ""
FIRST_NUDGE_START = time(10, 0)
FIRST_NUDGE_END = time(12, 0)
FIRST_NUDGE_ENABLED = os.getenv("FIRST_NUDGE_ENABLED", "false").lower() in ("true", "1", "yes")

# Comma-separated list of chat IDs where nudge is enabled
# Example: NUDGE_ENABLED_CHATS="-123456789,-987654321"
_nudge_chats_str = os.getenv("NUDGE_ENABLED_CHATS", "")
NUDGE_ENABLED_CHATS = set()
if _nudge_chats_str:
    for chat_id_str in _nudge_chats_str.split(","):
        try:
            NUDGE_ENABLED_CHATS.add(int(chat_id_str.strip()))
        except ValueError:
            logging.warning(f"Invalid chat ID in NUDGE_ENABLED_CHATS: {chat_id_str}")

# Short history for nudge prompts to avoid recent repeats
NUDGE_PROMPT_HISTORY_LEN = 3  # Number of previous prompts to avoid
nudge_prompt_history = []

BOT_TIMEZONE = ZoneInfo(os.getenv("BOT_TIMEZONE", "Europe/Riga"))

# Parse active hours from env (format: "HH:MM")
def _parse_time(time_str: str, default: time) -> time:
    try:
        h, m = time_str.split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return default

ACTIVE_START = _parse_time(os.getenv("ACTIVE_START", "10:00"), time(10, 0))
ACTIVE_END = _parse_time(os.getenv("ACTIVE_END", "21:00"), time(21, 0))

MAX_UNMENTIONED_REPLIES = 3

# Additional configuration constants
IMAGE_SEND_CHANCE = float(os.getenv("IMAGE_SEND_CHANCE", 0.3))  # Probability of sending an image with a nudge
NUDGE_RESET_INTERVAL = 300  # Seconds between unmentioned counter resets
NUDGE_CHECK_INTERVAL = 60  # Interval between inactivity checks
RECENT_ACTIVITY_SECONDS = 30  # Window to treat bot replies as "recent"

# === GLOBAL STATE ===
# Note: chat histories now managed in agent_client._histories
last_activity_time = {}  # chat_id: datetime — any message, used by nudge timer
last_bot_reply_time = {}  # chat_id: datetime — bot replies only, used by probabilistic logic
bot_unmentioned_count = {}  # chat_id: int
messages_since_bot_reply = {}  # chat_id: int — user messages since last bot reply
CLAIM_DIR = "/tmp/telebot_claims"
os.makedirs(CLAIM_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO)

# Validate required environment variables
def validate_environment():
    """Validate that all required environment variables are set."""
    missing = []

    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN.startswith("<"):
        missing.append("TELEGRAM_TOKEN")

    if not OPENAI_API_KEY or OPENAI_API_KEY.startswith("<"):
        missing.append("OPENAI_API_KEY")

    if not BOT_USERNAME:
        missing.append("BOT_USERNAME")

    if missing:
        logging.error("Missing required environment variables:")
        for var in missing:
            logging.error(f"  - {var}")
        logging.error("\nPlease create a .env file with these variables.")
        logging.error("See .env.example for reference.")
        raise SystemExit(1)

    # Validate that system prompt file exists
    if not os.path.exists(SYSTEM_PROMPT_FILE):
        logging.error(f"System prompt file not found: {SYSTEM_PROMPT_FILE}")
        logging.error(f"Please create prompts/{BOT_USERNAME}/system_prompt.yaml")
        logging.error("You can copy from prompts/default_bot/ as a starting point.")
        raise SystemExit(1)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()


# Helper: check if current time in Europe/Riga is between ACTIVE_START and ACTIVE_END
def is_active_hours():
    now = datetime.now(BOT_TIMEZONE).time()
    return ACTIVE_START <= now <= ACTIVE_END


async def try_claim_message(message: Message, emoji: str = "👀") -> bool:
    """Try to claim a message via an atomic file lock.

    Uses O_CREAT | O_EXCL to guarantee only one bot wins the claim.
    The reaction emoji is added as a visual indicator only.
    """
    chat_id = message.chat.id
    msg_id = message.message_id
    claim_path = os.path.join(CLAIM_DIR, f"{chat_id}_{msg_id}")

    # Random delay to desynchronize bots
    await asyncio.sleep(random.uniform(0.5, 3.0))

    # Atomic claim: O_CREAT | O_EXCL fails if file already exists
    try:
        fd = os.open(claim_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, BOT_USERNAME.encode())
        os.close(fd)
    except FileExistsError:
        return False

    # Visual indicator only — not used for coordination
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=msg_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except Exception:
        pass

    return True


async def ask_openai_contents(chat_id: int, contents, role="user", *, tool_choice: str | None = None) -> str:
    """Send prepared message contents to the agent.

    ``tool_choice`` can be used to force a specific tool for this message.
    """
    try:
        message_list = [{"role": role, "content": contents}]
        reply = await ask_agent(message_list, chat_id=chat_id, tool_choice=tool_choice)
        return clean_openai_reply(reply)
    except Exception as e:
        return f"OpenAI error: {e}"


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
    print(f"[ask_openai] Sending to OpenAI: {formatted_prompt}")  # Debug print
    return await ask_openai_contents(chat_id, formatted_prompt, role=role, tool_choice=tool_choice)


async def ask_openai_image(
    image_bytes: bytes,
    prompt: str = IMAGE_DEFAULT_PROMPT,
    *,
    chat_id: int,
) -> str:
    """Upload an image and include it with the prompt for the assistant."""
    try:
        image_file = io.BytesIO(image_bytes)
        image_file.name = "image.jpg"
        file_response = await openai_client.files.create(
            file=image_file,
            purpose="vision",
        )
        file_id = file_response.id
        contents = [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "file_id": file_id},
        ]
        return await ask_openai_contents(chat_id, contents)
    except Exception as e:
        return f"OpenAI error: {e}"


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

    try:
        if is_message:
            await target.answer(answer, parse_mode=ParseMode.HTML)
        else:
            await target.send_message(chat_id, answer, parse_mode=ParseMode.HTML)
    except Exception as e:
        logging.error(f"Failed to send nudge to chat {chat_id}: {e}")

    if random.random() < IMAGE_SEND_CHANCE:
        image_bytes = await generate_image_from_observation(answer)
        if image_bytes:
            try:
                buffer = io.BytesIO(image_bytes)
                buffer.seek(0)
                image_file = BufferedInputFile(buffer.getvalue(), filename="observation.png")
                if is_message:
                    await target.answer_photo(image_file, caption=caption)
                else:
                    await target.send_photo(chat_id, image_file, caption=caption)
            except Exception as e:
                logging.error(f"Failed to send nudge image to chat {chat_id}: {e}")


@dp.message(F.text)
async def handle_message(message: Message):
    print(
        f"[Chat ID: {message.chat.id}] Received message from {message.from_user.username or message.from_user.id}: {message.text}"
    )
    if not message.text:
        return

    chat_id = message.chat.id
    username = message.from_user.username or str(message.from_user.id)

    # Any user message resets the nudge inactivity timer
    last_activity_time[chat_id] = datetime.now()
    messages_since_bot_reply[chat_id] = messages_since_bot_reply.get(chat_id, 0) + 1

    # Manual nudge trigger by command (handles /nudge, /nudge@bot and arguments)
    command = message.text.strip().split()[0].split("@")[0].lower()
    if command == "/nudge":
        # /nudge without @bot is addressed to all bots — claim it first
        raw_command = message.text.strip().split()[0].lower()
        if "@" not in raw_command:
            if not await try_claim_message(message):
                return
        await nudge_inactive_chats(
            force=True, force_chat_id=chat_id, force_message=message
        )
        return
    if command == "/potd":
        if not await try_claim_message(message):
            return
        date_arg = message.text.strip().split(maxsplit=1)
        date = date_arg[1] if len(date_arg) > 1 else ""
        try:
            img_data, caption = await get_picture_of_the_day(date)
            styled = await style_caption(caption, chat_id=chat_id)
            if isinstance(img_data, bytes):
                photo = BufferedInputFile(img_data, filename="potd.jpg")
            else:
                photo = FSInputFile(img_data)
            await message.answer_photo(photo, caption=styled)
            if isinstance(img_data, str):
                try:
                    os.remove(img_data)
                except Exception:
                    pass
            mark_bot_replied(chat_id)
        except Exception as e:
            await message.answer(f"Error: {e}")
        return
    if command == "/meme":
        if not await try_claim_message(message):
            return
        try:
            path = await retrieve_joke()
            photo = FSInputFile(path)
            await message.answer_photo(photo)
            mark_bot_replied(chat_id)
            try:
                os.remove(path)
            except Exception:
                pass
        except Exception as e:
            await message.answer(f"Error: {e}")
        return
    if command == "/fact":
        if not await try_claim_message(message):
            return
        try:
            fact = await retrieve_fact()
            await message.answer(fact)
            mark_bot_replied(chat_id)
        except Exception as e:
            await message.answer(f"Error: {e}")
        return
    if command == "/voice":
        if not await try_claim_message(message):
            return
        parts = message.text.strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Usage: /voice <text>")
            return
        text = parts[1]
        try:
            path = await generate_voice_file(text)
            voice = FSInputFile(path)
            logging.debug(
                "answer_voice: sending file %s (%d bytes)",
                path,
                os.path.getsize(path),
            )
            await message.answer_voice(voice)
            mark_bot_replied(chat_id)
            try:
                os.remove(path)
            except Exception:
                pass
        except Exception as e:
            await message.answer(f"Error: {e}")
        return

    # Handle direct mentions - these always get a response
    mention_tag = f"@{BOT_USERNAME}".lower()
    mentioned = mention_tag in message.text.lower() or (NAME_MENTION_RE is not None and bool(NAME_MENTION_RE.search(message.text)))
    tool_choice = "generate_voice" if (mentioned and _needs_voice_tool(message.text)) else None

    if mentioned:
        prompt = re.sub(re.escape(mention_tag), "", message.text, count=1, flags=re.IGNORECASE).strip()
        # History is now automatically managed by agent_client
        answer = await ask_openai(
            prompt,
            username=username,
            chat_id=chat_id,
            tool_choice=tool_choice,
        )
        voice = await _extract_voice_file(answer)
        if voice is None and tool_choice == "generate_voice":
            try:
                path = await generate_voice_file(answer)
                voice = (path, "")
            except Exception:
                voice = None

        if voice:
            path, text = voice
            voice_file = FSInputFile(path)
            await message.answer_voice(voice_file)
            mark_bot_replied(chat_id)
            try:
                os.remove(path)
            except Exception:
                pass
            answer = text
        json_img = await _extract_json_image(answer)
        if json_img:
            img_data, raw_caption = json_img
            styled = raw_caption
            if raw_caption:
                try:
                    styled = await style_caption(raw_caption, chat_id=chat_id)
                except Exception:
                    pass
            if isinstance(img_data, bytes):
                photo = BufferedInputFile(img_data, filename="assistant.jpg")
            else:
                photo = FSInputFile(img_data)
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
        # Reset unmentioned counter since bot was mentioned
        bot_unmentioned_count[chat_id] = 0
        return

    # --- Probabilistic reply logic for non-mentions ---

    # Track user messages since last bot reply
    non_bot_count = messages_since_bot_reply.get(chat_id, 0)

    # Check constraints
    bot_unmentioned = bot_unmentioned_count.get(chat_id, 0)

    # Don't respond if we've hit the unmentioned reply limit
    if bot_unmentioned >= MAX_UNMENTIONED_REPLIES:
        return

    # Determine if bot should respond based on various factors
    should_respond = False
    now = datetime.now()
    last_bot_time = last_bot_reply_time.get(chat_id)

    # Recent activity increases response chance
    if (
        last_bot_time
        and (now - last_bot_time).total_seconds() <= RECENT_ACTIVITY_SECONDS
    ):
        should_respond = True
    else:
        # Probabilistic response based on message count since last bot message
        if non_bot_count == 0:
            should_respond = random.random() < 0.1
        elif non_bot_count == 1:
            should_respond = random.random() < 0.25
        elif non_bot_count == 2:
            should_respond = random.random() < 0.5
        elif non_bot_count == 3:
            should_respond = random.random() < 0.75
        else:  # 4+ messages without bot response
            should_respond = True

    if not should_respond:
        return

    # Increment unmentioned counter since we're replying without being mentioned
    bot_unmentioned_count[chat_id] = bot_unmentioned + 1

    # Record the actual user message in agent history, then instruct the
    # agent to react via CHAT_REACT_PROMPT as a system-level hint.
    formatted_msg = f"{username}: {message.text}"
    message_list = [
        {"role": "user", "content": formatted_msg},
        {"role": "system", "content": CHAT_REACT_PROMPT},
    ]
    try:
        raw_answer = await ask_agent(message_list, chat_id=chat_id, tool_choice=tool_choice)
        answer = clean_openai_reply(raw_answer)
    except Exception as e:
        answer = f"OpenAI error: {e}"
    voice = await _extract_voice_file(answer)
    if voice:
        path, text = voice
        voice_file = FSInputFile(path)
        await message.answer_voice(voice_file)
        mark_bot_replied(chat_id)
        try:
            os.remove(path)
        except Exception:
            pass
        answer = text
    json_img = await _extract_json_image(answer)
    if json_img:
        img_data, raw_caption = json_img
        styled = raw_caption
        if raw_caption:
            try:
                styled = await style_caption(raw_caption, chat_id=chat_id)
            except Exception:
                pass
        if isinstance(img_data, bytes):
            photo = BufferedInputFile(img_data, filename="assistant.jpg")
        else:
            photo = FSInputFile(img_data)
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


@dp.message(F.photo)
async def handle_photo(message: Message):
    if not await try_claim_message(message):
        return
    print(f"Received photo from {message.from_user.username or message.from_user.id}")
    photo = message.photo[-1]  # Get the highest resolution photo
    photo_bytes = await bot.download(photo)
    image_bytes = photo_bytes.read()
    prompt = message.caption if message.caption else IMAGE_DEFAULT_PROMPT
    chat_id = message.chat.id
    last_activity_time[chat_id] = datetime.now()
    messages_since_bot_reply[chat_id] = messages_since_bot_reply.get(chat_id, 0) + 1
    answer = await ask_openai_image(image_bytes, prompt, chat_id=chat_id)
    mark_bot_replied(chat_id)
    await message.reply(answer)


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
    """Rewrite ``caption`` using the bot's system prompt for style."""
    prompt = (
        "Rewrite the following picture caption in your own style, keeping the "
        f"same meaning:\n{caption}"
    )
    return await ask_openai(prompt, chat_id=chat_id)


async def generate_image_from_observation(observation: str) -> bytes:
    """Enhance observation and generate an image using OpenAI Images API."""
    try:
        response = await _openai_images_client.images.generate(
            model=IMAGE_GEN_MODEL,
            prompt=IMAGE_GEN_INPUT_PROMPT.format(observation=observation),
            n=1,
            size="1024x1024",
            response_format="b64_json",
        )
        if response.data and response.data[0].b64_json:
            return base64.b64decode(response.data[0].b64_json)
        return None
    except Exception as e:
        logging.error(f"Image generation error: {e}")
        return None


async def nudge_inactive_chats(
    force: bool = False, force_chat_id: int = None, force_message=None
):
    if force and force_chat_id is not None and force_message is not None:
        # Manual nudge for a specific chat
        system_prompt = get_nudge_prompt(force_chat_id)
        message_list = [{"role": "system", "content": system_prompt}]
        raw_answer = await ask_agent(message_list, chat_id=force_chat_id)
        answer = clean_openai_reply(raw_answer)
        mark_bot_replied(force_chat_id)
        await send_nudge_with_image(
            force_message, force_chat_id, answer, is_message=True
        )
        return
    last_reset = datetime.now()
    logging.info(f"[nudge] Starting nudge loop. NUDGE_ENABLED_CHATS={NUDGE_ENABLED_CHATS}, NUDGE_MINUTES={NUDGE_MINUTES}, Active hours: {ACTIVE_START}-{ACTIVE_END} {BOT_TIMEZONE}")
    while True:
        try:
            current_time = datetime.now(BOT_TIMEZONE).time()
            if not is_active_hours():
                logging.info(f"[nudge] Outside active hours (current: {current_time}), sleeping...")
                await asyncio.sleep(NUDGE_CHECK_INTERVAL)
                continue
            logging.info(f"[nudge] Active hours check passed (current: {current_time})")
            now = datetime.now()
            # Reset bot_unmentioned_count every 5 minutes
            if (now - last_reset).total_seconds() >= NUDGE_RESET_INTERVAL:
                bot_unmentioned_count.clear()
                last_reset = now
            all_chats = set(agent_client._histories.keys()) | set(last_activity_time.keys())
            logging.debug(f"[nudge] Checking {len(all_chats)} chats: {all_chats}")
            for chat_id in all_chats:
                if chat_id not in NUDGE_ENABLED_CHATS:
                    logging.debug(f"[nudge] Skipping chat {chat_id} - not in enabled list")
                    continue
                last_time = last_activity_time.get(chat_id)
                if last_time is None:
                    # No activity recorded (e.g. just restarted) — skip, don't nudge
                    continue
                else:
                    minutes_passed = (now - last_time).total_seconds() / 60
                logging.info(f"[nudge] Chat {chat_id}: {minutes_passed:.1f} minutes since last message (threshold: {NUDGE_MINUTES})")
                if minutes_passed >= NUDGE_MINUTES:
                    try:
                        logging.info(f"[nudge] Sending automatic nudge to chat {chat_id}")
                        system_prompt = get_nudge_prompt(chat_id)
                        message_list = [{"role": "system", "content": system_prompt}]
                        raw_answer = await ask_agent(message_list, chat_id=chat_id)
                        answer = clean_openai_reply(raw_answer)
                        mark_bot_replied(chat_id)
                        await send_nudge_with_image(
                            bot, chat_id, answer, caption="", is_message=False
                        )
                        logging.info(f"[nudge] Nudge sent to chat {chat_id}")
                    except Exception as e:
                        logging.error(f"[nudge] Error sending nudge to chat {chat_id}: {e}", exc_info=True)
                        continue
            await asyncio.sleep(NUDGE_CHECK_INTERVAL)  # Check every minute
        except asyncio.CancelledError:
            logging.info("[nudge] Nudge loop cancelled, shutting down.")
            raise
        except Exception as e:
            logging.error(f"[nudge] Unexpected error in nudge loop: {e}", exc_info=True)
            await asyncio.sleep(NUDGE_CHECK_INTERVAL)  # Sleep before retry


def mark_bot_replied(chat_id):
    """Record that the bot sent a message in ``chat_id``."""
    now = datetime.now()
    last_activity_time[chat_id] = now
    last_bot_reply_time[chat_id] = now
    messages_since_bot_reply[chat_id] = 0


def clean_openai_reply(text: str) -> str:
    """Remove tagged sections like {24:0†foo.json}, 【4:5†foo.json】 or
    【0:tagged_jura_messages.json】 from OpenAI replies."""
    import re

    pattern = r"[\{【]\d+:[^】}]+[】\}]"
    return re.sub(pattern, "", text).strip()


def get_random_nudge_prompt():
    """Return a random nudge prompt from the predefined list, avoiding recent repeats."""
    global nudge_prompt_history
    available_prompts = [
        p for p in NUDGE_SYSTEM_PROMPTS if p not in nudge_prompt_history
    ]
    if not available_prompts:
        # If all prompts are in history, reset history except the last one
        nudge_prompt_history = nudge_prompt_history[-1:]
        available_prompts = [
            p for p in NUDGE_SYSTEM_PROMPTS if p not in nudge_prompt_history
        ]
    prompt = random.choice(available_prompts)
    nudge_prompt_history.append(prompt)
    if len(nudge_prompt_history) > NUDGE_PROMPT_HISTORY_LEN:
        nudge_prompt_history = nudge_prompt_history[-NUDGE_PROMPT_HISTORY_LEN:]
    return prompt


def get_nudge_prompt(chat_id: int) -> str:
    """Return the first nudge during the morning window, otherwise random."""
    now = datetime.now(BOT_TIMEZONE).time()
    if FIRST_NUDGE_ENABLED and FIRST_NUDGE_START <= now < FIRST_NUDGE_END:
        return FIRST_NUDGE_PROMPT
    return get_random_nudge_prompt()


def load_system_prompt() -> str:
    """Return the full contents of the system prompt file."""
    if not os.path.exists(SYSTEM_PROMPT_FILE):
        return ""
    with open(SYSTEM_PROMPT_FILE, "r", encoding="utf-8") as f:
        return f.read()


# History loading and scheduled summarization removed - now handled by agent_client


def _cleanup_old_claims(max_age: int = 300):
    """Remove claim files older than ``max_age`` seconds."""
    import time as _time
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


async def periodic_history_save():
    """Periodically save chat histories to disk."""
    try:
        while True:
            await asyncio.sleep(300)  # Save every 5 minutes
            agent_client.save_histories_to_disk()
            _cleanup_old_claims()
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

    await dp.start_polling(bot)


def main() -> None:
    asyncio.run(startup())


if __name__ == "__main__":
    main()
