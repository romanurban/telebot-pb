import sys
import os

os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test')
os.environ.setdefault('ELEVEN_API_KEY', 'sk-test')

sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, '..'))))

import main
from datetime import datetime


def test_is_active_hours_true(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2024, 1, 1, 12, 0, tzinfo=main.BOT_TIMEZONE)

    monkeypatch.setattr(main, 'datetime', FixedDatetime)
    assert main.is_active_hours() is True


def test_is_active_hours_false(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2024, 1, 1, 7, 0, tzinfo=main.BOT_TIMEZONE)

    monkeypatch.setattr(main, 'datetime', FixedDatetime)
    assert main.is_active_hours() is False
