import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, '..'))))

from bot_context import BotContext
from handlers.messages import handle_text_message


class FakeUser:
    def __init__(self, username='tester', user_id=1):
        self.username = username
        self.id = user_id
        self.is_bot = False


class FakeChat:
    def __init__(self, chat_id=100):
        self.id = chat_id


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.caption = None
        self.message_id = 1
        self.date = datetime.now()
        self.from_user = FakeUser()
        self.chat = FakeChat()
        self.replies = []
        self.voice_replies = []

    async def answer(self, text, parse_mode=None):
        self.replies.append(text)

    async def answer_voice(self, voice):
        self.voice_replies.append(voice)

    async def answer_photo(self, photo, caption=None):
        self.replies.append(caption or '<photo>')


def _make_ctx(**overrides):
    marks = overrides.pop('_marks', [])
    defaults = dict(
        bot_username='testbot',
        name_mention_re=None,
        image_default_prompt='',
        chat_react_prompt='react',
        max_unmentioned_replies=3,
        recent_activity_seconds=30,
        try_claim_message=AsyncMock(return_value=True),
        nudge_inactive_chats=AsyncMock(),
        get_picture_of_the_day=AsyncMock(),
        style_caption=AsyncMock(side_effect=lambda text, chat_id=None: text),
        retrieve_joke=AsyncMock(),
        retrieve_fact=AsyncMock(),
        generate_voice_file=AsyncMock(),
        ask_openai=AsyncMock(return_value='test-reply'),
        ask_agent=AsyncMock(return_value='unused'),
        clean_openai_reply=lambda x: x,
        mark_bot_replied=lambda chat_id: marks.append(chat_id),
        extract_voice_file=AsyncMock(return_value=None),
        extract_json_image=AsyncMock(return_value=None),
        needs_voice_tool=lambda text: False,
    )
    defaults.update(overrides)
    return BotContext(**defaults)


@pytest.mark.asyncio
async def test_direct_mention_replies():
    msg = FakeMessage('@testbot hello')
    marks = []
    ctx = _make_ctx(_marks=marks)

    await handle_text_message(msg, ctx)

    assert msg.replies == ['test-reply']
    assert marks == [100]


@pytest.mark.asyncio
async def test_ignore_other_bot_mention():
    msg = FakeMessage('@otherbot hello')
    ask_agent = AsyncMock(return_value='should-not-send')
    ctx = _make_ctx(ask_agent=ask_agent)

    await handle_text_message(msg, ctx)

    ask_agent.assert_not_awaited()
    assert msg.replies == []
