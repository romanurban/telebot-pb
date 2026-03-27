"""Simple runtime state for telebot.

KISS version: plain module globals, no abstractions.
"""

# chat_id: datetime — any message, used by nudge timer
last_activity_time = {}

# set when nudge loop starts; prevents nudging right after restart
nudge_loop_started_at = None

# chat_id: datetime — bot replies only, used by probabilistic logic
last_bot_reply_time = {}

# chat_id: int
bot_unmentioned_count = {}

# chat_id: int — user messages since last bot reply
messages_since_bot_reply = {}

# Bot bus: track file read positions per chat
_bus_positions: dict[int, int] = {}

# chat_id -> timestamp of last bus-triggered reply
_bus_last_reply: dict[int, float] = {}
