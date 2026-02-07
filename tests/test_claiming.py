import os
import sys
import tempfile

os.environ.setdefault("TELEGRAM_TOKEN", "123456:TESTTOKEN")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("ELEVEN_API_KEY", "sk-test")

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, ".."))))

import pytest
from unittest.mock import AsyncMock
from datetime import datetime

import main


class FakeUser:
    def __init__(self, username="tester", id=1):
        self.username = username
        self.id = id
        self.is_bot = False


class FakeChat:
    def __init__(self, id=100):
        self.id = id


class FakeMessage:
    def __init__(self, text, user_id=1, chat_id=100, ts=None):
        self.text = text
        self.caption = None
        self.message_id = 1
        self.date = ts or datetime(2024, 1, 1, 12, 0, 0)
        self.from_user = FakeUser(id=user_id)
        self.chat = FakeChat(id=chat_id)


def test_claim_key_deterministic():
    msg = FakeMessage("hello", user_id=42, chat_id=100)
    key1 = main._claim_key(msg)
    key2 = main._claim_key(msg)
    assert key1 == key2


def test_claim_key_differs_for_different_text():
    msg_a = FakeMessage("hello", user_id=42, chat_id=100)
    msg_b = FakeMessage("world", user_id=42, chat_id=100)
    assert main._claim_key(msg_a) != main._claim_key(msg_b)


def test_claim_key_differs_for_different_user():
    msg_a = FakeMessage("hello", user_id=1, chat_id=100)
    msg_b = FakeMessage("hello", user_id=2, chat_id=100)
    assert main._claim_key(msg_a) != main._claim_key(msg_b)


def test_claim_key_uses_caption_for_photos():
    msg = FakeMessage(None, user_id=1, chat_id=100)
    msg.text = None
    msg.caption = "nice photo"
    key = main._claim_key(msg)
    assert key  # should not error


@pytest.mark.asyncio
async def test_try_claim_first_wins(monkeypatch, tmp_path):
    claim_dir = str(tmp_path / "claims")
    os.makedirs(claim_dir)
    monkeypatch.setattr(main, "CLAIM_DIR", claim_dir)
    monkeypatch.setattr(main, "bot", AsyncMock())
    # Remove random delay
    monkeypatch.setattr(main.asyncio, "sleep", AsyncMock())

    msg = FakeMessage("test")
    result = await main.try_claim_message(msg)
    assert result is True


@pytest.mark.asyncio
async def test_try_claim_second_loses(monkeypatch, tmp_path):
    claim_dir = str(tmp_path / "claims")
    os.makedirs(claim_dir)
    monkeypatch.setattr(main, "CLAIM_DIR", claim_dir)
    monkeypatch.setattr(main, "bot", AsyncMock())
    monkeypatch.setattr(main.asyncio, "sleep", AsyncMock())

    msg = FakeMessage("test")
    first = await main.try_claim_message(msg)
    assert first is True

    second = await main.try_claim_message(msg)
    assert second is False
