import os
import json
import sys
import pytest

# Ensure required environment variables
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test')
os.environ.setdefault('ELEVEN_API_KEY', 'sk-test')

# Adjust path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, '..'))))

import main


@pytest.mark.asyncio
async def test_extract_json_image_from_nested(monkeypatch, tmp_path):
    file_path = tmp_path / "poster.jpg"
    file_path.write_text("x")

    async def fake_download(url: str) -> str:
        assert url == "https://commons.wikimedia.org/wiki/Special:FilePath/Jaws%20movie%20poster.jpg?width=800"
        return str(file_path)

    monkeypatch.setattr(main, "download_image_to_tmp", fake_download)

    inner = {
        "url": "https://commons.wikimedia.org/wiki/Special:FilePath/Jaws%20movie%20poster.jpg?width=800",
        "caption": "This famous design by Roger Kastel"
    }
    wrapper = {
        "type": "text",
        "text": json.dumps(inner),
        "annotations": None,
    }
    reply = json.dumps(wrapper)

    result = await main._extract_json_image(reply)
    assert result == (str(file_path), inner["caption"])


@pytest.mark.asyncio
async def test_extract_json_image_local_path():
    import tempfile
    with tempfile.NamedTemporaryFile(dir="/tmp", suffix=".jpg", delete=False) as f:
        f.write(b"x")
        file_path = f.name
    try:
        reply = f"[meme] {file_path}"
        result = await main._extract_json_image(reply)
        assert result == (file_path, "")
    finally:
        os.remove(file_path)


@pytest.mark.asyncio
async def test_extract_json_image_caption_after_json(monkeypatch, tmp_path):
    file_path = tmp_path / "img.jpg"
    file_path.write_text("x")

    async def fake_download(url: str) -> str:
        assert url == "http://example.com/img.jpg"
        return str(file_path)

    monkeypatch.setattr(main, "download_image_to_tmp", fake_download)

    reply = "Here it is\n{" + "\"url\": \"http://example.com/img.jpg\"}" + "\nNice view"

    result = await main._extract_json_image(reply)
    assert result == (str(file_path), "Nice view")


@pytest.mark.asyncio
async def test_extract_json_image_json_string(monkeypatch, tmp_path):
    file_path = tmp_path / "img.jpg"
    file_path.write_text("x")

    async def fake_download(url: str) -> str:
        assert url == "http://example.com/img.jpg"
        return str(file_path)

    monkeypatch.setattr(main, "download_image_to_tmp", fake_download)

    inner = {"url": "http://example.com/img.jpg", "caption": "Nice"}
    reply = json.dumps(json.dumps(inner))

    result = await main._extract_json_image(reply)

    assert result == (str(file_path), "Nice")
