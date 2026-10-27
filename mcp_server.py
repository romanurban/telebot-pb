from mcp.server.fastmcp import FastMCP
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote
import json
import re
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
    from urllib.parse import parse_qs

    headers = {"User-Agent": _DEFAULT_USER_AGENT}
    li_pattern = re.compile(r"<li>(.*?)</li>", re.DOTALL)
    async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
        # Random pages in the category include index/topic pages with no usable
        # proverbs (empty items or links to other proverb pages) — try a few.
        for _ in range(5):
            async with session.get(url, ssl=False) as resp:
                final_url = str(resp.url)
                html = await resp.text()
            parsed = urlparse(final_url)
            # Redirect lands on /w/index.php?title=<Page>&action=render
            title = parse_qs(parsed.query).get("title", [unquote(parsed.path.split("/")[-1])])[0]
            title = title.replace("_", " ")
            items = [
                " ".join(unescape(re.sub("<.*?>", "", m.group(1))).split())
                for m in li_pattern.finditer(html)
            ]
            proverbs = [
                i for i in items
                if len(i) >= 10 and " " in i
                and re.search(r"[а-яё]", i, re.IGNORECASE)
                and not re.search(r"[a-z]{3,}", i, re.IGNORECASE)
                and not re.search(r"пословиц|поговор", i, re.IGNORECASE)
            ]
            if proverbs:
                return f"{title}: {random.choice(proverbs)}"

    raise ValueError("no proverbs found")


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


# Memes: Reddit via meme-api.com (reddit.com itself answers 403 to servers).
# (subreddit, minimum upvotes, weight) — mostly Russian-language, some English classics.
MEME_SOURCES = [
    ("KafkaFPS", 100, 3),
    ("Pikabu", 100, 3),
    ("dankmemes", 1500, 2),
    ("me_irl", 1000, 1),
]
SEEN_MEMES_FILE = "/tmp/telebot_nudges/memes_seen.json"
SEEN_MEMES_KEEP = 300


