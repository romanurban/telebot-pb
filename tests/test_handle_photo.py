import sys
import os

os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test')
os.environ.setdefault('ELEVEN_API_KEY', 'sk-test')

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, '..'))))

import pytest
from unittest.mock import AsyncMock

import main
import agent_client


class FakeUser:
    def __init__(self, username='tester', id=1):
        self.username = username
        self.id = id


class FakeChat:
    def __init__(self, id=100):
        self.id = id


class FakePhoto:
    def __init__(self, data=b'img'):
        self.data = data
        self.download_called = False
        self.read_called = False

    async def download(self):
        self.download_called = True
        return self

    def read(self):
        self.read_called = True
        return self.data


class FakeMessage:
    def __init__(self, caption=None):
        self.caption = caption
        self.photo = [FakePhoto()]
        self.from_user = FakeUser()
        self.chat = FakeChat()
        self.replies = []

    async def reply(self, text):
        self.replies.append(text)


@pytest.mark.asyncio
async def test_handle_photo(monkeypatch):
    agent_client._histories.clear()
    main.last_activity_time.clear()
    main.last_bot_reply_time.clear()

    msg = FakeMessage()
    photo_obj = msg.photo[-1]

    async def fake_download(photo):
        return await photo.download()

    monkeypatch.setattr(main.bot, 'download', fake_download)
    file_mock = AsyncMock(return_value=type('F', (), {'id': 'f1'}))
    ask_mock = AsyncMock(return_value='got it')
    monkeypatch.setattr(main.openai_client.files, 'create', file_mock)
    monkeypatch.setattr(main, 'ask_agent', ask_mock)

    await main.handle_photo(msg)

    assert photo_obj.download_called is True
    assert photo_obj.read_called is True
    file_mock.assert_awaited_once()
    ask_mock.assert_awaited_once()
    assert file_mock.await_args.kwargs['file'].getvalue() == photo_obj.data
    assert msg.replies == ['got it']
