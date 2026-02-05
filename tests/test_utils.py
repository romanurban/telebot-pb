import sys
import os

# Ensure required environment variables to avoid import errors
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test')
os.environ.setdefault('ELEVEN_API_KEY', 'sk-test')

# Adjust path to import from repo root
sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, '..'))))

from main import clean_openai_reply
from mcp_server import _cyrillic_to_latin


def test_clean_openai_reply_single_tag():
    input_text = "Ответ 【4:5†foo.json】текст"
    assert clean_openai_reply(input_text) == "Ответ текст"


def test_clean_openai_reply_multiple_tags():
    input_text = "До 【1:2†a.json】свидания 【3:4†b.json】друг"
    assert clean_openai_reply(input_text) == "До свидания друг"


def test_clean_openai_reply_curly_braces_single_tag():
    input_text = "Ответ {24:0†foo.json}текст"
    assert clean_openai_reply(input_text) == "Ответ текст"


def test_clean_openai_reply_curly_braces_multiple_tags():
    input_text = "До {1:2†a.json}свидания {3:4†b.json}друг"
    assert clean_openai_reply(input_text) == "До свидания друг"


def test_clean_openai_reply_square_brackets_no_cross():
    input_text = "Заморочек【0:tagged_jura_messages.json】"
    assert clean_openai_reply(input_text) == "Заморочек"


def test_clean_openai_reply_curly_braces_no_cross():
    input_text = "Привет {0:tagged_jura_messages.json}пока"
    assert clean_openai_reply(input_text) == "Привет пока"


def test_cyrillic_to_latin():
    assert _cyrillic_to_latin("Рига") == "Riga"
