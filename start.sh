#!/usr/bin/env bash
# Start or manage the shared MCP server and individual bot personas.
#
# Usage:
#   ./start.sh all        [start|stop|restart]   # MCP + all configured bots + autoupdate
#   ./start.sh mcp        [start|stop|restart]   # shared MCP server only
#   ./start.sh <bot>      [start|stop|restart]   # individual bot only
#   ./start.sh autoupdate                        # autoupdate watcher only
#
# Works with tmux (each process in its own session) or without (background
# processes tracked via PID files in .pids/).
#
# Examples:
#   ./start.sh all                         # start everything + autoupdate
#   ./start.sh all stop                    # stop everything
#   ./start.sh mcp                         # start shared MCP server
#   ./start.sh neuroyury_bot               # start neuroyury_bot
#   ./start.sh neuroman                    # start neuroman
#   ./start.sh neuroyury_bot stop          # stop neuroyury_bot

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="$SCRIPT_DIR/.pids"
USE_TMUX=false
command -v tmux >/dev/null && USE_TMUX=true

# --- Target name (required first argument) ---
if [[ -z "$1" || "$1" == -* ]]; then
    echo "Usage: $0 <all|mcp|bot_name|autoupdate> [start|stop|restart]" >&2
    echo "" >&2
    echo "Targets:" >&2
    echo "  all              — MCP + all configured bots + autoupdate" >&2
    echo "  mcp              — shared MCP server" >&2
    echo "  autoupdate       — watch git and restart all running sessions on changes" >&2
    echo "" >&2
    echo "Available bots:" >&2
    for d in prompts/*/; do
        [[ -d "$d" ]] && echo "  $(basename "$d")" >&2
    done
    exit 1
fi

TARGET="$1"
shift

# --- Process management helpers ---

# Recursively kill a process and all its descendants (depth-first).
kill_tree() {
    local pid="$1" sig="${2:-TERM}"
    local child
    for child in $(pgrep -P "$pid" 2>/dev/null); do
        kill_tree "$child" "$sig"
    done
    kill -"$sig" "$pid" 2>/dev/null || true
}

# Stop a named process (tmux session or PID file).
stop_session() {
    local name="$1"
    if [[ "$USE_TMUX" == true ]]; then
        if ! tmux has-session -t "$name" 2>/dev/null; then
            echo "No tmux session '$name' running."
            return
        fi
        echo "Stopping tmux session '$name'"
        local pane_pids
        pane_pids=$(tmux list-panes -t "$name" -F '#{pane_pid}' 2>/dev/null || true)
        tmux kill-session -t "$name" 2>/dev/null || true
        for pid in $pane_pids; do kill_tree "$pid"; done
        sleep 2
        for pid in $pane_pids; do kill_tree "$pid" 9; done
    else
        local pidfile="$PID_DIR/$name.pid"
        if [[ ! -f "$pidfile" ]]; then
            echo "No PID file for '$name'."
            return
        fi
        local pid
        pid=$(cat "$pidfile")
        echo "Stopping '$name' (pid $pid)"
        kill_tree "$pid"
        sleep 2
        kill_tree "$pid" 9
        rm -f "$pidfile"
    fi
}

# Start a named process. In tmux mode creates a session; otherwise
# runs in background and writes a PID file.
# Attaches to tmux only when NO_ATTACH is not set.
start_session() {
    local name="$1" run_cmd="$2"
    if [[ "$USE_TMUX" == true ]]; then
        echo "Starting '$name' (tmux)"
        tmux new-session -d -s "$name" "$run_cmd"
        if [[ "${NO_ATTACH:-}" != true ]]; then
            tmux attach-session -t "$name"
        fi
    else
        mkdir -p "$PID_DIR"
        echo "Starting '$name' (background)"
        $run_cmd >> "$PID_DIR/$name.log" 2>&1 &
        local pid=$!
        echo "$pid" > "$PID_DIR/$name.pid"
        echo "  pid $pid, log: $PID_DIR/$name.log"
    fi
}

# List all running telebot targets (session names or PID file basenames).
running_targets() {
    if [[ "$USE_TMUX" == true ]]; then
        tmux list-sessions -F '#{session_name}' 2>/dev/null | grep '^telebot-' || true
    else
        for pidfile in "$PID_DIR"/telebot-*.pid; do
            [[ -f "$pidfile" ]] || continue
            local name pid
            name=$(basename "$pidfile" .pid)
            pid=$(cat "$pidfile")
            # Only list if process is still alive
            if kill -0 "$pid" 2>/dev/null; then
                echo "$name"
            else
                rm -f "$pidfile"
            fi
        done
    fi
}

# Collect configured bot names: those that have both .env.<name> and prompts/<name>/
configured_bots() {
    for env_file in .env.*; do
        [[ -f "$env_file" ]] || continue
        local name="${env_file#.env.}"
        [[ "$name" == "example" ]] && continue
        [[ -d "prompts/$name" ]] && echo "$name"
    done
}

# ---------------------------------------------------------------
# all: start MCP + all configured bots + autoupdate
# ---------------------------------------------------------------
if [[ "$TARGET" == "all" ]]; then
    COMMAND="${1:-start}"

    if [[ "$COMMAND" == "stop" ]]; then
        sessions=$(running_targets)
        if [[ -z "$sessions" ]]; then
            echo "No telebot processes running."
        else
            for sess in $sessions; do
                stop_session "$sess"
            done
        fi
        exit 0
    fi

    if [[ "$COMMAND" == "restart" ]]; then
        sessions=$(running_targets)
        for sess in $sessions; do
            stop_session "$sess"
        done
    fi

    # Start MCP (detached / background)
    NO_ATTACH=true "$0" mcp start

    # Give MCP a moment to bind its port
    sleep 2

    # Start each configured bot (detached / background)
    bots=$(configured_bots)
    if [[ -z "$bots" ]]; then
        echo "Warning: no configured bots found (need .env.<bot_name> + prompts/<bot_name>/)" >&2
    else
        for bot in $bots; do
            NO_ATTACH=true "$0" "$bot" start
        done
    fi

    echo ""
    echo "Running:"
    running_targets | sed 's/^/  /'
    echo ""

    # Run autoupdate in foreground (Ctrl-C stops only the watcher;
    # tmux sessions / background processes keep running)
    exec "$0" autoupdate
fi

# ---------------------------------------------------------------
# autoupdate: watch git and restart all running telebot-* processes
# ---------------------------------------------------------------
if [[ "$TARGET" == "autoupdate" ]]; then
    INTERVAL=${INTERVAL:-900}

    echo "Auto-update watcher started (checking every ${INTERVAL}s)"
    while true; do
        git fetch origin
        LOCAL=$(git rev-parse HEAD)
        REMOTE=$(git rev-parse @{u})
        if [[ "$LOCAL" != "$REMOTE" ]]; then
            echo "$(date): Updates found. Pulling and restarting..."
            git pull --ff-only

            sessions=$(running_targets)
            if [[ -z "$sessions" ]]; then
                echo "$(date): No telebot processes running."
            else
                for sess in $sessions; do
                    stop_session "$sess"
                done

                for sess in $sessions; do
                    target="${sess#telebot-}"
                    NO_ATTACH=true "$0" "$target" start &
                done
                wait
                echo "$(date): All processes restarted."
            fi
        else
            echo "$(date): No updates."
        fi
        sleep "$INTERVAL"
    done
    exit 0
fi

# ---------------------------------------------------------------
# mcp / bot targets
# ---------------------------------------------------------------

if [[ "$TARGET" == "mcp" ]]; then
    ENV_FILE=".env"
    if [[ -f "$ENV_FILE" ]]; then
        echo "Loading environment from $ENV_FILE"
        set -a
        source "$ENV_FILE"
        set +a
    fi
    MCP_PORT="${MCP_PORT:-8888}"
    export MCP_PORT
    SESSION="telebot-mcp"
    RUN_CMD="uv run python mcp_server.py"
else
    BOT_NAME="$TARGET"

    if [[ ! -d "prompts/$BOT_NAME" ]]; then
        echo "Error: prompts/$BOT_NAME/ does not exist." >&2
        echo "" >&2
        echo "Available bots:" >&2
        for d in prompts/*/; do
            [[ -d "$d" ]] && echo "  $(basename "$d")" >&2
        done
        exit 1
    fi

    ENV_FILE=".env.$BOT_NAME"
    if [[ ! -f "$ENV_FILE" ]]; then
        ENV_FILE=".env"
    fi
    if [[ -f "$ENV_FILE" ]]; then
        echo "Loading environment from $ENV_FILE"
        set -a
        source "$ENV_FILE"
        set +a
    else
        echo "Warning: no .env file found for $BOT_NAME" >&2
    fi

    MCP_PORT="${MCP_PORT:-8888}"
    export MCP_SERVER_URL="${MCP_SERVER_URL:-http://127.0.0.1:$MCP_PORT/sse}"
    SESSION="telebot-$BOT_NAME"
    RUN_CMD="uv run python main.py"
fi

# --- Parse remaining arguments ---
COMMAND="start"
while [[ $# -gt 0 ]]; do
    case "$1" in
        start|stop|restart)
            COMMAND="$1"
            ;;
        *)
            echo "Usage: $0 <all|mcp|bot_name|autoupdate> [start|stop|restart]" >&2
            exit 1
            ;;
    esac
    shift
done

case "$COMMAND" in
    stop)
        stop_session "$SESSION"
        ;;
    restart)
        stop_session "$SESSION"
        start_session "$SESSION" "$RUN_CMD"
        ;;
    start)
        start_session "$SESSION" "$RUN_CMD"
        ;;
esac
