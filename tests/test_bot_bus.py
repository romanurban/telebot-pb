import json
import os
import tempfile

import pytest

import bot_bus


@pytest.fixture(autouse=True)
def tmp_bus_dir(monkeypatch, tmp_path):
    """Redirect bus directory to a temporary folder for every test."""
    bus_dir = str(tmp_path / "bus")
    monkeypatch.setattr(bot_bus, "BOT_BUS_DIR", bus_dir)
    return bus_dir


def test_init_bus_creates_directory(tmp_bus_dir):
    assert not os.path.exists(tmp_bus_dir)
    bot_bus.init_bus()
    assert os.path.isdir(tmp_bus_dir)


def test_init_bus_idempotent(tmp_bus_dir):
    bot_bus.init_bus()
    bot_bus.init_bus()
    assert os.path.isdir(tmp_bus_dir)


def test_broadcast_creates_file_and_writes_jsonl(tmp_bus_dir):
    bot_bus.broadcast(100, "bot_a", "hello")
    path = os.path.join(tmp_bus_dir, "100.jsonl")
    assert os.path.exists(path)

    with open(path) as f:
        lines = f.readlines()
    assert len(lines) == 1

    record = json.loads(lines[0])
    assert record["bot"] == "bot_a"
    assert record["text"] == "hello"
    assert "ts" in record
    assert "via_bus" not in record


def test_broadcast_via_bus_flag(tmp_bus_dir):
    bot_bus.broadcast(100, "bot_a", "hi", via_bus=True)
    path = os.path.join(tmp_bus_dir, "100.jsonl")

    with open(path) as f:
        record = json.loads(f.readline())
    assert record["via_bus"] is True


def test_broadcast_appends_multiple(tmp_bus_dir):
    bot_bus.broadcast(100, "bot_a", "msg1")
    bot_bus.broadcast(100, "bot_b", "msg2")
    path = os.path.join(tmp_bus_dir, "100.jsonl")

    with open(path) as f:
        lines = f.readlines()
    assert len(lines) == 2


def test_poll_returns_empty_for_missing_file(tmp_bus_dir):
    bot_bus.init_bus()
    messages, pos = bot_bus.poll(999, "bot_a", 0)
    assert messages == []
    assert pos == 0


def test_poll_reads_new_messages_and_skips_own(tmp_bus_dir):
    bot_bus.broadcast(100, "bot_a", "from a")
    bot_bus.broadcast(100, "bot_b", "from b")

    messages, pos = bot_bus.poll(100, "bot_a", 0)
    assert len(messages) == 1
    assert messages[0]["bot"] == "bot_b"
    assert messages[0]["text"] == "from b"
    assert pos > 0


def test_poll_tracks_position(tmp_bus_dir):
    bot_bus.broadcast(100, "bot_b", "msg1")
    _, pos = bot_bus.poll(100, "bot_a", 0)

    # Write another message after first poll
    bot_bus.broadcast(100, "bot_b", "msg2")
    messages, new_pos = bot_bus.poll(100, "bot_a", pos)
    assert len(messages) == 1
    assert messages[0]["text"] == "msg2"
    assert new_pos > pos


def test_poll_handles_malformed_json(tmp_bus_dir):
    bot_bus.init_bus()
    path = os.path.join(tmp_bus_dir, "100.jsonl")
    with open(path, "w") as f:
        f.write("not json\n")
        f.write(json.dumps({"bot": "bot_b", "text": "ok", "ts": 1}) + "\n")

    messages, _ = bot_bus.poll(100, "bot_a", 0)
    assert len(messages) == 1
    assert messages[0]["text"] == "ok"


def test_trim_keeps_last_n_lines(tmp_bus_dir):
    for i in range(10):
        bot_bus.broadcast(100, "bot_a", f"msg{i}")

    bot_bus.trim(100, max_lines=3)
    path = os.path.join(tmp_bus_dir, "100.jsonl")
    with open(path) as f:
        lines = f.readlines()
    assert len(lines) == 3
    # Should keep the last 3
    assert json.loads(lines[0])["text"] == "msg7"
    assert json.loads(lines[2])["text"] == "msg9"


def test_trim_noop_when_small(tmp_bus_dir):
    bot_bus.broadcast(100, "bot_a", "msg1")
    bot_bus.broadcast(100, "bot_a", "msg2")
    bot_bus.trim(100, max_lines=5)

    path = os.path.join(tmp_bus_dir, "100.jsonl")
    with open(path) as f:
        lines = f.readlines()
    assert len(lines) == 2


def test_trim_missing_file(tmp_bus_dir):
    bot_bus.init_bus()
    # Should not raise
    bot_bus.trim(999, max_lines=5)
