import os
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, '..'))))

import bus_runtime
import bot_bus


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


import pytest


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
            mark_bot_replied=mark_bot_replied,
            parse_mode='HTML',
        )

    assert injected == [(100, 'otherbot', '@testbot ping')]
    fake_bot.send_message.assert_awaited_once()
    assert marks == [100]
    assert bus_positions[100] == 42
