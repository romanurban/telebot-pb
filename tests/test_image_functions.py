import sys
import os

os.environ.setdefault("TELEGRAM_TOKEN", "123456:TESTTOKEN")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault('ELEVEN_API_KEY', 'sk-test')

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, ".."))))

import pytest
from unittest.mock import AsyncMock

import main


import agent_client


@pytest.mark.asyncio
async def test_generate_image_success(monkeypatch):
    monkeypatch.setattr(agent_client, "ask_stateless", AsyncMock(return_value='"A cat fixing a tap, photo"\nextra'))
    fetch = AsyncMock(return_value=b"imgdata")
    monkeypatch.setattr(main, "fetch_pollinations_image", fetch)

    result = await main.generate_image_from_observation("obs", chat_id=5)

    assert result == b"imgdata"
    fetch.assert_awaited_once_with("A cat fixing a tap, photo")


@pytest.mark.asyncio
async def test_generate_image_failure(monkeypatch):
    monkeypatch.setattr(agent_client, "ask_stateless", AsyncMock(return_value="A ginger cat on a white car in the rain"))
    monkeypatch.setattr(main, "fetch_pollinations_image", AsyncMock(return_value=None))

    result = await main.generate_image_from_observation("obs", chat_id=5)

    assert result is None


@pytest.mark.asyncio
async def test_generate_image_prompt_error(monkeypatch):
    monkeypatch.setattr(agent_client, "ask_stateless", AsyncMock(side_effect=Exception("llm down")))
    fetch = AsyncMock()
    monkeypatch.setattr(main, "fetch_pollinations_image", fetch)
    assert await main.generate_image_from_observation("obs") is None
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_in_character_refusal_never_becomes_image_prompt(monkeypatch):
    # The real incident: the persona answered the image-prompt request in chat style.
    monkeypatch.setattr(agent_client, "ask_stateless",
                        AsyncMock(return_value="Не буду рисовать, @neuromaksim_bot. Диван ждёт, планов нет."))
    fetch = AsyncMock()
    monkeypatch.setattr(main, "fetch_pollinations_image", fetch)
    assert await main.generate_image_from_observation("obs", chat_id=5) is None
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_image_prompt_does_not_touch_chat_history(monkeypatch):
    ask_agent = AsyncMock()
    monkeypatch.setattr(main, "ask_agent", ask_agent)
    stateless = AsyncMock(return_value="A ginger cat wearing sunglasses on a car hood, rainy street")
    monkeypatch.setattr(agent_client, "ask_stateless", stateless)
    agent_client._histories.pop(0, None)

    await main.make_image_prompt("кот на машине")

    ask_agent.assert_not_awaited()
    assert 0 not in agent_client._histories
    assert stateless.await_args.kwargs.get("persona", False) is False


@pytest.mark.asyncio
async def test_ask_openai_image_passes_caption_as_context(monkeypatch):
    dummy = b"dummy-img"
    file_mock = AsyncMock(return_value=type("F", (), {"id": "f1"}))
    agent_mock = AsyncMock(return_value="final reply")

    monkeypatch.setattr(main, "USE_OPENROUTER", False)
    monkeypatch.setattr(main.openai_client.files, "create", file_mock)
    monkeypatch.setattr(main, "ask_agent", agent_mock)

    result = await main.ask_openai_image(dummy, "Нормальный прикол", chat_id=1, sender="neuromans_bot")

    assert result == "final reply"
    assert file_mock.await_args.kwargs["file"].getvalue() == dummy
    text = agent_mock.await_args.args[0][0]["content"][0]["text"]
    assert "neuromans_bot" in text and "Подпись: Нормальный прикол" in text


@pytest.mark.asyncio
async def test_vision_gets_neutral_prompt_not_the_caption(monkeypatch):
    seen = {}

    async def fake_vision(path, prompt):
        seen["prompt"] = prompt
        return "Кот на поводке в трамвае"

    agent_mock = AsyncMock(return_value="reply")
    monkeypatch.setattr(main, "USE_OPENROUTER", True)
    monkeypatch.setattr(main, "analyze_image_via_mcp", fake_vision)
    monkeypatch.setattr(main, "ask_agent", agent_mock)

    await main.ask_openai_image(b"img", "Нормальный прикол. Можно в чат.", chat_id=1, sender="x")

    assert seen["prompt"] == main.VISION_DESCRIBE_PROMPT
    sent = agent_mock.await_args.args[0][0]["content"]
    assert "Кот на поводке в трамвае" in sent and "Подпись: Нормальный прикол. Можно в чат." in sent


@pytest.mark.asyncio
async def test_style_caption_is_stateless(monkeypatch):
    stateless = AsyncMock(return_value="styled")
    ask_openai = AsyncMock()
    monkeypatch.setattr(agent_client, "ask_stateless", stateless)
    monkeypatch.setattr(main, "ask_openai", ask_openai)

    result = await main.style_caption("original", chat_id=42)

    assert result == "styled"
    ask_openai.assert_not_awaited()
    assert stateless.await_args.args[1] == "original"
    assert stateless.await_args.kwargs["persona"] is True


@pytest.mark.asyncio
async def test_style_caption_falls_back_to_original(monkeypatch):
    monkeypatch.setattr(agent_client, "ask_stateless", AsyncMock(side_effect=Exception("down")))
    assert await main.style_caption("original", chat_id=42) == "original"
