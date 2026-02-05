import sys
import os

os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test')
os.environ.setdefault('BOT_USERNAME', 'testbot')
os.environ.setdefault('ELEVEN_API_KEY', 'sk-test')

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, '..'))))

import pytest
from unittest.mock import AsyncMock
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
        self.replies = []
        self.voice_replies = []

    async def answer(self, text, parse_mode=None):
        self.replies.append(text)

    async def answer_voice(self, voice):
        self.voice_replies.append(voice)


@pytest.mark.asyncio
async def test_direct_mention(monkeypatch):
    agent_client._histories.clear()
    main.bot_unmentioned_count.clear()
    main.last_activity_time.clear()
    main.last_bot_reply_time.clear()
    fake_msg = FakeMessage(f'@{main.BOT_USERNAME} hi')
    ask_mock = AsyncMock(return_value='test-reply')
    monkeypatch.setattr(main, 'ask_agent', ask_mock)

    await main.handle_message(fake_msg)

    assert ask_mock.await_count == 1
    assert fake_msg.replies == ['test-reply']


@pytest.mark.asyncio
async def test_unmentioned_reply(monkeypatch):
    agent_client._histories.clear()
    main.bot_unmentioned_count.clear()
    main.last_activity_time.clear()
    main.last_bot_reply_time.clear()
    main.messages_since_bot_reply.clear()
    fake_msg = FakeMessage('hello')
    ask_mock = AsyncMock(return_value='test-reply')
    monkeypatch.setattr(main, 'ask_agent', ask_mock)
    monkeypatch.setattr(main.random, 'random', lambda: 1.0)
    chat_id = fake_msg.chat.id
    # 4+ messages since last bot reply triggers always-respond
    main.messages_since_bot_reply[chat_id] = 4

    await main.handle_message(fake_msg)

    assert ask_mock.await_count == 1
    assert fake_msg.replies == ['test-reply']


@pytest.mark.asyncio
async def test_unmentioned_no_reply_due_to_probability(monkeypatch):
    agent_client._histories.clear()
    main.bot_unmentioned_count.clear()
    main.last_activity_time.clear()
    main.last_bot_reply_time.clear()
    main.messages_since_bot_reply.clear()
    fake_msg = FakeMessage('ping')
    ask_mock = AsyncMock(return_value='will-not-be-used')
    monkeypatch.setattr(main, 'ask_agent', ask_mock)
    monkeypatch.setattr(main.random, 'random', lambda: 0.9)

    await main.handle_message(fake_msg)

    assert ask_mock.await_count == 0
    assert fake_msg.replies == []
    chat_id = fake_msg.chat.id
    assert main.bot_unmentioned_count.get(chat_id, 0) == 0


@pytest.mark.asyncio
async def test_unmentioned_limit_reached(monkeypatch):
    agent_client._histories.clear()
    main.bot_unmentioned_count.clear()
    main.last_activity_time.clear()
    main.last_bot_reply_time.clear()
    main.messages_since_bot_reply.clear()
    fake_msg = FakeMessage('again')
    ask_mock = AsyncMock(return_value='should-not-reply')
    monkeypatch.setattr(main, 'ask_agent', ask_mock)
    monkeypatch.setattr(main.random, 'random', lambda: 1.0)
    chat_id = fake_msg.chat.id
    # 4+ messages since last bot reply so probability would be 1.0
    main.messages_since_bot_reply[chat_id] = 4
    main.bot_unmentioned_count[chat_id] = main.MAX_UNMENTIONED_REPLIES

    await main.handle_message(fake_msg)

    assert ask_mock.await_count == 0
    assert fake_msg.replies == []
    assert main.bot_unmentioned_count[chat_id] == main.MAX_UNMENTIONED_REPLIES


@pytest.mark.asyncio
async def test_recent_bot_activity_overrides_probability(monkeypatch):
    agent_client._histories.clear()
    main.bot_unmentioned_count.clear()
    main.last_activity_time.clear()
    main.last_bot_reply_time.clear()
    fake_msg = FakeMessage('hey')
    ask_mock = AsyncMock(return_value='new-reply')
    monkeypatch.setattr(main, 'ask_agent', ask_mock)
    monkeypatch.setattr(main.random, 'random', lambda: 0.9)
    chat_id = fake_msg.chat.id
    from datetime import datetime as real_datetime
    base_time = real_datetime(2024, 1, 1, 12, 0, 0)

    class FixedDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return base_time

    monkeypatch.setattr(main, 'datetime', FixedDatetime)
    # Bot replied 10 seconds ago — within RECENT_ACTIVITY_SECONDS window
    main.last_bot_reply_time[chat_id] = base_time - main.timedelta(seconds=10)

    await main.handle_message(fake_msg)

    assert ask_mock.await_count == 1
    assert fake_msg.replies == ['new-reply']
    assert main.bot_unmentioned_count[chat_id] == 1


