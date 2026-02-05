# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Async Telegram group bot powered by aiogram and the OpenAI Agents SDK. The bot can join conversations autonomously, uses MCP (Model Context Protocol) tools for extended capabilities, and maintains per-chat conversation history.

**Development Environment**: This project uses `uv` for Python package and environment management. All commands should be run via `uv run` when possible.

**Development Principle**: Follow the KISS (Keep It Simple, Stupid) principle. Prefer straightforward, maintainable solutions over complex abstractions.

## Common Commands

### Development Setup
```bash
pip install -e .         # Install base dependencies
pip install -e .[dev]    # Install with dev dependencies (pytest, pytest-asyncio)
```

### Running the Bot
```bash
uv run ./start.sh              # Start both MCP server and bot (uses tmux if available)
uv run ./start.sh --autoupdate # Auto-restart on git updates every 15 min
python main.py                 # Run bot directly (requires MCP server running separately)
python mcp_server.py           # Run MCP server directly
```

### Testing
```bash
pytest -q                # Run all tests
pytest tests/test_*.py   # Run specific test file
```

## Architecture

### Core Components

**main.py** - Telegram bot logic using aiogram:
- Handles mentions (`@bot_name`) and probabilistic unmentioned replies
- Photo analysis via OpenAI vision
- Commands: `/meme`, `/voice`, `/fact`
- Nudge system for chat activity prompts
- Chat history management with scheduled summarization
- Probabilistic reply logic: responds more frequently as messages accumulate without bot participation

**agent_client.py** - OpenAI Agents SDK integration:
- Manages agent initialization with system prompts
- Per-chat conversation history tracking (in `_histories` dict keyed by chat_id)
- History trimming and summarization to manage context length
- Handles MCP server connections

**mcp_server.py** - FastMCP server providing tools to the agent:
- `get_current_datetime()` - Returns Riga time with weekday
- `get_current_weather(location)` - Weather data via Open-Meteo API
- `get_random_story(path)` - Retrieves random story entries with deduplication
- `retrieve_joke()` - Fetches random meme image URLs
- `get_picture_of_the_day()` - NASA APOD integration
- `retrieve_fact()` - Random fact from uselessfacts.jsph.pl API
- `generate_voice(text)` - ElevenLabs voice generation

### Bot Configuration

Each bot requires configuration in `prompts/<bot_name>/`:
- **system_prompt.yaml** - System instructions for the OpenAI agent (can be plain text or YAML)
- **bot_prompts.yaml** - Contains:
  - `nudge_system_prompts` - Rotating prompts for inactivity nudges
  - `image_default_prompt` - Default prompt when analyzing photos
  - `chat_react_prompt` - Prompt for unmentioned message reactions
  - `image_gen_input_prompt` - Template for image generation requests

Example structure from `prompts/default_bot/system_prompt.yaml`:
```yaml
name: default_bot
intro: >
  You are a simple demonstration bot.
behavior:
  - "Keep responses under two sentences"
  - "When asked for a meme, reply with `/meme`"
  - "When asked for voice, call the generate_voice tool"
```

### Chat History System

- **In-memory history**: `chat_histories` dict stores recent (username, text) pairs
- **Agent history**: Per-chat message history in `agent_client._histories` for OpenAI API
- **Summarization**: Scheduled at configurable times (default 12:20, 18:20, 23:20 Riga time)
- **Reset threshold**: Controlled by `HISTORY_RESET_MESSAGES` env var (default: 10 messages)

### Reply Logic Flow

1. **Mentioned messages** (`@bot_name`): Always respond
2. **Commands** (`/meme`, `/voice`, `/fact`): Direct handling
3. **Photos**: Analyzed via OpenAI vision with configurable prompt
4. **Unmentioned messages**: Probabilistic response based on:
   - Messages since last bot reply (higher count = higher probability)
   - Recent bot activity within 30 seconds (always respond)
   - Limit of 3 unmentioned replies per cycle
   - Resets every 5 minutes or when bot is mentioned

### Nudge System

- Triggers after `NUDGE_MINUTES` of chat inactivity (default: 120 min)
- Only during active hours (10:00-21:00 Riga time by default)
- Enabled for specific chat IDs in `NUDGE_ENABLED_CHATS`
- Rotates through prompts from `bot_prompts.yaml` avoiding recent repeats
- Can attach generated images when assistant replies with JSON format: `{"image": "...", "caption": "..."}`
- Special "first nudge" between 10:00-12:00 uses first prompt from list

### Environment Variables

Required in `.env` (never commit this file):
- `TELEGRAM_TOKEN` - Telegram Bot API token
- `OPENAI_API_KEY` - OpenAI API key
- `BOT_USERNAME` - Bot username (determines which prompts/ folder to use)

Optional:
- `OPENAI_MODEL` - Model to use (default: "gpt-5.1")
- `IMAGE_GEN_MODEL` - Model for image generation (default: "gpt-image-1.5")
- `MCP_SERVER_URL` - MCP server endpoint (default: "http://127.0.0.1:8888/sse")
- `ELEVEN_API_KEY` - ElevenLabs API key for voice generation (required only if using voice features)
- `ELEVEN_VOICE_ID` - ElevenLabs voice ID
- `HISTORY_RESET_MESSAGES` - Messages before wiping context (default: 10, set 0 to disable)
- `MAX_HISTORY` - Max in-memory history length (default: 20)
- `NUDGE_MINUTES` - Inactivity before nudge (default: 120)
- `NUDGE_ENABLED_CHATS` - Comma-separated chat IDs where nudge is enabled
- `BOT_TIMEZONE` - Timezone for bot operations (default: "Europe/Riga")
- `ACTIVE_START` - Start of active hours in HH:MM format (default: "10:00")
- `ACTIVE_END` - End of active hours in HH:MM format (default: "21:00")
- `SUMMARY_TIMES` - Comma-separated HH:MM times for scheduled summarization

## Key Implementation Details

### Testing Patterns

- Tests use `pytest-asyncio` for async functions
- Minimal environment variables set automatically in test fixtures
- Tests are located in `tests/` and follow `test_*.py` naming
- Mock external APIs (OpenAI, ElevenLabs, Telegram) in tests

### Voice Message Handling

Bot can generate voice messages via ElevenLabs when:
1. User requests with `/voice <text>`
2. Text contains Russian words starting with "голос"
3. Assistant calls the `generate_voice` tool

Voice files are saved to `/tmp/voice_*.ogg` and cleaned up after sending.

### Image Handling

**Input (user photos)**:
- Downloaded from Telegram
- Uploaded to OpenAI Files API with `purpose="vision"`
- Passed to agent with structure: `{"type": "input_image", "file_id": "<file_id>"}`

**Output (bot images)**:
- Assistant can return JSON: `{"image": "<url_or_base64>", "caption": "..."}`
- Base64 images decoded and sent as BufferedInputFile
- URL images downloaded to `/tmp` and sent as FSInputFile

### History Normalization

The `_normalize_history()` function in agent_client.py converts plain text content to typed format for the Agents SDK API:
- User/bot text messages: `{"type": "input_text", "text": "..."}`
- Assistant text messages: `{"type": "output_text", "text": "..."}`
- System messages remain as plain strings
- Non-text content types are left unchanged: `input_image`, `input_file`, `computer_screenshot`, `summary_text`, `refusal`
