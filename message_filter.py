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