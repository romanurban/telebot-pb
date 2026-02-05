import sys
import os
import base64

os.environ.setdefault("TELEGRAM_TOKEN", "123456:TESTTOKEN")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault('ELEVEN_API_KEY', 'sk-test')

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, ".."))))

import pytest
from unittest.mock import AsyncMock

import main


class FakeImageData:
    def __init__(self, b64):
        self.b64_json = b64


class FakeImagesResponse:
    def __init__(self, data):
        self.data = data


@pytest.mark.asyncio
async def test_generate_image_success(monkeypatch):
    img_bytes = b"imgdata"
    encoded = base64.b64encode(img_bytes).decode()

    async_mock = AsyncMock(return_value=FakeImagesResponse([FakeImageData(encoded)]))
    monkeypatch.setattr(main._openai_images_client.images, "generate", async_mock)

    result = await main.generate_image_from_observation("obs")

    assert result == img_bytes
    async_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_image_failure(monkeypatch):
    async_mock = AsyncMock(side_effect=Exception("fail"))
    monkeypatch.setattr(main._openai_images_client.images, "generate", async_mock)

    result = await main.generate_image_from_observation("obs")

    assert result is None
    async_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_ask_openai_image(monkeypatch):
    dummy = b"dummy-img"
    # Prepare mock OpenAI methods
    file_mock = AsyncMock(return_value=type("F", (), {"id": "f1"}))
    agent_mock = AsyncMock(return_value="final reply")

    monkeypatch.setattr(main.openai_client.files, "create", file_mock)
    monkeypatch.setattr(main, "ask_agent", agent_mock)

    result = await main.ask_openai_image(dummy, prompt="p", chat_id=1)

    assert result == "final reply"
    assert file_mock.await_args.kwargs["file"].getvalue() == dummy
    agent_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_style_caption(monkeypatch):
    ask_mock = AsyncMock(return_value="styled")
    monkeypatch.setattr(main, "ask_openai", ask_mock)

    result = await main.style_caption("original", chat_id=42)

    assert result == "styled"
    assert ask_mock.await_count == 1
    assert "original" in ask_mock.await_args.args[0]
    assert ask_mock.await_args.kwargs["chat_id"] == 42
