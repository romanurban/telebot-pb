"""Message filtering utilities for bot responses."""
import re


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
    
    # Clean up extra whitespace left by removal
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
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