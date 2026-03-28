import os
import sys
from datetime import datetime

import pytest
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, '..'))))

import claims
import main
import agent_client


@pytest.mark.asyncio
async def test_daily_nudge_claim_allows_only_one_bot(monkeypatch, tmp_path):
    claim_dir = str(tmp_path / 'claims')
    os.makedirs(claim_dir)
    monkeypatch.setattr(claims, 'CLAIM_DIR', claim_dir)

    now = datetime(2026, 3, 28, 10, 0, 0)
    assert claims.try_claim_nudge(100, 'bot_a', now=now) is True
    assert claims.try_claim_nudge(100, 'bot_b', now=now) is False


@pytest.mark.asyncio
async def test_nudge_loop_skips_when_claim_lost(monkeypatch, tmp_path):
    import nudge as nudge_module
    import state

    agent_client._histories.clear()
    main.last_activity_time.clear()
    main.last_bot_reply_time.clear()
    main.bot_unmentioned_count.clear()

    monkeypatch.setattr(main, 'NUDGE_ENABLED_CHATS', {100})
    monkeypatch.setattr(main, 'ACTIVE_START', __import__('datetime').time(0, 0))
    monkeypatch.setattr(main, 'ACTIVE_END', __import__('datetime').time(23, 59))
    monkeypatch.setattr(main.bot_bus, 'last_message_time', lambda cid: None)
    monkeypatch.setattr(main, 'get_nudge_minutes', lambda: main.NUDGE_MINUTES)
    monkeypatch.setattr(nudge_module, 'try_claim_nudge', lambda chat_id, bot_username: False)

    chat_id = 100
    past = main.datetime.now() - main.timedelta(minutes=main.NUDGE_MINUTES + 1)
    main.last_activity_time[chat_id] = past
    state.nudge_loop_started_at = past

    ask_mock = AsyncMock(return_value='nudge-msg')
    send_mock = AsyncMock()
    monkeypatch.setattr(main, 'ask_agent', ask_mock)
    monkeypatch.setattr(main, 'send_nudge_with_image', send_mock)

    calls = []
    async def fake_sleep(seconds):
        calls.append(seconds)
        if seconds == main.NUDGE_CHECK_INTERVAL and len(calls) >= 2:
            raise RuntimeError('stop')

    monkeypatch.setattr(nudge_module.asyncio, 'sleep', fake_sleep)

    with pytest.raises(RuntimeError):
        await main.nudge_inactive_chats()

    ask_mock.assert_not_awaited()
    send_mock.assert_not_awaited()
