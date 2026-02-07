import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, ".."))))

import pytest


def test_missing_telegram_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    monkeypatch.setenv("BOT_USERNAME", "testbot")

    import main
    monkeypatch.setattr(main, "TELEGRAM_TOKEN", "")
    monkeypatch.setattr(main, "OPENAI_API_KEY", "sk-real")
    monkeypatch.setattr(main, "BOT_USERNAME", "testbot")
    monkeypatch.setattr(main, "SYSTEM_PROMPT_FILE", __file__)  # exists

    with pytest.raises(SystemExit):
        main.validate_environment()


def test_missing_openai_api_key(monkeypatch):
    import main
    monkeypatch.setattr(main, "TELEGRAM_TOKEN", "123:TOKEN")
    monkeypatch.setattr(main, "OPENAI_API_KEY", "")
    monkeypatch.setattr(main, "BOT_USERNAME", "testbot")
    monkeypatch.setattr(main, "SYSTEM_PROMPT_FILE", __file__)

    with pytest.raises(SystemExit):
        main.validate_environment()


def test_placeholder_telegram_token(monkeypatch):
    import main
    monkeypatch.setattr(main, "TELEGRAM_TOKEN", "<YOUR_TELEGRAM_TOKEN>")
    monkeypatch.setattr(main, "OPENAI_API_KEY", "sk-real")
    monkeypatch.setattr(main, "BOT_USERNAME", "testbot")
    monkeypatch.setattr(main, "SYSTEM_PROMPT_FILE", __file__)

    with pytest.raises(SystemExit):
        main.validate_environment()


def test_placeholder_openai_key(monkeypatch):
    import main
    monkeypatch.setattr(main, "TELEGRAM_TOKEN", "123:TOKEN")
    monkeypatch.setattr(main, "OPENAI_API_KEY", "<YOUR_OPENAI_API_KEY>")
    monkeypatch.setattr(main, "BOT_USERNAME", "testbot")
    monkeypatch.setattr(main, "SYSTEM_PROMPT_FILE", __file__)

    with pytest.raises(SystemExit):
        main.validate_environment()


def test_missing_bot_username(monkeypatch):
    import main
    monkeypatch.setattr(main, "TELEGRAM_TOKEN", "123:TOKEN")
    monkeypatch.setattr(main, "OPENAI_API_KEY", "sk-real")
    monkeypatch.setattr(main, "BOT_USERNAME", "")
    monkeypatch.setattr(main, "SYSTEM_PROMPT_FILE", __file__)

    with pytest.raises(SystemExit):
        main.validate_environment()


def test_missing_system_prompt_file(monkeypatch):
    import main
    monkeypatch.setattr(main, "TELEGRAM_TOKEN", "123:TOKEN")
    monkeypatch.setattr(main, "OPENAI_API_KEY", "sk-real")
    monkeypatch.setattr(main, "BOT_USERNAME", "testbot")
    monkeypatch.setattr(main, "SYSTEM_PROMPT_FILE", "/nonexistent/path.yaml")

    with pytest.raises(SystemExit):
        main.validate_environment()


def test_valid_environment_passes(monkeypatch):
    import main
    monkeypatch.setattr(main, "TELEGRAM_TOKEN", "123:TOKEN")
    monkeypatch.setattr(main, "OPENAI_API_KEY", "sk-real")
    monkeypatch.setattr(main, "BOT_USERNAME", "testbot")
    monkeypatch.setattr(main, "SYSTEM_PROMPT_FILE", __file__)  # exists

    # Should not raise
    main.validate_environment()
