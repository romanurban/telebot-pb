import logging
import os
import random
import re
from datetime import datetime, time
from zoneinfo import ZoneInfo

import yaml
from openai import AsyncOpenAI

from agent_client import USE_OPENROUTER


TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")

SYSTEM_PROMPT_FILE = os.path.join("prompts", BOT_USERNAME, "system_prompt.yaml")
BOT_PROMPTS_FILE = os.path.join("prompts", BOT_USERNAME, "bot_prompts.yaml")
DEFAULT_BOT_PROMPTS_FILE = os.path.join("prompts", "default_bot", "bot_prompts.yaml")

_NUDGE_MINUTES_BASE = int(os.getenv("NUDGE_MINUTES", 120))


def get_nudge_minutes() -> int:
    """Return nudge interval with a fresh random offset each call."""
    offset = random.choice([-1, 1]) * random.randint(20, 30)
    return max(30, _NUDGE_MINUTES_BASE + offset)

IMAGE_GEN_MODEL = os.getenv("IMAGE_GEN_MODEL", "gpt-image-1.5")
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8888/sse")

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

FIRST_NUDGE_PROMPT = NUDGE_SYSTEM_PROMPTS[0] if NUDGE_SYSTEM_PROMPTS else ""
FIRST_NUDGE_START = time(10, 0)
FIRST_NUDGE_END = time(12, 0)
FIRST_NUDGE_ENABLED = os.getenv("FIRST_NUDGE_ENABLED", "false").lower() in ("true", "1", "yes")

_nudge_chats_str = os.getenv("NUDGE_ENABLED_CHATS", "")
NUDGE_ENABLED_CHATS = set()
if _nudge_chats_str:
    for chat_id_str in _nudge_chats_str.split(","):
        try:
            NUDGE_ENABLED_CHATS.add(int(chat_id_str.strip()))
        except ValueError:
            logging.warning(f"Invalid chat ID in NUDGE_ENABLED_CHATS: {chat_id_str}")

NUDGE_PROMPT_HISTORY_LEN = 3
nudge_prompt_history = []

BOT_TIMEZONE = ZoneInfo(os.getenv("BOT_TIMEZONE", "Europe/Riga"))


def _parse_time(time_str: str, default: time) -> time:
    try:
        h, m = time_str.split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return default


ACTIVE_START = _parse_time(os.getenv("ACTIVE_START", "10:00"), time(10, 0))
ACTIVE_END = _parse_time(os.getenv("ACTIVE_END", "21:00"), time(21, 0))

MAX_UNMENTIONED_REPLIES = 3
IMAGE_SEND_CHANCE = float(os.getenv("IMAGE_SEND_CHANCE", 0.3))
NUDGE_RESET_INTERVAL = 300
NUDGE_CHECK_INTERVAL = 60
RECENT_ACTIVITY_SECONDS = 30

_openai_images_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def get_openai_images_client() -> AsyncOpenAI | None:
    return _openai_images_client


def validate_environment():
    """Validate that all required environment variables are set."""
    missing = []

    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN.startswith("<"):
        missing.append("TELEGRAM_TOKEN")

    if not USE_OPENROUTER:
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

    if not os.path.exists(SYSTEM_PROMPT_FILE):
        logging.error(f"System prompt file not found: {SYSTEM_PROMPT_FILE}")
        logging.error(f"Please create prompts/{BOT_USERNAME}/system_prompt.yaml")
        logging.error("You can copy from prompts/default_bot/ as a starting point.")
        raise SystemExit(1)


def is_active_hours(now: datetime | None = None) -> bool:
    current = (now or datetime.now(BOT_TIMEZONE)).time()
    return ACTIVE_START <= current <= ACTIVE_END
