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
    for i in range(12):
        nudge.remember_nudge(-1, f"opener {i}")
    recent = nudge.load_recent_nudges(-1)
    assert len(recent) == nudge.RECENT_KEEP and recent[-1] == "opener 11"
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
