import sys
import os

# Setup environment variables before importing main
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test')
os.environ.setdefault('ELEVEN_API_KEY', 'sk-test')

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, '..'))))

import pytest
from datetime import datetime as real_datetime

import main


@pytest.mark.asyncio
async def test_mark_bot_replied_updates_times(monkeypatch):
    main.last_activity_time.clear()
    main.last_bot_reply_time.clear()

    fixed_time = real_datetime(2024, 1, 1, 12, 0, 0)

    class FixedDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_time

    monkeypatch.setattr(main, 'datetime', FixedDatetime)

    chat_id = 42
    main.mark_bot_replied(chat_id)

    assert main.last_activity_time[chat_id] == fixed_time
    assert main.last_bot_reply_time[chat_id] == fixed_time


@pytest.mark.asyncio
async def test_mark_bot_replied_does_not_exist_before_call():
    main.last_activity_time.clear()
    main.last_bot_reply_time.clear()
    chat_id = 99
    assert chat_id not in main.last_bot_reply_time
    main.mark_bot_replied(chat_id)
    assert chat_id in main.last_bot_reply_time
    assert chat_id in main.last_activity_time
