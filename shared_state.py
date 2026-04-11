"""Shared state module for nudge timing across bot processes."""
import json
import os
from datetime import datetime
from pathlib import Path

SHARED_STATE_FILE = os.getenv('SHARED_STATE_FILE', '/tmp/telebot-nudge-state.json')

def load_shared_last_activity():
    """Load last_activity_time from shared file."""
    if not os.path.exists(SHARED_STATE_FILE):
        return {}
    try:
        with open(SHARED_STATE_FILE, 'r') as f:
            data = json.load(f)
        # Convert ISO strings back to datetime
        return {int(k): datetime.fromisoformat(v) for k, v in data.items()}
    except (json.JSONDecodeError, ValueError, OSError):
        return {}

def save_shared_last_activity(last_activity_time):
    """Save last_activity_time to shared file."""
    try:
        data = {str(k): v.isoformat() for k, v in last_activity_time.items()}
        with open(SHARED_STATE_FILE, 'w') as f:
            json.dump(data, f)
    except OSError:
        pass  # Ignore write errors

def load_shared_bot_unmentioned():
    """Load bot_unmentioned_count from shared file."""
    state_file = SHARED_STATE_FILE.replace('.json', '-unmentioned.json')
    if not os.path.exists(state_file):
        return {}
    try:
        with open(state_file, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

def save_shared_bot_unmentioned(bot_unmentioned_count):
    """Save bot_unmentioned_count to shared file."""
    state_file = SHARED_STATE_FILE.replace('.json', '-unmentioned.json')
    try:
        with open(state_file, 'w') as f:
            json.dump(bot_unmentioned_count, f)
    except OSError:
        pass
