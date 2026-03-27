import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, '..'))))

from handlers.photos import handle_photo_message


class FakeUser:
    def __init__(self, username='tester', user_id=1):
        self.username = username
        self.id = user_id
        self.is_bot = False


class FakeChat:
    def __init__(self, chat_id=100):
        self.id = chat_id


class FakeDownloaded:
    def __init__(self, data=b'img'):
        self._data = data

    def read(self):
        return self._data


class FakePhoto:
    pass


class FakeMessage:
    def __init__(self, caption=None):
        self.caption = caption
        self.text = None
        self.message_id = 1
        self.date = datetime.now()
        self.photo = [FakePhoto()]
        self.from_user = FakeUser()
        self.chat = FakeChat()
        self.replies = []

    async def reply(self, text):
        self.replies.append(text)


@pytest.mark.asyncio
async def test_handle_photo_message_module():
    msg = FakeMessage(caption='look')
    fake_bot = AsyncMock()
    fake_bot.download = AsyncMock(return_value=FakeDownloaded(b'img-bytes'))
    last_activity_time = {}
    messages_since_bot_reply = {}
    marks = []

    await handle_photo_message(
        msg,
        bot=fake_bot,
        image_default_prompt='default prompt',
        last_activity_time=last_activity_time,
        messages_since_bot_reply=messages_since_bot_reply,
        try_claim_message=AsyncMock(return_value=True),
        ask_openai_image=AsyncMock(return_value='got image'),
        mark_bot_replied=lambda chat_id: marks.append(chat_id),
    )

    fake_bot.download.assert_awaited_once()
    assert msg.replies == ['got image']
    assert marks == [100]
    assert messages_since_bot_reply[100] == 1
    assert 100 in last_activity_time
