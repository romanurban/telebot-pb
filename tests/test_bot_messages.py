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
    monkeypatch.setattr(bm, 'REPLY_DELAY', (0, 0))
    # deterministic chains in tests: always the longest cap, never drop
    monkeypatch.setattr(bm.random, 'randint', lambda a, b: b)
    monkeypatch.setattr(bm, 'DROP_CHANCE', 0.0)
    bm.reset_state()
    yield
    bm.reset_state()


def _msg(text='hi', sender='otherbot', msg_id=1, reply_to=None, chat_id=100, is_bot=True):
    m = SimpleNamespace(
        text=text, caption=None, message_id=msg_id,
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(username=sender, id=7, is_bot=is_bot),
        reply_to_message=reply_to,
        date=__import__('datetime').datetime(2026, 10, 19, 12, 0, msg_id % 60),
    )
    m.reply = AsyncMock()
    m.answer = AsyncMock()
    return m


def _ctx(**kw):
    ctx = SimpleNamespace(
        bot_username='mybot', name_mention_re=re.compile('макс'),
        chat_react_prompt='react', image_default_prompt='img',
        last_activity_time={}, messages_since_bot_reply={}, bot_unmentioned_count={},
        last_bot_reply_time={},
        ask_openai=AsyncMock(return_value='addressed answer'),
        ask_agent=AsyncMock(return_value='react answer'),
        clean_openai_reply=lambda x: x,
        mark_bot_replied=lambda chat_id: bm.count_bot_message(chat_id),
        bot=None,
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
    for i in range(bm.MAX_CHAIN - 2):
        bm.count_bot_message(100, now=now + i)
    assert bm.may_react(_msg(msg_id=9), 'mybot', addressed=True, now=now + 10)       # (MAX-1)th: last answer allowed
    assert bm.is_last_in_chain(100)                                                    # ...and it must close the thread
    assert not bm.may_react(_msg(msg_id=10), 'mybot', addressed=True, now=now + 20)  # MAX-th -> cut
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
async def test_handle_text_routes_bot_message():
    ctx = _ctx()
    m = _msg(text='@mybot как дела?')
    await handle_text_message(m, ctx)
    ctx.ask_agent.assert_awaited_once()
    m.reply.assert_awaited_once()          # reply-quote, so the other bot receives it
    assert m.reply.await_args.args[0] == 'react answer'



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
    ctx = _ctx(ask_agent=AsyncMock(return_value=''))
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


def test_opener_claim_is_content_based_across_bots():
    # basic groups: each bot sees a different message_id for the same message
    a = _msg(text='Кот насрал в тапки.', msg_id=5)
    b = _msg(text='Кот насрал в тапки.', msg_id=5)
    b.message_id = 999
    assert bm.may_react(a, 'bot_a', addressed=False, now=1000)
    bm.reset_state()
    assert not bm.may_react(b, 'bot_b', addressed=False, now=1000)


def test_clean_self_mention_leaves_no_leading_comma():
    from message_filter import clean_self_mentions
    assert clean_self_mentions('@neuroyury_bot, ты тапки уже догнал?', 'neuroyury_bot') == 'ты тапки уже догнал?'
    assert clean_self_mentions('Ну @neuroyury_bot , ладно', 'neuroyury_bot') == 'Ну, ладно'


@pytest.mark.asyncio
async def test_closing_hint_on_last_reply_and_not_before():
    ctx = _ctx()
    await bm.handle_bot_text(_msg(text='@mybot привет', msg_id=1), ctx)
    first_hint = ctx.ask_agent.await_args.args[0][1]['content']
    assert bm.CLOSING_HINT not in first_hint and bm.NATURAL_HINT in first_hint
    bm.reset_state()
    for i in range(bm.MAX_CHAIN - 2):
        bm.count_bot_message(100)
    ctx = _ctx()
    await bm.handle_bot_text(_msg(text='@mybot ну что?', msg_id=2), ctx)
    assert bm.CLOSING_HINT in ctx.ask_agent.await_args.args[0][1]['content']


@pytest.mark.asyncio
async def test_reply_drops_redundant_mention_of_quoted_bot():
    ctx = _ctx(ask_agent=AsyncMock(return_value='@otherbot, в восемь у входа.'))
    m = _msg(text='@mybot когда?')
    await bm.handle_bot_text(m, ctx)
    assert m.reply.await_args.args[0] == 'в восемь у входа.'


@pytest.mark.asyncio
async def test_typing_shown_while_pausing(monkeypatch):
    monkeypatch.setattr(bm, 'REPLY_DELAY', (0.05, 0.05))
    bot = SimpleNamespace(send_chat_action=AsyncMock())
    ctx = _ctx(bot=bot)
    await bm.handle_bot_text(_msg(text='@mybot привет'), ctx)
    bot.send_chat_action.assert_awaited_with(100, 'typing')


def test_thin_emoji():
    from message_filter import thin_emoji
    assert thin_emoji('Кот смотрит сурово. 😐', rand=lambda: 0.9) == 'Кот смотрит сурово.'
    assert thin_emoji('Кот смотрит сурово. 😐', rand=lambda: 0.1) == 'Кот смотрит сурово. 😐'
    assert thin_emoji('Без эмодзи', rand=lambda: 0.9) == 'Без эмодзи'
    assert thin_emoji('😐', rand=lambda: 0.9) == '😐'  # never empties a message


def test_random_chain_cap(monkeypatch):
    monkeypatch.setattr(bm.random, 'randint', lambda a, b: a)   # shortest cap: opener + one reaction
    assert bm.may_react(_msg(text='@mybot привет', msg_id=1), 'mybot', addressed=True, now=1000)
    assert bm.chain_cap(100) == bm.MIN_CHAIN
    assert bm.is_last_in_chain(100)                               # that one reaction must close
    assert not bm.may_react(_msg(text='@mybot а?', msg_id=2), 'mybot', addressed=True, now=1010)


def test_cap_is_rerolled_for_each_new_chain(monkeypatch):
    caps = iter([2, 5])
    monkeypatch.setattr(bm.random, 'randint', lambda a, b: next(caps))
    bm.count_bot_message(100, now=1000)
    assert bm.chain_cap(100) == 2
    bm.reset_chain(100)                       # a person writes
    bm.count_bot_message(100, now=1010)
    assert bm.chain_cap(100) == 5


def test_addressed_bot_sometimes_lets_thread_end(monkeypatch):
    monkeypatch.setattr(bm, 'DROP_CHANCE', 1.0)
    bm.count_bot_message(100, now=1000)
    bm.count_bot_message(100, now=1001)
    assert not bm.may_react(_msg(text='@mybot ну?', msg_id=3), 'mybot', addressed=True, now=1002)
    bm.reset_state()
    # ...but never on the first exchange
    assert bm.may_react(_msg(text='@mybot ну?', msg_id=4), 'mybot', addressed=True, now=2000)
