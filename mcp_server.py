from mcp.server.fastmcp import FastMCP
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote
import json
import random
import os
from hashlib import sha256
import aiohttp
import dotenv

dotenv.load_dotenv()

_CYRILLIC_TO_LATIN_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh",
    "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}

_CYRILLIC_TO_LATIN_MAP.update({k.upper(): v.capitalize() for k, v in _CYRILLIC_TO_LATIN_MAP.items()})


def _cyrillic_to_latin(text: str) -> str:
    """Return `text` with Cyrillic letters transliterated to Latin."""
    return "".join(_CYRILLIC_TO_LATIN_MAP.get(ch, ch) for ch in text)

STORY_HISTORY_LIMIT = 20

_story_history_hashes: set[str] = set()

_DEFAULT_USER_AGENT = (
    os.getenv("HTTP_USER_AGENT")
    or "telebot/1.0 (+https://github.com/urban-roman/telebot)"
)

MCP_PORT = int(os.getenv("MCP_PORT", "8888"))
mcp = FastMCP(port=MCP_PORT)

@mcp.tool()
def get_current_datetime() -> str:
    """Return the current Riga time with the weekday name.

    Use this whenever a question involves the exact time, date or season so the
    assistant can provide an accurate answer.
    """
    now = datetime.now(ZoneInfo("Europe/Riga"))
    weekday = now.strftime("%A")
    return f"{now.isoformat()} ({weekday})"


@mcp.tool()
async def get_current_weather(location: str) -> str:
    """Return current weather for a given location.

    `location` is a human readable place name, like "Riga" or "Riga, Latvia".
    The function will geocode it and return the current weather data as JSON.
    Anything after a comma is ignored so "Riga, Latvia" becomes "Riga".
    """
    location = location.split(",", 1)[0].strip()
    query = quote(_cyrillic_to_latin(location))

    async with aiohttp.ClientSession(trust_env=True) as session:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={query}&count=1"
        async with session.get(geo_url, ssl=False) as resp:
            geo_data = await resp.json()

        if not geo_data.get("results"):
            raise ValueError("location not found")

        lat = geo_data["results"][0]["latitude"]
        lon = geo_data["results"][0]["longitude"]

        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}&current=temperature_2m"
        )
        async with session.get(url, ssl=False) as resp:
            return await resp.text()


@mcp.tool()
def get_random_story(path: str = "dataset/neuroyury_bot/stories.txt") -> str:
    """Return a random story entry from a file, avoiding recent repeats.

    The file should contain blocks separated by lines with '---'. The first line
    of each block is treated as the topic and the rest as the text. The function
    returns them joined with a colon and a space. Previously returned stories are
    tracked by hash to minimize repetition. After STORY_HISTORY_LIMIT unique
    stories, the history is cleared so they may repeat again.
    """
    global _story_history_hashes

    if not os.path.exists(path):
        raise FileNotFoundError(path)

    if len(_story_history_hashes) >= STORY_HISTORY_LIMIT:
        _story_history_hashes.clear()

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    entries = [block.strip() for block in content.split("---") if block.strip()]
    if not entries:
        raise ValueError("no entries found")

    available = [e for e in entries if sha256(e.encode()).hexdigest() not in _story_history_hashes]
    if not available:
        _story_history_hashes.clear()
        available = entries

    choice = random.choice(available)
    _story_history_hashes.add(sha256(choice.encode()).hexdigest())

    lines = [line.strip() for line in choice.splitlines() if line.strip()]
    if not lines:
        raise ValueError("empty entry")
    topic = lines[0]
    text = " ".join(lines[1:]) if len(lines) > 1 else ""
    return f"{topic}: {text}".strip()


@mcp.tool()
async def get_wikipedia_extract(date: str) -> str:
    """Return the Russian Wikipedia extract for a given date.

    `date` should be a string like "17 июня" or "18 июня". The function
    queries the Wikipedia API and returns the page extract as plain text.
    """
    query = quote(date.replace(" ", "_"), encoding="utf-8")
    url = (
        "https://ru.wikipedia.org/w/api.php?"
        "action=query&prop=extracts&explaintext=1&exsectionformat=plain&"
        "format=json&formatversion=2&titles=" + query
    )
    headers = {"User-Agent": _DEFAULT_USER_AGENT}
    async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
        async with session.get(url, ssl=False) as resp:
            data = await resp.json()

    pages = data.get("query", {}).get("pages", [])
    if not pages:
        raise ValueError("page not found")

    extract = pages[0].get("extract", "").strip()
    if not extract:
        raise ValueError("extract not found")

    return extract


