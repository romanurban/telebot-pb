"""File-based message bus for inter-bot communication.

Each chat gets a JSONL file in BOT_BUS_DIR. Bots append their outgoing
messages and poll for new lines from other bots.
"""

import json
import os
import time

BOT_BUS_DIR = os.getenv("BOT_BUS_DIR", "/tmp/telebot_bus")


def init_bus() -> None:
    """Create the bus directory if it doesn't exist."""
    os.makedirs(BOT_BUS_DIR, exist_ok=True)


def _bus_path(chat_id: int) -> str:
    return os.path.join(BOT_BUS_DIR, f"{chat_id}.jsonl")


def broadcast(
    chat_id: int, bot_username: str, text: str, *, via_bus: bool = False
) -> None:
    """Append a message to the bus file for ``chat_id``.

    When ``via_bus`` is True the message is marked as a reply that was itself
    triggered by the bus, so other bots won't start a new chain from it.

    Short writes on POSIX are atomic when under PIPE_BUF (typically 4096),
    so concurrent appends from different bots won't interleave.
    """
    record = {"bot": bot_username, "text": text, "ts": time.time()}
    if via_bus:
        record["via_bus"] = True
    line = json.dumps(record, ensure_ascii=False)
    path = _bus_path(chat_id)
    os.makedirs(BOT_BUS_DIR, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def poll(
    chat_id: int, bot_username: str, last_pos: int
) -> tuple[list[dict], int]:
    """Read new lines from ``last_pos`` and return messages from other bots.

    Returns ``(messages, new_pos)`` where each message is a dict with
    ``bot``, ``text``, and ``ts`` keys.
    """
    path = _bus_path(chat_id)
    if not os.path.exists(path):
        return [], 0

    messages: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        f.seek(last_pos)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("bot") != bot_username:
                messages.append(msg)
        new_pos = f.tell()

    return messages, new_pos


def last_message_time(chat_id: int) -> float | None:
    """Return the epoch timestamp of the most recent bus message for ``chat_id``."""
    path = _bus_path(chat_id)
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            end = f.tell()
            if end == 0:
                return None
            f.seek(max(0, end - 4096))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(tail.strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line).get("ts")
        except (json.JSONDecodeError, AttributeError):
            continue
    return None


def trim(chat_id: int, max_lines: int = 200) -> None:
    """Keep only the last ``max_lines`` lines in the bus file."""
    path = _bus_path(chat_id)
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if len(lines) <= max_lines:
        return

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines[-max_lines:])
