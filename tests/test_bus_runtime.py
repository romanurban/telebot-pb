import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, '..'))))

import bus_runtime
import bot_bus
import pytest


def test_initialize_bus_positions_seeks_to_end(monkeypatch, tmp_path):
    bus_dir = tmp_path / 'bus'
    bus_dir.mkdir()
    p1 = bus_dir / '100.jsonl'
    p1.write_text('{"bot":"a","text":"x"}\n', encoding='utf-8')
    p2 = bus_dir / 'bad.txt'
    p2.write_text('ignore', encoding='utf-8')

    monkeypatch.setattr(bot_bus, 'BOT_BUS_DIR', str(bus_dir))
    positions = {}

    bus_runtime.initialize_bus_positions(positions)

    assert positions[100] == p1.stat().st_size
    assert len(positions) == 1


async def _cancel_sleep(_seconds):
    raise RuntimeError('stop')


@pytest.mark.asyncio
async def test_poll_bot_bus_replies_on_bot_mention(monkeypatch):
    fake_bot = AsyncMock()
    monkeypatch.setattr(bus_runtime.os, 'listdir', lambda _p: ['100.jsonl'])
    monkeypatch.setattr(bot_bus, 'BOT_BUS_DIR', '/tmp/fake-bus')
    monkeypatch.setattr(bot_bus, 'poll', lambda chat_id, bot_username, last_pos: ([{'bot': 'otherbot', 'text': '@testbot ping'}], 42))
    monkeypatch.setattr(bot_bus, 'broadcast', lambda *args, **kwargs: None)
    monkeypatch.setattr(bus_runtime.asyncio, 'sleep', _cancel_sleep)

    injected = []
    async def ask_openai(prompt, username, chat_id):
        return 'pong'

    marks = []
    def mark_bot_replied(chat_id):
        marks.append(chat_id)

    last_activity_time = {}
    bus_positions = {}
    bus_last_reply = {}

    with pytest.raises(RuntimeError):
        await bus_runtime.poll_bot_bus(
            bot=fake_bot,
            bot_username='testbot',
            bus_positions=bus_positions,
            bus_last_reply=bus_last_reply,
            last_activity_time=last_activity_time,
            name_mention_re=None,
            inject_external_message=lambda chat_id, other_bot, text: injected.append((chat_id, other_bot, text)),
            ask_openai=ask_openai,
            ask_agent=AsyncMock(),
            clean_openai_reply=lambda x: x,
            mark_bot_replied=mark_bot_replied,
            parse_mode='HTML',
        )

    assert injected == [(100, 'otherbot', '@testbot ping')]
    fake_bot.send_message.assert_awaited_once()
    assert marks == [100]
    assert bus_positions[100] == 42


@pytest.mark.asyncio
async def test_poll_bot_bus_can_probabilistically_reply_without_mention(monkeypatch):
    fake_bot = AsyncMock()
    monkeypatch.setattr(bus_runtime.os, 'listdir', lambda _p: ['100.jsonl'])
    monkeypatch.setattr(bot_bus, 'BOT_BUS_DIR', '/tmp/fake-bus')
    monkeypatch.setattr(bot_bus, 'poll', lambda chat_id, bot_username, last_pos: ([{'bot': 'otherbot', 'text': 'plain bus message'}], 42))
    monkeypatch.setattr(bot_bus, 'broadcast', lambda *args, **kwargs: None)
    monkeypatch.setattr(bus_runtime.asyncio, 'sleep', _cancel_sleep)
    monkeypatch.setattr(bus_runtime.random, 'random', lambda: 0.0)

    marks = []
    def mark_bot_replied(chat_id):
        marks.append(chat_id)

    with pytest.raises(RuntimeError):
        await bus_runtime.poll_bot_bus(
            bot=fake_bot,
            bot_username='testbot',
            bus_positions={},
            bus_last_reply={},
            last_activity_time={},
            name_mention_re=None,
            inject_external_message=lambda chat_id, other_bot, text: None,
            ask_openai=AsyncMock(),
            ask_agent=AsyncMock(return_value='bus-react'),
            clean_openai_reply=lambda x: x,
            mark_bot_replied=mark_bot_replied,
            parse_mode='HTML',
            messages_since_bot_reply={},
            bot_unmentioned_count={},
            last_bot_reply_time={},
            max_unmentioned_replies=3,
            recent_activity_seconds=30,
            chat_react_prompt='react',
        )

    fake_bot.send_message.assert_awaited_once()
    assert marks == [100]


@pytest.mark.asyncio
async def test_poll_bot_bus_skips_via_bus_non_mentions(monkeypatch):
    fake_bot = AsyncMock()
    monkeypatch.setattr(bus_runtime.os, 'listdir', lambda _p: ['100.jsonl'])
    monkeypatch.setattr(bot_bus, 'BOT_BUS_DIR', '/tmp/fake-bus')
    monkeypatch.setattr(bot_bus, 'poll', lambda chat_id, bot_username, last_pos: ([{'bot': 'otherbot', 'text': 'plain bus message', 'via_bus': True}], 42))
    monkeypatch.setattr(bus_runtime.asyncio, 'sleep', _cancel_sleep)

    with pytest.raises(RuntimeError):
        await bus_runtime.poll_bot_bus(
            bot=fake_bot,
            bot_username='testbot',
            bus_positions={},
            bus_last_reply={},
            last_activity_time={},
            name_mention_re=None,
            inject_external_message=lambda chat_id, other_bot, text: None,
            ask_openai=AsyncMock(),
            ask_agent=AsyncMock(return_value='bus-react'),
            clean_openai_reply=lambda x: x,
            mark_bot_replied=lambda chat_id: None,
            parse_mode='HTML',
            messages_since_bot_reply={},
            bot_unmentioned_count={},
            last_bot_reply_time={},
            max_unmentioned_replies=3,
            recent_activity_seconds=30,
            chat_react_prompt='react',
        )

    fake_bot.send_message.assert_not_awaited()


def test_initialize_bus_positions_partial_line(monkeypatch, tmp_path):
    """Position should align to end of last complete line, ignoring partial writes."""
    bus_dir = tmp_path / 'bus'
    bus_dir.mkdir()
    p = bus_dir / '100.jsonl'
    complete_line = '{"bot":"a","text":"x"}\n'
    partial_line = '{"bot":"b","text":"incomp'
    p.write_text(complete_line + partial_line, encoding='utf-8')

    monkeypatch.setattr(bot_bus, 'BOT_BUS_DIR', str(bus_dir))
    positions = {}

    bus_runtime.initialize_bus_positions(positions)

    # Should point to end of the complete line, not the partial one
    assert positions[100] == len(complete_line.encode('utf-8'))


def test_initialize_bus_positions_complete_file(monkeypatch, tmp_path):
    """Position should equal file size when file ends with newline."""
    bus_dir = tmp_path / 'bus'
    bus_dir.mkdir()
    p = bus_dir / '200.jsonl'
    content = '{"bot":"a","text":"x"}\n{"bot":"b","text":"y"}\n'
    p.write_text(content, encoding='utf-8')

    monkeypatch.setattr(bot_bus, 'BOT_BUS_DIR', str(bus_dir))
    positions = {}

    bus_runtime.initialize_bus_positions(positions)

    assert positions[200] == p.stat().st_size


def test_line_aligned_size_empty_file(tmp_path):
    """Empty file should return position 0."""
    p = tmp_path / 'empty.jsonl'
    p.write_text('', encoding='utf-8')
    assert bus_runtime._line_aligned_size(str(p)) == 0
