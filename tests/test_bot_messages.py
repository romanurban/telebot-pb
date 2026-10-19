import os
import sys
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, '..'))))

import pytest
import claims
from handlers import bot_messages as bm
from handlers.messages import handle_text_message


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(claims, 'CLAIM_DIR', str(tmp_path))
    bm.reset_state()
    yield
    bm.reset_state()


def _msg(text='hi', sender='otherbot', msg_id=1, reply_to=None, chat_id=100, is_bot=True):
    m = SimpleNamespace(
        text=text, caption=None, message_id=msg_id,
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(username=sender, id=7, is_bot=is_bot),
        reply_to_message=reply_to,
    )
    m.reply = AsyncMock()
    m.answer = AsyncMock()
    return m


def _ctx(native=True, **kw):
    ctx = SimpleNamespace(
        bot_username='mybot', name_mention_re=re.compile('макс'), native_bot_to_bot=native,
        chat_react_prompt='react', image_default_prompt='img',
        last_activity_time={}, messages_since_bot_reply={}, bot_unmentioned_count={},
        last_bot_reply_time={}, bus_last_reply={},
        ask_openai=AsyncMock(return_value='addressed answer'),
        ask_agent=AsyncMock(return_value='react answer'),
        clean_openai_reply=lambda x: x,
        mark_bot_replied=lambda chat_id: bm.count_bot_message(chat_id),
    )
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


def test_is_addressed():
    reply_to_me = SimpleNamespace(from_user=SimpleNamespace(username='MyBot'))
    assert bm.is_addressed(_msg(reply_to=reply_to_me), 'mybot')
    assert bm.is_addressed(_msg(text='эй @mybot'), 'mybot')
    assert bm.is_addressed(_msg(text='Макс, привет'), 'mybot', re.compile('макс', re.I))
    assert not bm.is_addressed(_msg(text='просто текст'), 'mybot')


def test_unaddressed_opener_only_one_bot_reacts():
    m = _msg(msg_id=55)
    assert bm.may_react(m, 'bot_a', addressed=False, now=1000)
    bm.reset_state()  # second bot process has its own counters
    assert not bm.may_react(m, 'bot_b', addressed=False, now=1000)  # claim already taken


def test_unaddressed_mid_chain_ignored():
    bm.count_bot_message(100, now=1000)
    assert not bm.may_react(_msg(msg_id=2), 'mybot', addressed=False, now=1001)


def test_chain_is_cut_after_max_and_stays_quiet():
    now = 1000.0
    for i in range(bm.MAX_CHAIN - 1):
        bm.count_bot_message(100, now=now + i)
    assert bm.may_react(_msg(msg_id=9), 'mybot', addressed=True, now=now + 10)       # 6th message
    assert not bm.may_react(_msg(msg_id=10), 'mybot', addressed=True, now=now + 20)  # 7th -> cut
    assert not bm.may_react(_msg(msg_id=11), 'mybot', addressed=True, now=now + 60)  # still blocked
    bm.reset_chain(100)                                                               # human writes
    assert bm.may_react(_msg(msg_id=12), 'mybot', addressed=True, now=now + 70)


def test_stale_chain_resets_for_new_nudge():
    for i in range(bm.MAX_CHAIN + 2):
        bm.count_bot_message(100, now=1000 + i)
    later = 1000 + bm.CHAIN_COOLDOWN * 2 + 10
    assert bm.may_react(_msg(msg_id=77), 'mybot', addressed=False, now=later)


def test_min_reply_gap():
    bm._last_b2b_reply[100] = 1000
    assert not bm.may_react(_msg(), 'mybot', addressed=True, now=1000 + bm.MIN_REPLY_GAP - 1)


@pytest.mark.asyncio
async def test_handle_text_routes_bot_message_when_native():
    ctx = _ctx(native=True)
    m = _msg(text='@mybot как дела?')
    await handle_text_message(m, ctx)
    ctx.ask_openai.assert_awaited_once()
    m.reply.assert_awaited_once()          # reply-quote, so the other bot receives it
    assert m.reply.await_args.args[0] == 'addressed answer'


@pytest.mark.asyncio
async def test_handle_text_ignores_bots_when_not_native():
    ctx = _ctx(native=False)
    m = _msg(text='@mybot как дела?')
    await handle_text_message(m, ctx)
    ctx.ask_openai.assert_not_awaited()
    m.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_not_reacting_still_remembers(monkeypatch):
    injected = []
    monkeypatch.setattr(bm.agent_client, 'inject_external_message', lambda *a: injected.append(a))
    bm.count_bot_message(100)  # mid-chain, unaddressed -> no reaction
    ctx = _ctx()
    m = _msg(text='что-то своё', msg_id=3)
    await bm.handle_bot_text(m, ctx)
    m.reply.assert_not_awaited()
    assert injected == [(100, 'otherbot', 'что-то своё')]


@pytest.mark.asyncio
async def test_failed_llm_stays_silent():
    ctx = _ctx(ask_openai=AsyncMock(return_value=''))
    m = _msg(text='@mybot ?')
    await bm.handle_bot_text(m, ctx)
    m.reply.assert_not_awaited()


def test_opener_addressed_to_another_bot_is_left_to_it():
    # nudge "@neuroyury_bot, пойдёшь на квиз?" must not pull in a third bot
    m = _msg(text='@neuroyury_bot, пойдёшь на квиз сегодня?', msg_id=90)
    assert not bm.may_react(m, 'neuromans_bot', addressed=False, now=1000)
    bm.reset_state()
    other = SimpleNamespace(from_user=SimpleNamespace(username='neuroyury_bot'))
    assert not bm.may_react(_msg(text='ага', msg_id=91, reply_to=other), 'neuromans_bot', addressed=False, now=1000)


def test_addressed_elsewhere():
    assert bm.addressed_elsewhere(_msg(text='@otherbot привет'), 'mybot')
    assert not bm.addressed_elsewhere(_msg(text='@mybot привет'), 'mybot')
    assert not bm.addressed_elsewhere(_msg(text='всем привет'), 'mybot')
