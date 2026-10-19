import sys
import os

os.environ.setdefault("TELEGRAM_TOKEN", "123456:TESTTOKEN")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault('ELEVEN_API_KEY', 'sk-test')

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, ".."))))

import pytest
from unittest.mock import AsyncMock

import main


@pytest.mark.asyncio
async def test_generate_image_success(monkeypatch):
    monkeypatch.setattr(main, "ask_agent", AsyncMock(return_value='"A cat fixing a tap, photo"\nextra'))
    fetch = AsyncMock(return_value=b"imgdata")
    monkeypatch.setattr(main, "fetch_pollinations_image", fetch)

    result = await main.generate_image_from_observation("obs", chat_id=5)

    assert result == b"imgdata"
    fetch.assert_awaited_once_with("A cat fixing a tap, photo")


@pytest.mark.asyncio
async def test_generate_image_failure(monkeypatch):
    monkeypatch.setattr(main, "ask_agent", AsyncMock(return_value="A cat"))
    monkeypatch.setattr(main, "fetch_pollinations_image", AsyncMock(return_value=None))

    result = await main.generate_image_from_observation("obs", chat_id=5)

    assert result is None


@pytest.mark.asyncio
async def test_generate_image_prompt_error(monkeypatch):
    monkeypatch.setattr(main, "ask_agent", AsyncMock(side_effect=Exception("llm down")))
    fetch = AsyncMock()
    monkeypatch.setattr(main, "fetch_pollinations_image", fetch)
    assert await main.generate_image_from_observation("obs") is None
    fetch.assert_not_awaited()


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
