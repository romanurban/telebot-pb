#!/usr/bin/env bash
# Periodically pull updates from the git repository and restart the bot
# and MCP server when new commits are detected.

set -e

# Interval in seconds between checks. Override with INTERVAL env var.
# Default is 15 minutes.
INTERVAL=${INTERVAL:-900}

while true; do
    git fetch origin
    LOCAL=$(git rev-parse HEAD)
    REMOTE=$(git rev-parse @{u})
    if [[ "$LOCAL" != "$REMOTE" ]]; then
        echo "$(date): Updates found. Pulling and restarting..."
        git pull --ff-only
        ./start.sh restart
    else
        echo "$(date): No updates found."
    fi
    sleep "$INTERVAL"
done
