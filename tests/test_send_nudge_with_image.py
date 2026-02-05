import sys
import os

os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test')
os.environ.setdefault('ELEVEN_API_KEY', 'sk-test')

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, '..'))))

import pytest
from unittest.mock import AsyncMock
import json
import base64

import main


class FakeTarget:
    def __init__(self):
        self.answer_calls = []
        self.answer_photo_calls = []
        self.send_message_calls = []
        self.send_photo_calls = []
        self.answer_voice_calls = []
        self.send_voice_calls = []

    async def answer(self, *args, **kwargs):
        self.answer_calls.append((args, kwargs))

    async def answer_photo(self, *args, **kwargs):
        self.answer_photo_calls.append((args, kwargs))

    async def send_message(self, *args, **kwargs):
        self.send_message_calls.append((args, kwargs))

    async def send_photo(self, *args, **kwargs):
        self.send_photo_calls.append((args, kwargs))

    async def answer_voice(self, *args, **kwargs):
        self.answer_voice_calls.append((args, kwargs))

    async def send_voice(self, *args, **kwargs):
        self.send_voice_calls.append((args, kwargs))


@pytest.mark.asyncio
async def test_send_nudge_with_image(monkeypatch):
    target = FakeTarget()
    gen_mock = AsyncMock(return_value=b'dummy')
    monkeypatch.setattr(main, 'generate_image_from_observation', gen_mock)
    monkeypatch.setattr(main.random, 'random', lambda: 0)

    await main.send_nudge_with_image(target, 1, 'hello', caption='cap', is_message=True)

    assert len(target.answer_calls) == 1
    assert len(target.answer_photo_calls) == 1
    assert target.send_photo_calls == []

    # Reset lists for bot case
    target.answer_calls.clear()
    target.answer_photo_calls.clear()
    target.send_message_calls.clear()
    target.send_photo_calls.clear()

    await main.send_nudge_with_image(target, 1, 'hello', caption='cap', is_message=False)

    assert len(target.send_message_calls) == 1
    assert len(target.send_photo_calls) == 1
    assert target.answer_photo_calls == []


@pytest.mark.asyncio
async def test_send_nudge_with_image_json(monkeypatch):
    target = FakeTarget()
    img_bytes = b"imgdata"
    encoded = base64.b64encode(img_bytes).decode()
    reply = json.dumps({"image": encoded, "caption": "hi"})
    style_mock = AsyncMock(return_value="styled")
    monkeypatch.setattr(main, "style_caption", style_mock)
    gen_mock = AsyncMock()
    monkeypatch.setattr(main, "generate_image_from_observation", gen_mock)

    await main.send_nudge_with_image(target, 1, reply, is_message=True)

    assert target.answer_calls == []
    assert len(target.answer_photo_calls) == 1
    photo_args, photo_kwargs = target.answer_photo_calls[0]
    assert photo_kwargs["caption"] == "styled"
    assert isinstance(photo_args[0], main.BufferedInputFile)
    gen_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_nudge_with_image_json_no_caption(monkeypatch):
    target = FakeTarget()
    img_bytes = b"imgdata"
    encoded = base64.b64encode(img_bytes).decode()
    reply = json.dumps({"image": encoded})
    style_mock = AsyncMock()
    monkeypatch.setattr(main, "style_caption", style_mock)

    await main.send_nudge_with_image(target, 1, reply, is_message=True)

    assert target.answer_calls == []
    assert len(target.answer_photo_calls) == 1
    photo_args, photo_kwargs = target.answer_photo_calls[0]
    assert photo_kwargs.get("caption", "") == ""
    style_mock.assert_not_called()


@pytest.mark.asyncio
async def test_send_nudge_with_image_meme_command(monkeypatch, tmp_path):
    target = FakeTarget()
    meme_path = tmp_path / "meme.jpg"
    meme_mock = AsyncMock(return_value=str(meme_path))
    monkeypatch.setattr(main, "retrieve_joke", meme_mock)

    await main.send_nudge_with_image(target, 1, json.dumps({"command": "/meme"}), is_message=True)

    assert target.answer_calls == []
    assert len(target.answer_photo_calls) == 1
    photo_args, _ = target.answer_photo_calls[0]
    assert isinstance(photo_args[0], main.FSInputFile)
    meme_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_nudge_with_image_fact_command(monkeypatch):
    target = FakeTarget()
    fact_mock = AsyncMock(return_value="Факт дня.")
    monkeypatch.setattr(main, "retrieve_fact", fact_mock)

    await main.send_nudge_with_image(target, 1, json.dumps({"command": "/fact"}), is_message=True)

    assert len(target.answer_calls) == 1
    args, kwargs = target.answer_calls[0]
    assert args == ("Факт дня.",)
    assert kwargs.get("parse_mode") == main.ParseMode.HTML
    fact_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_nudge_with_image_plain_url(monkeypatch, tmp_path):
    target = FakeTarget()
    file_path = tmp_path / "img.jpg"
    file_path.write_text("x")
    async def fake_download(url: str) -> str:
        assert url == "http://example.com/img.jpg"
        return str(file_path)

    monkeypatch.setattr(main, "download_image_to_tmp", fake_download)
    gen_mock = AsyncMock()
    monkeypatch.setattr(main, "generate_image_from_observation", gen_mock)

    await main.send_nudge_with_image(
        target,
        1,
        "Check this http://example.com/img.jpg",
        is_message=True,
    )

    assert target.answer_calls == []
    assert len(target.answer_photo_calls) == 1
    photo_args, photo_kwargs = target.answer_photo_calls[0]
    assert isinstance(photo_args[0], main.FSInputFile)
    assert photo_kwargs.get("caption", "") == ""
    gen_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.skip(reason="voice functionality currently disabled")
async def test_send_nudge_with_voice(monkeypatch, tmp_path):
    target = FakeTarget()
    file_path = tmp_path / "v.ogg"
    file_path.write_text("x")

    await main.send_nudge_with_image(target, 1, f"Take this {file_path}", is_message=True)

    assert len(target.answer_voice_calls) == 1
    args, _ = target.answer_voice_calls[0]
    assert isinstance(args[0], main.FSInputFile)
    assert args[0].path == str(file_path)
    assert len(target.answer_calls) == 1
    assert target.answer_calls[0][0] == ("Take this",)
