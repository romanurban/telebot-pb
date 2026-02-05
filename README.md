# Telebot

Async Telegram group bot powered by aiogram and the OpenAI Agents SDK.
Supports multiple bot personas, MCP tools, and autonomous conversation participation.

## Features
- Replies to `@bot_username` mentions using OpenAI
- May answer unmentioned messages based on recent activity
- Sends nudge messages after periods of inactivity (can mention other bots)
- Handles photos: uploads the image to OpenAI and replies with analysis
- MCP tools: weather, jokes, facts, picture of the day, voice generation
- Multi-persona support: run multiple bots with different personalities
- Persists chat history (user + assistant messages) for context

## Quick Start

### 1. Clone the repository
```sh
git clone <your-repo-url>
cd telebot
```

### 2. Install dependencies (Python 3.13+)
```sh
pip install -e .       # add [dev] to run tests
```

### 3. Create a Telegram bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts to create your bot
3. Copy the **API token** (looks like `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)
4. Configure bot settings in BotFather:
   - `/mybots` -> Select your bot -> **Bot Settings**
   - **Group Privacy** -> Turn **OFF** (required for bot to see all messages in groups)
   - Optional: Set bot name, description, and profile picture

**Important:** After disabling Group Privacy, you must **remove and re-add** the bot to existing groups for the change to take effect.

### 4. Configure environment variables
Copy `.env.example` to `.env.<bot_name>` for each bot:
```sh
cp .env.example .env.my_bot
```

Required variables:
- `TELEGRAM_TOKEN` - Your bot token from @BotFather
- `OPENAI_API_KEY` - Your OpenAI API key
- `BOT_USERNAME` - Bot username (determines which `prompts/<bot_name>/` folder to use)

Optional variables:
- `OPENAI_MODEL` - Model for the agent (default: `gpt-5.1`)
- `IMAGE_GEN_MODEL` - Model for image generation (default: `gpt-image-1.5`)
- `MCP_SERVER_URL` - MCP server URL (default: `http://127.0.0.1:8888/sse`)
- `ELEVEN_API_KEY` - ElevenLabs API key for voice generation
- `ELEVEN_VOICE_ID` - ElevenLabs voice ID
- `NUDGE_MINUTES` - Minutes of inactivity before nudge (default: 120)
- `NUDGE_ENABLED_CHATS` - Comma-separated chat IDs where nudge is enabled
- `FIRST_NUDGE_ENABLED` - Enable morning nudge 10:00-12:00 (default: false)
- `BOT_TIMEZONE` - Timezone for bot operations (default: `Europe/Riga`)
- `ACTIVE_START` / `ACTIVE_END` - Active hours for nudges (default: `10:00` to `21:00`)

**Never commit your `.env` files!** They are in `.gitignore`.

### 5. Create bot persona
Each bot needs a folder in `prompts/<bot_name>/` with:
- `system_prompt.yaml` - System instructions for the OpenAI agent
- `bot_prompts.yaml` - Nudge prompts, image prompts, name patterns

Copy from `prompts/default_bot/` as a starting point:
```sh
cp -r prompts/default_bot prompts/my_bot
```

### 6. Run the bot

**Single bot:**
```sh
./start.sh my_bot
```

**All configured bots + MCP server + autoupdate:**
```sh
./start.sh all
```

**MCP server only:**
```sh
./start.sh mcp
```

The script uses tmux when available (each process in its own session) or falls back to background processes with PID files.

**Stop all:**
```sh
./start.sh all stop
```

## Usage
- Add your bot to a Telegram group (make sure Group Privacy is disabled first)
- Mention the bot: `@my_bot What's the weather?`
- Send a photo and the bot will analyze it
- Use `/meme` to fetch a random meme
- Use `/fact` to get a random fact
- Use `/voice <text>` to generate a voice message
- Use `/potd` to get NASA's picture of the day

## Multi-Bot Setup

To run multiple personas in the same group:

1. Create a bot for each persona in @BotFather (repeat step 3 above)
2. Disable Group Privacy for each bot
3. Create `.env.<bot_name>` with unique `TELEGRAM_TOKEN` and matching `BOT_USERNAME`
4. Create `prompts/<bot_name>/` folder for each persona
5. Add all bots to your group
6. Run `./start.sh all`

Bots can nudge each other using `@mentions` in their nudge prompts.

## Notes
- The bot uses the OpenAI Agents SDK with MCP tools
- The `.env` files **must** contain valid API keys
- For production, use a process manager (systemd, supervisor) or the built-in tmux/PID support

## Security
- **Never share your `.env` files or API keys**
- All `.env*` files (except `.env.example`) are excluded from git
