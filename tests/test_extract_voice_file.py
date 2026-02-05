import os
import sys
import pytest
import json

os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test')
os.environ.setdefault('ELEVEN_API_KEY', 'sk-test')

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, '..'))))

import main


@pytest.mark.asyncio
async def test_extract_voice_file_local_path():
    import tempfile
    with tempfile.NamedTemporaryFile(dir="/tmp", suffix=".mp3", delete=False) as f:
        f.write(b"x")
        file_path = f.name
    try:
        reply = f"Here {file_path}"
        result = await main._extract_voice_file(reply)
        assert result == (file_path, "Here")
    finally:
        os.remove(file_path)


@pytest.mark.asyncio
async def test_extract_voice_file_json(monkeypatch, tmp_path):
    file_path = tmp_path / "voice.ogg"
    file_path.write_text('x')
    data = {"path": str(file_path), "caption": "hi"}
    reply = json.dumps(data)

    result = await main._extract_voice_file(reply)
    assert result == (str(file_path), "hi")
