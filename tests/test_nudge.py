import sys
import os

os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test')
os.environ.setdefault('ELEVEN_API_KEY', 'sk-test')

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, '..'))))

import pytest
from unittest.mock import AsyncMock
import asyncio
from datetime import datetime
import main
import agent_client


class FakeUser:
    def __init__(self, username='tester', id=1):
        self.username = username
        self.id = id
        self.is_bot = False


class FakeChat:
    def __init__(self, id=100):
        self.id = id


class FakeMessage:
    _next_id = 1

    def __init__(self, text):
        self.text = text
        self.caption = None
        self.message_id = FakeMessage._next_id
        FakeMessage._next_id += 1
        self.date = datetime.now()
        self.from_user = FakeUser()
        self.chat = FakeChat()

    async def answer(self, text, parse_mode=None):
        pass


@pytest.mark.asyncio
async def test_nudge_inactive_chat(monkeypatch):
    agent_client._histories.clear()
    main.last_activity_time.clear()
    main.last_bot_reply_time.clear()
    main.bot_unmentioned_count.clear()

    monkeypatch.setattr(main, "NUDGE_ENABLED_CHATS", {100})
    chat_id = 100
    past = main.datetime.now() - main.timedelta(minutes=main.NUDGE_MINUTES + 1)
    main.last_activity_time[chat_id] = past
    main.nudge_loop_started_at = past

    ask_mock = AsyncMock(return_value='nudge-msg')
    send_mock = AsyncMock()
    monkeypatch.setattr(main, 'ask_agent', ask_mock)
    monkeypatch.setattr(main, 'send_nudge_with_image', send_mock)
    monkeypatch.setattr(main, 'is_active_hours', lambda: True)

    async def fake_sleep(seconds):
        raise StopIteration

    monkeypatch.setattr(main.asyncio, 'sleep', fake_sleep)

    with pytest.raises(RuntimeError):
        await main.nudge_inactive_chats()

    ask_mock.assert_awaited_once()
    send_mock.assert_awaited_once()


def test_get_random_nudge_prompt_no_immediate_repeat(monkeypatch):
    """Calling the helper repeatedly should not return the same prompt twice in a row."""
    monkeypatch.setattr(main, "NUDGE_SYSTEM_PROMPTS", ["A", "B", "C"])
    main.nudge_prompt_history = []

    results = [
        main.get_random_nudge_prompt()
        for _ in range(main.NUDGE_PROMPT_HISTORY_LEN * 2)
    ]

    for prev, curr in zip(results, results[1:]):
        assert prev != curr

    # History should reset at least once, allowing repeats but never consecutively
    assert len(set(results)) < len(results)


@pytest.mark.asyncio
async def test_manual_nudge_command_variants(monkeypatch):
    """The /nudge command should work with mentions and extra args."""
    agent_client._histories.clear()
    main.last_activity_time.clear()
    main.last_bot_reply_time.clear()
    main.bot_unmentioned_count.clear()

    nudge_mock = AsyncMock()
    monkeypatch.setattr(main, 'nudge_inactive_chats', nudge_mock)
    monkeypatch.setattr(main, 'try_claim_message', AsyncMock(return_value=True))

    msg1 = FakeMessage(f'/nudge@{main.BOT_USERNAME}')
    await main.handle_message(msg1)
    nudge_mock.assert_awaited_once_with(force=True, force_chat_id=msg1.chat.id, force_message=msg1)

    msg2 = FakeMessage('/nudge extra')
    await main.handle_message(msg2)
    assert nudge_mock.await_count == 2


@pytest.mark.asyncio
async def test_manual_nudge_works_when_disabled(monkeypatch):
    """/nudge should send a message even if the chat isn't nudge enabled."""
    agent_client._histories.clear()
    main.last_activity_time.clear()
    main.last_bot_reply_time.clear()
    main.bot_unmentioned_count.clear()

    monkeypatch.setattr(main, "NUDGE_ENABLED_CHATS", {123})
    ask_mock = AsyncMock(return_value="nudge-msg")
    send_mock = AsyncMock()
    monkeypatch.setattr(main, "ask_agent", ask_mock)
    monkeypatch.setattr(main, "send_nudge_with_image", send_mock)

    msg = FakeMessage("/nudge")
    msg.chat = FakeChat(id=999)

    await main.nudge_inactive_chats(force=True, force_chat_id=msg.chat.id, force_message=msg)

    ask_mock.assert_awaited_once()
    send_mock.assert_awaited_once()