@mcp.tool()
async def get_random_proverb() -> str:
    """Вернуть случайную пословицу или поговорку из русской Викицитатника.

    Используй функцию, когда просят выдать "случайную" или "рандомную"
    пословицу, поговорку, крылатое выражение, цитату, афоризм. Она выбирает
    произвольную страницу в категории "Пословицы", берет одну из записей и
    возвращает её вместе с названием страницы.
    """
    import re
    from html import unescape
    from urllib.parse import urlparse, unquote

    url = (
        "https://ru.wikiquote.org/wiki/"
        "Special:RandomInCategory/%D0%9F%D0%BE%D1%81%D0%BB%D0%BE%D0%B2%D0%B8%D1%86%D1%8B?action=render"
    )
    headers = {"User-Agent": _DEFAULT_USER_AGENT}
    async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
        async with session.get(url, ssl=False) as resp:
            final_url = str(resp.url)
            html = await resp.text()

    title = unquote(urlparse(final_url).path.split("/")[-1]).replace("_", " ")

    li_pattern = re.compile(r"<li>(.*?)</li>", re.DOTALL)
    items = [unescape(re.sub("<.*?>", "", m.group(1))).strip() for m in li_pattern.finditer(html)]
    proverbs = [i for i in items if i]
    if not proverbs:
        raise ValueError("no proverbs found")

    return f"{title}: {random.choice(proverbs)}"


@mcp.tool()
async def get_picture_of_the_day(date: str = "") -> str:
    """Return Wikimedia picture of the day URL and caption as JSON.

    ``date`` should be ``YYYY-MM-DD``. If omitted, today's date is used.
    The JSON contains ``url`` (direct link to the image) and ``caption``.
    """
    import re

    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    url = (
        "https://en.wikipedia.org/wiki/Template:POTD/"
        f"{date}?action=raw&ctype=text/plain"
    )
    headers = {"User-Agent": _DEFAULT_USER_AGENT}
    async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
        async with session.get(url, ssl=False) as resp:
            text = await resp.text()

    image_match = re.search(r"\|image=([^\n]+)", text)
    if not image_match:
        raise ValueError("image not found")
    image_name = image_match.group(1).strip()

    caption_match = re.search(r"\|caption=\n*(.*?)\n\|credit=", text, re.S)
    if not caption_match:
        raise ValueError("caption not found")
    caption = caption_match.group(1).strip()

    # Strip simple wiki markup
    caption = re.sub(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]", r"\1", caption)
    caption = caption.replace("''", "")
    caption = re.sub(r"<.*?>", "", caption)

    image_url = (
        "https://commons.wikimedia.org/wiki/Special:FilePath/" + quote(image_name) + "?width=800"
    )

    return json.dumps({"url": image_url, "caption": caption})


@mcp.tool()
async def retrieve_joke() -> str:
    """Fetch a random meme image and return the direct image URL."""

    import re
    import aiohttp

    headers = {"User-Agent": "Mozilla/5.0"}
    async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
        try:
            async with session.get("https://www.memify.ru/highfive/", ssl=False) as resp:
                html = await resp.text()
            urls = re.findall(
                r"https://[^\"']*?memify\.ru/[^\"']+\.(?:jpe?g|png|gif|webp)",
                html,
                re.IGNORECASE,
            )
        except Exception:
            urls = []
        if not urls:
            return "https://i.imgflip.com/1bij.jpg"
        img_url = random.choice(urls)
    return img_url


@mcp.tool()
async def retrieve_fact() -> str:
    """Fetch a random fact from randstuff.ru and return the text."""

    import re
    from html import unescape
    import aiohttp

    headers = {"User-Agent": _DEFAULT_USER_AGENT}
    async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
        async with session.get("https://randstuff.ru/fact/", ssl=False) as resp:
            html = await resp.text()

    match = re.search(r'<div id="fact".*?<td>(.*?)</td>', html, re.S)
    if not match:
        raise ValueError("fact not found")

    fact = unescape(re.sub(r"<.*?>", "", match.group(1))).strip()
    if not fact:
        raise ValueError("fact not found")
    return fact


@mcp.tool()
async def generate_voice(text: str) -> str:
    """Convert ``text`` to speech using ElevenLabs and return a local mp3 path."""

    import aiohttp
    from tempfile import NamedTemporaryFile

    api_key = os.getenv("ELEVEN_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVEN_API_KEY not set")

    voice_id = os.getenv("ELEVEN_VOICE_ID", "EZQLe5vG5r2BoTGdGRL7")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "accept": "audio/mpeg",
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.68,
            "similarity_boost": 1.0,
            "style": 0.0,
            "use_speaker_boost": True,
            "speed": 0.9,
        },
    }

    async with aiohttp.ClientSession(trust_env=True) as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status != 200:
                raise ValueError(f"request failed with status {resp.status}")
            data = await resp.read()

    with NamedTemporaryFile(delete=False, suffix=".mp3", dir="/tmp") as f:
        f.write(data)
        return f.name

if __name__ == "__main__":
    mcp.run(transport="sse")
