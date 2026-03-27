import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, '..'))))

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


@pytest.mark.asyncio
async def test_direct_mention_replies():
    msg = FakeMessage('@testbot hello')
    last_activity_time = {}
    messages_since_bot_reply = {}
    bot_unmentioned_count = {}
    last_bot_reply_time = {}
    marks = []
    broadcasts = []

    await handle_text_message(
        msg,
        bot_username='testbot',
        name_mention_re=None,
        image_default_prompt='',
        chat_react_prompt='react',
        max_unmentioned_replies=3,
        recent_activity_seconds=30,
        last_activity_time=last_activity_time,
        messages_since_bot_reply=messages_since_bot_reply,
        bot_unmentioned_count=bot_unmentioned_count,
        last_bot_reply_time=last_bot_reply_time,
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

    assert msg.replies == ['test-reply']
    assert marks == [100]


@pytest.mark.asyncio
async def test_ignore_other_bot_mention():
    msg = FakeMessage('@otherbot hello')
    ask_agent = AsyncMock(return_value='should-not-send')

    await handle_text_message(
        msg,
        bot_username='testbot',
        name_mention_re=None,
        image_default_prompt='',
        chat_react_prompt='react',
        max_unmentioned_replies=3,
        recent_activity_seconds=30,
        last_activity_time={},
        messages_since_bot_reply={},
        bot_unmentioned_count={},
        last_bot_reply_time={},
        try_claim_message=AsyncMock(return_value=True),
        nudge_inactive_chats=AsyncMock(),
        get_picture_of_the_day=AsyncMock(),
        style_caption=AsyncMock(),
        retrieve_joke=AsyncMock(),
        retrieve_fact=AsyncMock(),
        generate_voice_file=AsyncMock(),
        ask_openai=AsyncMock(),
        ask_agent=ask_agent,
        clean_openai_reply=lambda x: x,
        mark_bot_replied=lambda chat_id: None,
        extract_voice_file=AsyncMock(return_value=None),
        extract_json_image=AsyncMock(return_value=None),
        needs_voice_tool=lambda text: False,
    )

    ask_agent.assert_not_awaited()
    assert msg.replies == []
