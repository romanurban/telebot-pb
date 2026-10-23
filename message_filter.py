"""Message filtering utilities for bot responses."""
import random
import re

# Trailing emoji cluster (plus variation selectors / ZWJ / skin tones) at the end of a reply.
_TRAILING_EMOJI_RE = re.compile(
    r"[\s]*[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u200D\U0001F3FB-\U0001F3FF]+\s*$"
)
EMOJI_KEEP_CHANCE = 0.3


def thin_emoji(text: str, keep_chance: float = EMOJI_KEEP_CHANCE, rand=random.random) -> str:
    """Drop a trailing emoji most of the time, so it doesn't become a signature tic."""
    if not text or rand() < keep_chance:
        return text
    stripped = _TRAILING_EMOJI_RE.sub("", text).rstrip()
    return stripped or text


def clean_self_mentions(message: str, bot_username: str) -> str:
    """Remove self-mentions from bot messages to prevent confusion.
    
    Args:
        message: The bot's message text
        bot_username: The username of the current bot
    
    Returns:
        Message with self-mentions removed
    """
    if not message or not bot_username:
        return message
    
    # Remove @bot_username mentions (case insensitive)
    mention_pattern = rf'@{re.escape(bot_username)}\b'
    cleaned = re.sub(mention_pattern, '', message, flags=re.IGNORECASE)
    
    # Clean up extra whitespace and punctuation left by removal ("@me, ты..." -> "ты...")
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    cleaned = re.sub(r'^[\s,.:;!—–-]+', '', cleaned)
    cleaned = re.sub(r'\s+([,.!?])', r'\1', cleaned)
    
    # If message becomes too short or meaningless, return original
    if len(cleaned) < 5:
        return message
    
    return cleaned


def validate_bot_interaction(message: str, bot_username: str) -> bool:
    """Check if a bot message is valid for inter-bot communication.
    
    Args:
        message: The bot's message text
        bot_username: The username of the current bot
    
    Returns:
        True if message is valid for sending
    """
    if not message or not message.strip():
        return False
    
    # Check if bot is only talking to itself
    mention_pattern = rf'@(\w+bot)\b'
    mentions = re.findall(mention_pattern, message, re.IGNORECASE)
    
    # If there are mentions and they're all self-mentions, reject
    if mentions and all(mention.lower() == bot_username.lower() for mention in mentions):
        return False
    
    return True

_CYR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p",
                 "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))
_MENTION_RE = re.compile(r"@([0-9A-Za-z_Ѐ-ӿ]+)")


def _norm_handle(handle: str) -> str:
    latin = "".join(_CYR.get(c, c) for c in handle.lower())
    return re.sub(r"[^a-z0-9]", "", latin)


def fix_mentions(text: str, known_bots) -> str:
    """Repair or drop bot @mentions the model garbled.

    Models sometimes half-transliterate a username ("@neuромаксимбот"), which
    pings nobody. A mention close to a known bot becomes that bot's real
    handle; a bot-looking or Cyrillic mention that matches nothing is dropped.
    Ordinary human usernames are left alone.
    """
    if not text or "@" not in text or not known_bots:
        return text
    import difflib

    known = {b.lower(): b for b in known_bots}
    normed = {_norm_handle(b): b for b in known_bots}

    def repl(m):
        handle = m.group(1)
        if handle.lower() in known:
            return "@" + known[handle.lower()]
        n = _norm_handle(handle)
        best = difflib.get_close_matches(n, list(normed), n=1, cutoff=0.75)
        if best:
            return "@" + normed[best[0]]
        if re.search(r"[Ѐ-ӿ]", handle) or n.endswith("bot") or handle.lower().endswith("бот"):
            return ""
        return m.group(0)

    return _MENTION_RE.sub(repl, text)


def tidy_reply(text: str) -> str:
    """Remove punctuation debris left where a mention was cut out ("Кину, . Восстание")."""
    if not text:
        return text
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]{2,}", " ", line)
        line = re.sub(r",\s*(?=[,.!?;:])", "", line)       # ", ." / ", ," -> "." / ","
        line = re.sub(r"[ \t]+(?=[,.!?;:])", "", line)      # "слово ," -> "слово,"
        line = re.sub(r"^\s*[,;:]+\s*", "", line)            # leading ", "
        line = re.sub(r"\(\s*\)", "", line)
        lines.append(line.strip())
    return "\n".join(lines).strip()


_STOP = {
    "это", "этот", "эта", "эти", "того", "тоже", "только", "когда", "будет", "есть", "было", "были",
    "просто", "вообще", "сегодня", "тебя", "меня", "тебе", "мне", "себя", "если", "даже", "ещё", "еще",
    "уже", "всех", "всё", "все", "теперь", "потом", "тогда", "чтобы", "очень", "можно", "нужно", "надо",
    "какой", "какая", "какие", "который", "которая", "там", "тут", "здесь", "кстати", "типо", "вроде",
}
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+")


def _stems(text: str) -> set[str]:
    text = re.sub(r"@\w+|https?://\S+", " ", text or "")
    words = [w.lower().replace("ё", "е") for w in _WORD_RE.findall(text)]
    return {w[:5] for w in words if len(w) >= 4 and w not in _STOP}


def echo_problem(reply: str, previous: str | None) -> str | None:
    """Return a reason if ``reply`` mostly re-uses the words of ``previous``.

    Catches the word-rally pattern ("без возврата" -> "без возврата — это
    преимущество", "навеса нет" -> "навеса нет — мерзнешь") that a prompt rule
    alone doesn't stop on the free models. Crude 5-letter stems are enough for Russian.
    """
    if not reply or not previous:
        return None
    mine, theirs = _stems(reply), _stems(previous)
    if not mine:
        return None
    shared = mine & theirs
    if len(shared) >= 2 and len(shared) / len(mine) >= 0.4:
        return f"ответ повторяет слова собеседника ({', '.join(sorted(shared))})"
    if len(mine) <= 3 and len(shared) >= 1 and len(shared) / len(mine) >= 0.5:
        return f"ответ подхватывает слово собеседника ({', '.join(sorted(shared))})"
    return None


def latin_problem(text: str) -> str | None:
    """Return a reason if a Cyrillic-only persona slipped Latin words in ("Ziepniekalns", "autobus")."""
    if not text:
        return None
    cleaned = re.sub(r"@\w+|https?://\S+|/\w+", " ", text)
    latin = re.findall(r"[A-Za-z]{3,}", cleaned)
    if latin:
        return f"латиница в ответе ({', '.join(latin[:3])}) — пиши всё кириллицей"
    return None
