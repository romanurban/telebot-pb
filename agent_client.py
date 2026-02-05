import os
import json
from agents import (
    Agent,
    Runner,
    AsyncOpenAI,
    set_default_openai_client,
    ModelSettings,
    RunConfig,
)
from agents.mcp import MCPServerSse

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "<YOUR_OPENAI_API_KEY>")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.1")
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8888/sse")

HISTORY_DIR = "chat_history"
MAX_HISTORY = 20  # Keep last N messages (user + assistant) per chat

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
set_default_openai_client(openai_client)


_agent: Agent | None = None
_mcp_server: MCPServerSse | None = None
_system_history: list[dict] = []
_histories: dict[int, list[dict]] = {}  # chat_id -> last N user messages


def _normalize_history(history: list[dict]) -> list[dict]:
    """Convert plain text content to the typed format used by the Agents SDK API."""
    normalized = []

    for msg in history:
        msg_copy = dict(msg)
        msg_copy.pop("reasoning", None)
        if msg_copy.get("type") in ("reasoning", "output_reasoning"):
            continue
        role = msg_copy.get("role")
        content = msg_copy.get("content")

        if role == "system":
            normalized.append(msg_copy)
            continue

        if role in ("user", "bot"):
            target_type = "input_text"
        elif role == "assistant":
            target_type = "output_text"
        else:
            target_type = "input_text"

        if isinstance(content, list):
            new_content = []
            for item in content:
                if isinstance(item, dict):
                    item_copy = dict(item)
                    current_type = item_copy.get("type")
                    if current_type in ("input_image", "input_file", "computer_screenshot", "summary_text", "refusal"):
                        new_content.append(item_copy)
                        continue
                    if current_type in ("text", "input_text", "output_text"):
                        item_copy["type"] = target_type
                    new_content.append(item_copy)
                else:
                    new_content.append(item)
            msg_copy["content"] = new_content
        elif isinstance(content, str):
            msg_copy["content"] = [{"type": target_type, "text": content}]

        normalized.append(msg_copy)

    return normalized


async def create_thread_with_system_prompt(
    system_prompt: str, bot_name: str | None = None
) -> None:
    """Initialize the agent with a system prompt."""
    global _agent, _system_history, _histories, _mcp_server
    if bot_name is None:
        bot_name = os.getenv("BOT_USERNAME", "telebot")
    _mcp_server = MCPServerSse({"url": MCP_SERVER_URL})
    await _mcp_server.connect()
    _agent = Agent(
        name=bot_name,
        instructions=system_prompt,
        tools=[],
        mcp_servers=[_mcp_server],
        model=OPENAI_MODEL,
    )
    _system_history = [{"role": "system", "content": system_prompt}]


async def ask_agent(contents: list[dict], chat_id: int, *, tool_choice: str | None = None) -> str:
    """Send message contents to the agent and return its reply.

    History is simple: system prompt + last MAX_HISTORY user messages + new contents.
    Only user messages from contents are stored in history.
    """
    if _agent is None:
        raise RuntimeError("Agent not initialized")

    # Store user messages from contents into history
    history = _histories.get(chat_id, [])
    for msg in contents:
        if msg.get("role") == "user":
            history.append(msg)

    # Build API input: system prompt + history + non-user hints from contents
    api_history = list(_system_history) + history
    for msg in contents:
        if msg.get("role") != "user" and msg not in api_history:
            api_history.append(msg)

    api_history = _normalize_history(api_history)
    print(f"[ask_agent] Chat {chat_id}: sending {len(api_history)} messages")

    run_cfg = None
    if tool_choice:
        run_cfg = RunConfig(model_settings=ModelSettings(tool_choice=tool_choice))
    result = await Runner.run(_agent, api_history, run_config=run_cfg)

    reply = str(result.final_output)

    # Store assistant response in history so model knows what it already said
    history.append({"role": "assistant", "content": reply})

    # Trim history to last MAX_HISTORY messages
    _histories[chat_id] = history[-MAX_HISTORY:]

    return reply


# === History Management ===


def get_history(chat_id: int) -> list[dict] | None:
    return _histories.get(chat_id)


def clear_history(chat_id: int) -> None:
    global _histories
    if chat_id in _histories:
        del _histories[chat_id]


def save_histories_to_disk() -> None:
    os.makedirs(HISTORY_DIR, exist_ok=True)
    for chat_id, history in _histories.items():
        file_path = os.path.join(HISTORY_DIR, f"{chat_id}.json")
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[save_histories] Error saving chat {chat_id}: {e}")
    print(f"[save_histories] Saved {len(_histories)} chats")


def load_histories_from_disk() -> None:
    global _histories
    if not os.path.exists(HISTORY_DIR):
        return
    for filename in os.listdir(HISTORY_DIR):
        if not filename.endswith(".json"):
            continue
        try:
            chat_id = int(filename[:-5])
            with open(os.path.join(HISTORY_DIR, filename), "r", encoding="utf-8") as f:
                raw = json.load(f)
            # Keep user and assistant messages — discard old SDK-format entries
            clean = [m for m in raw if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
            _histories[chat_id] = clean[-MAX_HISTORY:]
        except (ValueError, json.JSONDecodeError) as e:
            print(f"[load_histories] Error loading {filename}: {e}")
    print(f"[load_histories] Loaded {len(_histories)} chats")