def _load_seen(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as f:
            return list(json.load(f))
    except (OSError, ValueError):
        return []


def _save_seen(path: str, items: list[str], keep: int) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(items[-keep:], f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def _pick_meme(memes: list[dict], min_ups: int, seen: set[str]) -> dict | None:
    good = [
        m for m in memes
        if not m.get("nsfw") and not m.get("spoiler") and m.get("ups", 0) >= min_ups
        and str(m.get("url", "")).lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        and m.get("url") not in seen
    ]
    return max(good, key=lambda m: m.get("ups", 0)) if good else None


async def _memify_fallback(session) -> str | None:
    import re
    try:
        async with session.get("https://www.memify.ru/highfive/", ssl=False) as resp:
            html = await resp.text()
        urls = re.findall(r"https://[^\"']*?memify\.ru/[^\"']+\.(?:jpe?g|png|webp)", html, re.IGNORECASE)
        return random.choice(urls) if urls else None
    except Exception:
        return None


@mcp.tool()
async def retrieve_joke() -> str:
    """Fetch a random popular meme (never one already posted) and return the direct image URL."""
    seen = _load_seen(SEEN_MEMES_FILE)
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(headers={"User-Agent": _DEFAULT_USER_AGENT}, timeout=timeout,
                                     trust_env=True) as session:
        sources = list(MEME_SOURCES)
        random.shuffle(sources)
        sources.sort(key=lambda s: -s[2] * random.random())  # weighted order, still varied
        for sub, min_ups, _w in sources:
            try:
                async with session.get(f"https://meme-api.com/gimme/{sub}/25") as resp:
                    data = await resp.json(content_type=None)
            except Exception:
                continue
            meme = _pick_meme(data.get("memes") or [], min_ups, set(seen))
            if meme:
                _save_seen(SEEN_MEMES_FILE, seen + [meme["url"]], SEEN_MEMES_KEEP)
                return meme["url"]
        url = await _memify_fallback(session)
        if url and url not in seen:
            _save_seen(SEEN_MEMES_FILE, seen + [url], SEEN_MEMES_KEEP)
            return url
    raise RuntimeError("no fresh meme available")


# Facts: Russian Wikipedia «Знаете ли вы?» — curated, editor-checked facts, each
# linked to an article. Monthly archives go back years (100+ items per month).
DYK_ARCHIVE = "Проект:Знаете ли вы/Архив рубрики/{ym}"
DYK_FIRST_YEAR = 2016
SEEN_FACTS_FILE = "/tmp/telebot_nudges/facts_seen.json"


async def _wiki_parse(session, page: str) -> str:
    url = "https://ru.wikipedia.org/w/api.php"
    params = {"action": "parse", "page": page, "prop": "text", "format": "json", "formatversion": "2"}
    async with session.get(url, params=params) as resp:
        data = await resp.json(content_type=None)
    return data.get("parse", {}).get("text", "")


def _dyk_items(html_text: str) -> list[tuple[str, str]]:
    """(teaser, linked article title) pairs from a «Знаете ли вы» page."""
    import html
    import re

    out = []
    for li in re.findall(r"<li[^>]*>(.*?)</li>", html_text, re.S):
        link = re.search(r'<b>\s*<a [^>]*title="([^"]+)"', li) or re.search(r'<a [^>]*title="([^"]+)"', li)
        teaser = html.unescape(re.sub(r"<[^>]+>", "", li)).replace("\xa0", " ").strip()
        teaser = re.sub(r"\s*\(на илл\.\)", "", teaser)
        if link and 30 <= len(teaser) <= 300 and not teaser.startswith(("Во избежание", "Пожалуйста", "Будьте", "И только", "Убедитесь")):
            out.append((teaser, html.unescape(link.group(1))))
    return out


async def _wiki_summary(session, title: str) -> tuple[str, str]:
    from urllib.parse import quote as _q

    async with session.get(f"https://ru.wikipedia.org/api/rest_v1/page/summary/{_q(title.replace(' ', '_'))}") as resp:
        data = await resp.json(content_type=None)
    link = data.get("content_urls", {}).get("desktop", {}).get("page", "")
    return (data.get("extract") or "").strip(), link


@mcp.tool()
async def retrieve_fact() -> str:
    """A curated interesting fact from Russian Wikipedia's «Знаете ли вы?» with a short explanation and link."""
    seen = _load_seen(SEEN_FACTS_FILE)
    now = datetime.now()
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(headers={"User-Agent": _DEFAULT_USER_AGENT}, timeout=timeout,
                                     trust_env=True) as session:
        for _ in range(4):
            year = random.randint(DYK_FIRST_YEAR, now.year)
            month = random.randint(1, 12 if year < now.year else max(1, now.month - 1))
            try:
                items = _dyk_items(await _wiki_parse(session, DYK_ARCHIVE.format(ym=f"{year}-{month:02d}")))
            except Exception:
                continue
            items = [it for it in items if it[0] not in seen]
            if not items:
                continue
            teaser, title = random.choice(items)
            try:
                summary, link = await _wiki_summary(session, title)
            except Exception:
                summary, link = "", ""
            _save_seen(SEEN_FACTS_FILE, seen + [teaser], 500)
            sentences = re.split(r"(?<=[.!?])\s+", summary)
            short = " ".join(sentences[:2])[:400]
            parts = [teaser]
            if short:
                parts.append(f"Подробнее: {short}")
            if link:
                parts.append(link)
            return "\n".join(parts)
    raise RuntimeError("no fact available")


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

NEWS_FEEDS = {
    "lv": [("LSM", "https://rus.lsm.lv/rss/"), ("Delfi", "https://rus.delfi.lv/rss/index.xml")],
    "world": [("BBC", "https://feeds.bbci.co.uk/russian/rss.xml"), ("Meduza", "https://meduza.io/rss/all")],
}


def _parse_rss(xml_text: str, source: str, limit: int) -> list[dict]:
    import html
    import re
    import xml.etree.ElementTree as ET

    items = []
    root = ET.fromstring(xml_text)
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        summary = html.unescape(re.sub(r"<[^>]+>", " ", item.findtext("description") or ""))
        items.append({
            "source": source,
            "title": html.unescape(title),
            "summary": re.sub(r"\s+", " ", summary).strip()[:300],
            "link": link,
            "published": (item.findtext("pubDate") or "").strip(),
        })
        if len(items) >= limit:
            break
    return items


@mcp.tool()
async def get_news(region: str = "any", limit: int = 8) -> str:
    """Fresh Russian-language news headlines as JSON: a list of {source, title, summary, link, published}.

    ``region``: "lv" (Latvia: LSM, Delfi), "world" (BBC Russian, Meduza) or "any" (both).
    Use it when someone asks what's new, or to share a real piece of news. Never invent news.
    """
    regions = ["lv", "world"] if region not in NEWS_FEEDS else [region]
    feeds = [f for r in regions for f in NEWS_FEEDS[r]]
    items: list[dict] = []
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(headers={"User-Agent": _DEFAULT_USER_AGENT}, timeout=timeout,
                                     trust_env=True) as session:
        for source, url in feeds:
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    items.extend(_parse_rss(await resp.text(), source, limit))
            except Exception:
                continue  # one dead feed shouldn't kill the others
    if not items:
        raise RuntimeError("no news feeds reachable")
    return json.dumps(items, ensure_ascii=False)


@mcp.tool()
async def analyze_image(image_path: str, prompt: str = "Describe this image") -> str:
    """Analyze an image using a vision model and return a text description.

    ``image_path`` is the local file path to the image (e.g. /tmp/photo.jpg).
    ``prompt`` describes what to look for in the image.
    """
    import base64
    import logging

    logging.info(f"[analyze_image] Called with image_path={image_path}, prompt={prompt!r}")

    api_key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("OPENROUTER_VISION_MODEL", "qwen/qwen3-vl-235b-a22b-thinking:free")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    file_size = os.path.getsize(image_path)
    logging.info(f"[analyze_image] Reading file {image_path} ({file_size} bytes), model={model}")

    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    data_url = f"data:image/jpeg;base64,{b64}"

    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    logging.info(f"[analyze_image] Sending request to OpenRouter ({model})...")
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers, json=payload,
        ) as resp:
            data = await resp.json()
    logging.info(f"[analyze_image] Response status keys: {list(data.keys())}")
    if "error" in data:
        logging.error(f"[analyze_image] API error: {data['error']}")
        raise RuntimeError(f"OpenRouter vision error: {data['error']}")
    content = data["choices"][0]["message"]["content"]
    logging.info(f"[analyze_image] Got description ({len(content)} chars): {content[:200]!r}...")
    return content


if __name__ == "__main__":
    mcp.run(transport="sse")