def test_get_nudge_prompt_time_based(monkeypatch):
    """Return first nudge only during the morning window."""
    monkeypatch.setattr(main, "FIRST_NUDGE_PROMPT", "FIRST")
    monkeypatch.setattr(main, "FIRST_NUDGE_ENABLED", True)
    monkeypatch.setattr(main, "get_random_nudge_prompt", lambda: "RANDOM")

    class Morning(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2024, 1, 1, 10, 30, tzinfo=main.BOT_TIMEZONE)

    monkeypatch.setattr(main, "datetime", Morning)
    assert main.get_nudge_prompt(1) == "FIRST"

    class Afternoon(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2024, 1, 1, 12, 30, tzinfo=main.BOT_TIMEZONE)

    monkeypatch.setattr(main, "datetime", Afternoon)
    assert main.get_nudge_prompt(1) == "RANDOM"


@pytest.mark.asyncio
async def test_nudge_blocked_during_startup_grace(monkeypatch):
    """Nudge should not fire when nudge_loop_started_at is too recent."""
    agent_client._histories.clear()
    main.last_activity_time.clear()
    main.last_bot_reply_time.clear()
    main.bot_unmentioned_count.clear()

    monkeypatch.setattr(main, "NUDGE_ENABLED_CHATS", {100})
    chat_id = 100
    now = main.datetime.now()
    # Activity was long ago but startup was just now
    main.last_activity_time[chat_id] = now - main.timedelta(minutes=main.NUDGE_MINUTES + 10)
    main.nudge_loop_started_at = now  # just started

    ask_mock = AsyncMock(return_value='nudge-msg')
    send_mock = AsyncMock()
    monkeypatch.setattr(main, 'ask_agent', ask_mock)
    monkeypatch.setattr(main, 'send_nudge_with_image', send_mock)
    monkeypatch.setattr(main, 'is_active_hours', lambda: True)

    call_count = 0

    async def fake_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise StopIteration

    monkeypatch.setattr(main.asyncio, 'sleep', fake_sleep)

    with pytest.raises(RuntimeError):
        await main.nudge_inactive_chats()

    # Agent should NOT have been called — startup guard blocks it
    ask_mock.assert_not_awaited()
    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_nudge_allowed_after_startup_grace(monkeypatch):
    """Nudge should fire once startup grace period has elapsed."""
    agent_client._histories.clear()
    main.last_activity_time.clear()
    main.last_bot_reply_time.clear()
    main.bot_unmentioned_count.clear()

    monkeypatch.setattr(main, "NUDGE_ENABLED_CHATS", {100})
    chat_id = 100
    past = main.datetime.now() - main.timedelta(minutes=main.NUDGE_MINUTES + 1)
    main.last_activity_time[chat_id] = past
    main.nudge_loop_started_at = past  # started long ago

    ask_mock = AsyncMock(return_value='nudge-msg')
    send_mock = AsyncMock()
    monkeypatch.setattr(main, 'ask_agent', ask_mock)
    monkeypatch.setattr(main, 'send_nudge_with_image', send_mock)
    monkeypatch.setattr(main, 'is_active_hours', lambda: True)

    async def fake_sleep(seconds):
        raise StopIteration

    monkeypatch.setattr(main.asyncio, 'sleep', fake_sleep)

    with pytest.raises(RuntimeError):
        await main.nudge_inactive_chats()

    ask_mock.assert_awaited_once()
    send_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_nudge_enabled_chats_always_in_all_chats(monkeypatch):
    """NUDGE_ENABLED_CHATS should be part of all_chats even with no history."""
    agent_client._histories.clear()
    main.last_activity_time.clear()

    monkeypatch.setattr(main, "NUDGE_ENABLED_CHATS", {200, 300})

    # Verify the set union includes NUDGE_ENABLED_CHATS
    all_chats = set(agent_client._histories.keys()) | set(main.last_activity_time.keys()) | main.NUDGE_ENABLED_CHATS
    assert 200 in all_chats
    assert 300 in all_chats