@pytest.mark.asyncio
async def test_direct_mention_resets_counter(monkeypatch):
    agent_client._histories.clear()
    main.bot_unmentioned_count.clear()
    main.last_activity_time.clear()
    main.last_bot_reply_time.clear()
    fake_msg = FakeMessage(f'@{main.BOT_USERNAME} yo')
    ask_mock = AsyncMock(return_value='ok')
    monkeypatch.setattr(main, 'ask_agent', ask_mock)
    chat_id = fake_msg.chat.id
    main.bot_unmentioned_count[chat_id] = 2

    await main.handle_message(fake_msg)

    assert ask_mock.await_count == 1
    assert fake_msg.replies == ['ok']
    assert main.bot_unmentioned_count[chat_id] == 0


@pytest.mark.asyncio
async def test_voice_command(monkeypatch, tmp_path):
    agent_client._histories.clear()
    main.bot_unmentioned_count.clear()
    main.last_activity_time.clear()
    main.last_bot_reply_time.clear()
    msg = FakeMessage('/voice hello world')
    monkeypatch.setattr(main, 'try_claim_message', AsyncMock(return_value=True))
    voice_path = tmp_path / 'v.ogg'
    voice_path.write_text('x')
    gen_mock = AsyncMock(return_value=str(voice_path))
    monkeypatch.setattr(main, 'generate_voice_file', gen_mock)
    removed = []
    monkeypatch.setattr(main.os, 'remove', lambda p: removed.append(p))

    await main.handle_message(msg)

    gen_mock.assert_awaited_once_with('hello world')
    assert len(msg.voice_replies) == 1
    voice_file = msg.voice_replies[0]
    assert isinstance(voice_file, main.FSInputFile)
    assert voice_file.path == str(voice_path)
    assert removed == [str(voice_path)]


@pytest.mark.asyncio
async def test_voice_command_usage(monkeypatch):
    agent_client._histories.clear()
    main.bot_unmentioned_count.clear()
    main.last_activity_time.clear()
    main.last_bot_reply_time.clear()
    msg = FakeMessage('/voice')
    monkeypatch.setattr(main, 'try_claim_message', AsyncMock(return_value=True))
    gen_mock = AsyncMock()
    monkeypatch.setattr(main, 'generate_voice_file', gen_mock)

    await main.handle_message(msg)

    gen_mock.assert_not_called()
    assert msg.replies == ['Usage: /voice <text>']
    assert msg.voice_replies == []


@pytest.mark.asyncio
@pytest.mark.skip(reason="voice functionality currently disabled")
async def test_voice_in_assistant_reply(monkeypatch, tmp_path):
    agent_client._histories.clear()
    main.bot_unmentioned_count.clear()
    main.last_activity_time.clear()
    main.last_bot_reply_time.clear()
    msg = FakeMessage(f'@{main.BOT_USERNAME} hi')
    voice_path = tmp_path / 'a.mp3'
    voice_path.write_text('x')
    ask_mock = AsyncMock(return_value=f"Take it {voice_path}")
    monkeypatch.setattr(main, 'ask_openai', ask_mock)
    remove_list = []
    monkeypatch.setattr(main.os, 'remove', lambda p: remove_list.append(p))

    await main.handle_message(msg)

    assert ask_mock.await_count == 1
    assert len(msg.voice_replies) == 1
    assert msg.voice_replies[0].path == str(voice_path)
    assert msg.replies == ['Take it']
    assert remove_list == [str(voice_path)]


@pytest.mark.asyncio
async def test_voice_trigger_prefix(monkeypatch):
    agent_client._histories.clear()
    main.bot_unmentioned_count.clear()
    main.last_activity_time.clear()
    main.last_bot_reply_time.clear()
    msg = FakeMessage(f'@{main.BOT_USERNAME} голосом привет')
    ask_mock = AsyncMock(return_value='hi')
    monkeypatch.setattr(main, 'ask_openai', ask_mock)

    await main.handle_message(msg)

    assert ask_mock.await_count == 1
    assert ask_mock.await_args.kwargs['tool_choice'] == 'generate_voice'
