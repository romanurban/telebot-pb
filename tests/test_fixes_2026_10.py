"""Regression tests for the issues found in the 2026-10-21 conversation log."""
import importlib
import os
import sys

os.environ.setdefault("TELEGRAM_TOKEN", "123456:TESTTOKEN")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, ".."))))

import agent_client
from message_filter import fix_mentions, tidy_reply

BOTS = ["neuromaksim_bot", "neuromans_bot", "neuroyury_bot"]


def test_history_dir_is_per_bot(monkeypatch):
    monkeypatch.setenv("BOT_USERNAME", "neuromans_bot")
    mod = importlib.reload(agent_client)
    try:
        assert mod.HISTORY_DIR == os.path.join("chat_history", "neuromans_bot")
    finally:
        monkeypatch.delenv("BOT_USERNAME", raising=False)
        importlib.reload(agent_client)


def test_two_bots_do_not_overwrite_each_other(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for bot, line in (("bot_a", "я A"), ("bot_b", "я B")):
        monkeypatch.setattr(agent_client, "HISTORY_DIR", os.path.join("chat_history", bot))
        agent_client._histories.clear()
        agent_client._histories[-1] = [{"role": "assistant", "content": line}]
        agent_client.save_histories_to_disk()
    monkeypatch.setattr(agent_client, "HISTORY_DIR", os.path.join("chat_history", "bot_a"))
    agent_client._histories.clear()
    agent_client.load_histories_from_disk()
    assert agent_client._histories[-1][0]["content"] == "я A"
    agent_client._histories.clear()


def test_garbled_bot_mention_is_repaired():
    assert fix_mentions("@neuромаксимбот бокал не виноват.", BOTS) == "@neuromaksim_bot бокал не виноват."
    assert fix_mentions("@нейроюработ ну чо", BOTS) == "@neuroyury_bot ну чо"


def test_unknown_bot_mention_dropped_human_kept():
    assert tidy_reply(fix_mentions("@какойтобот ну чо", BOTS)) == "ну чо"
    assert fix_mentions("@urbanroman ну чо", BOTS) == "@urbanroman ну чо"


def test_tidy_reply_removes_mention_debris():
    assert tidy_reply("Кину, . Восстание подождёт.") == "Кину. Восстание подождёт."
    assert tidy_reply(", пойдёшь на квиз сегодня?") == "пойдёшь на квиз сегодня?"
    assert tidy_reply("Пошли бы, но  уже записал нас.") == "Пошли бы, но уже записал нас."
    assert tidy_reply("1,5 евро. Ну... ладно?!") == "1,5 евро. Ну... ладно?!"


def test_common_rules_and_handle_are_in_system_prompt(monkeypatch, tmp_path):
    import main
    monkeypatch.setattr(main, "BOT_USERNAME", "neuromans_bot")
    prompt = main.load_system_prompt() if os.path.exists(main.SYSTEM_PROMPT_FILE) else None
    if prompt is None:  # tests run without BOT_USERNAME: point at a real persona
        monkeypatch.setattr(main, "SYSTEM_PROMPT_FILE", os.path.join("prompts", "neuromans_bot", "system_prompt.yaml"))
        prompt = main.load_system_prompt()
    assert "shared_rules" in prompt
    assert "@neuromans_bot" in prompt


def test_known_bots_detected():
    import main
    assert set(BOTS) <= set(main.KNOWN_BOTS)


def test_recent_nudges_shared_and_fed_into_prompt(monkeypatch, tmp_path):
    import claims
    import nudge
    monkeypatch.setattr(claims, "NUDGE_DIR", str(tmp_path / "nudges"))
    assert nudge.load_recent_nudges(-1) == []
    nudge.remember_nudge(-1, "22 октября 1964-го Сартр отказался от Нобелевской премии.")
    for i in range(nudge.RECENT_KEEP + 2):
        nudge.remember_nudge(-1, f"opener {i}")
    recent = nudge.load_recent_nudges(-1)
    assert len(recent) == nudge.RECENT_KEEP and recent[-1] == f"opener {nudge.RECENT_KEEP + 1}"
    msg = nudge.build_nudge_messages("Расскажи факт дня", ["Сартр отказался от премии"])[0]["content"]
    assert "Сартр отказался от премии" in msg and "Не повторяй" in msg
    assert "Недавно" not in nudge.build_nudge_messages("x")[0]["content"]


def test_echo_check_catches_word_rallies_from_the_log():
    from message_filter import echo_problem
    assert echo_problem("Без возврата — это преимущество.", "На шаре бы не сел. Высота без возврата.")
    assert echo_problem("Навеса нет — мерзнешь по расписанию.", "Остановка без навеса. Стоишь и мерзнешь.")
    assert echo_problem("Одиннадцать с двумя — и уже одевайся теплее.", "В Риге одиннадцать градусов. Одевайтесь теплее.")
    assert echo_problem("Кот затаскивает под машинку. Производители не при чём.",
                        "может носки в стиралке исчезают по сговору производителей") is None
    assert echo_problem("соседи сами скинутся тебе на новый", "Поставить холодильник, который орет ночью.") is None


def test_latin_check():
    from message_filter import latin_problem
    assert latin_problem("В Ziepniekalns ждёшь autobus")
    assert latin_problem("@neuromans_bot ну да, https://x.lv/a 👍") is None


def _fake_run_factory(replies, seen):
    async def fake_run(agent, instructions, api_history, label, **kw):
        seen.append(api_history)
        return replies.pop(0)
    return fake_run


def test_checker_retries_once_then_accepts(monkeypatch):
    import asyncio
    seen = []
    monkeypatch.setattr(agent_client, "_agent", object())
    monkeypatch.setattr(agent_client, "_system_history", [{"role": "system", "content": "persona"}])
    monkeypatch.setattr(agent_client, "_run", _fake_run_factory(["Навеса нет — мерзнешь.", "Пойду пешком."], seen))
    from message_filter import echo_problem
    monkeypatch.setattr(agent_client, "reply_checker", echo_problem)
    agent_client._histories.pop(77, None)
    reply = asyncio.run(agent_client.ask_agent(
        [{"role": "user", "content": "neuromans_bot: Остановка без навеса. Стоишь и мерзнешь."}], chat_id=77))
    assert reply == "Пойду пешком."
    assert len(seen) == 2 and "не годится" in str(seen[1][-1])
    assert agent_client._histories[77][-1] == {"role": "assistant", "content": "Пойду пешком."}
    agent_client._histories.pop(77, None)


def test_checker_stays_silent_after_second_failure(monkeypatch):
    import asyncio
    monkeypatch.setattr(agent_client, "_agent", object())
    monkeypatch.setattr(agent_client, "_system_history", [{"role": "system", "content": "persona"}])
    monkeypatch.setattr(agent_client, "_run", _fake_run_factory(["В Ziepniekalns autobus", "Ziepniekalns rulez"], []))
    from message_filter import latin_problem
    monkeypatch.setattr(agent_client, "reply_checker", lambda r, a: latin_problem(r))
    agent_client._histories.pop(78, None)
    reply = asyncio.run(agent_client.ask_agent([{"role": "user", "content": "x: привет"}], chat_id=78))
    assert reply == ""
    assert all(m["role"] != "assistant" for m in agent_client._histories[78])
    agent_client._histories.pop(78, None)


def test_tool_payloads_and_photo_reactions_skip_checks(monkeypatch):
    import main
    monkeypatch.setattr(main, "CYRILLIC_ONLY", True)
    assert main.check_reply('{"image": "https://x/y.jpg", "caption": "кот"}', None) is None
    assert main.check_reply("https://memes.example/cat.jpg", None) is None
    answered = f"x {main.PHOTO_MARK}.\nНа картинке: кот на поводке в трамвае"
    assert main.check_reply("Кот на поводке серьёзнее бунта.", answered) is None
    assert main.check_reply("В Ziepniekalns autobus", None)


def test_last_nudger(monkeypatch, tmp_path):
    import claims
    from datetime import datetime
    monkeypatch.setattr(claims, "NUDGE_DIR", str(tmp_path / "n"))
    assert claims.last_nudger(1) is None
    claims.try_claim_nudge(1, "neuromaksim_bot", now=datetime(2026, 10, 22, 12))
    assert claims.last_nudger(1) == "neuromaksim_bot"


def test_maksim_lives_in_imanta():
    text = open(os.path.join("prompts", "neuromaksim_bot", "system_prompt.yaml"), encoding="utf-8").read()
    nudges = open(os.path.join("prompts", "neuromaksim_bot", "bot_prompts.yaml"), encoding="utf-8").read()
    assert "Imanta" in text and "Ziepniekalns" not in text + nudges


def test_echo_check_prefix_roots_and_opening_word():
    from message_filter import echo_problem
    assert echo_problem("Молчит — значит согласен.", "Молчание — тоже реакция.")
    assert echo_problem("Шутка зашла. Без смеха.", "Ладно, шутка удалась.")
    for reply, prev in [
        ("Даже у осьминога бывают задержки.", "Три сердца, синяя кровь — и всё равно по расписанию не ходят."),
        ("Сам не помню, о чём речь.", "главное никому не напоминать"),
        ("Пустая тара — не валюта. Кэссу не обманешь.", "Женщина пытается оплатить пустой бутылкой из-под кефира."),
    ]:
        assert echo_problem(reply, prev) is None, reply


class _Target:
    def __init__(self):
        self.photos = []

    async def send_photo(self, chat_id, photo, caption=None):
        self.photos.append((chat_id, caption))


def test_meme_nudge_is_fetched_in_code(monkeypatch, tmp_path):
    import asyncio
    from unittest.mock import AsyncMock
    import main
    meme = tmp_path / "meme.jpg"
    meme.write_bytes(b"img")
    monkeypatch.setattr(main, "retrieve_joke", AsyncMock(return_value=str(meme)))
    monkeypatch.setattr(main, "analyze_image_via_mcp", AsyncMock(return_value="Кот в коробке"))
    monkeypatch.setattr(agent_client, "ask_stateless", AsyncMock(return_value="Пятничный кот одобряет"))
    target = _Target()
    posted = asyncio.run(main.send_media_nudge(target, -1, "Вызови retrieve_joke и отправь мем с комментарием.", is_message=False))
    assert posted == "Пятничный кот одобряет"
    assert target.photos == [(-1, "Пятничный кот одобряет")]
    assert not meme.exists()  # temp file cleaned up


def test_meme_without_vision_is_posted_without_caption(monkeypatch, tmp_path):
    import asyncio
    from unittest.mock import AsyncMock
    import main
    meme = tmp_path / "meme.jpg"
    meme.write_bytes(b"img")
    monkeypatch.setattr(main, "retrieve_joke", AsyncMock(return_value=str(meme)))
    monkeypatch.setattr(main, "analyze_image_via_mcp", AsyncMock(side_effect=RuntimeError("vision down")))
    target = _Target()
    posted = asyncio.run(main.send_media_nudge(target, -1, "Получи мем через retrieve_joke и отправь его.", is_message=False))
    assert posted == "[мем]" and target.photos == [(-1, None)]


def test_text_nudges_are_not_media(monkeypatch):
    import asyncio
    import main
    assert asyncio.run(main.send_media_nudge(_Target(), -1, "Скажи случайный факт.", is_message=False)) is None


def test_meme_fetch_failure_signals_text_fallback(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    import main
    monkeypatch.setattr(main, "retrieve_joke", AsyncMock(side_effect=RuntimeError("no meme")))
    # "" = picture nudge failed: the loop switches to a text-only prompt
    assert asyncio.run(main.send_media_nudge(_Target(), -1, "retrieve_joke", is_message=False)) == ""


def test_failed_picture_nudge_returns_empty_and_text_prompt_is_picked(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    import main
    import nudge
    monkeypatch.setattr(main, "get_picture_of_the_day", AsyncMock(side_effect=RuntimeError("tool failed")))
    assert asyncio.run(main.send_media_nudge(_Target(), -1, "get_picture_of_the_day", is_message=False)) == ""
    prompts = iter(["Получи мем через retrieve_joke", "get_picture_of_the_day", "Скажи факт"])
    assert nudge.text_only_prompt(lambda c: next(prompts), -1) == "Скажи факт"
    assert nudge.text_only_prompt(lambda c: "retrieve_joke", -1) == nudge.FALLBACK_TEXT_PROMPT


def test_release_nudge_frees_slot_for_other_bots(monkeypatch, tmp_path):
    import claims
    from datetime import datetime, timedelta
    monkeypatch.setattr(claims, "NUDGE_DIR", str(tmp_path / "n"))
    now = datetime(2026, 10, 26, 15, 48)
    assert claims.try_claim_nudge(1, "neuromans_bot", now=now)
    claims.release_nudge(1, "neuroyury_bot")  # not ours: no effect
    assert not claims.try_claim_nudge(1, "neuromaksim_bot", now=now + timedelta(minutes=5))
    claims.release_nudge(1, "neuromans_bot")
    assert claims.try_claim_nudge(1, "neuromaksim_bot", now=now + timedelta(minutes=5))


def test_recent_list_is_long_enough_for_two_days():
    import nudge
    assert nudge.RECENT_KEEP >= 20


def test_news_rss_parsing():
    import mcp_server
    xml = """<rss><channel><title>X</title>
      <item><title>Рига &amp; новости</title><link>https://rus.lsm.lv/a</link>
        <description>&lt;p&gt;Кратко   о главном&lt;/p&gt;</description><pubDate>Tue</pubDate></item>
      <item><title></title><link>https://x</link></item></channel></rss>"""
    items = mcp_server._parse_rss(xml, "LSM", 5)
    assert items == [{"source": "LSM", "title": "Рига & новости", "summary": "Кратко о главном",
                      "link": "https://rus.lsm.lv/a", "published": "Tue"}]


class _NewsTarget:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)


def test_news_nudge_posts_real_link_and_skips_seen(monkeypatch, tmp_path):
    import asyncio
    from unittest.mock import AsyncMock
    import claims
    import main
    import nudge
    monkeypatch.setattr(claims, "NUDGE_DIR", str(tmp_path / "n"))
    news = [{"source": "LSM", "title": "Старая", "summary": "", "link": "https://lsm/old"},
            {"source": "LSM", "title": "В Риге открыли музей носков", "summary": "Кратко", "link": "https://lsm/new"}]
    nudge.remember_news(-1, news[0])
    monkeypatch.setattr(main, "fetch_news", AsyncMock(return_value=news))
    monkeypatch.setattr(main.random, "shuffle", lambda x: None)
    monkeypatch.setattr(agent_client, "ask_stateless",
                        AsyncMock(return_value='{"i": 0, "comment": "Наконец-то культура"}'))
    target = _NewsTarget()
    posted = asyncio.run(main.send_news_nudge(target, -1, "get_news: новость из Латвии", is_message=False))
    assert target.sent == ["Наконец-то культура\nhttps://lsm/new"]
    assert "музей носков" in posted
    assert "музей носков" in nudge.news_context(-1, "Наконец-то культура\nhttps://lsm/new")
    assert nudge.news_context(-1, "просто текст") == ""


def test_news_nudge_bad_pick_falls_back(monkeypatch, tmp_path):
    import asyncio
    from unittest.mock import AsyncMock
    import claims
    import main
    monkeypatch.setattr(claims, "NUDGE_DIR", str(tmp_path / "n"))
    monkeypatch.setattr(main, "fetch_news", AsyncMock(return_value=[{"source": "BBC", "title": "t", "summary": "", "link": "https://b/1"}]))
    monkeypatch.setattr(agent_client, "ask_stateless", AsyncMock(return_value="не json"))
    target = _NewsTarget()
    assert asyncio.run(main.send_news_nudge(target, -1, "get_news", is_message=False)) == ""
    assert target.sent == []


def test_no_domestic_nudges_and_news_present():
    import yaml
    for b in ("neuroyury_bot", "neuromans_bot", "neuromaksim_bot"):
        prompts = yaml.safe_load(open(os.path.join("prompts", b, "bot_prompts.yaml"), encoding="utf-8"))["nudge_system_prompts"]
        text = " ".join(prompts).lower()
        assert sum("get_news" in p for p in prompts) >= 4
        import re
        for word in (r"\bкран", r"\bсосед", r"\bпогод", r"\bбыт"):
            assert not re.search(word, text), (b, word)
