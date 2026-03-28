import json
import os

import pytest
from unittest.mock import AsyncMock, Mock

import agent_client


@pytest.mark.asyncio
async def test_create_thread_and_ask(monkeypatch):
    monkeypatch.setenv("MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client, "MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client.MCPServerSse, "connect", AsyncMock())
    await agent_client.create_thread_with_system_prompt("sys", bot_name="bot")
    assert agent_client._agent is not None
    assert agent_client._system_history == [{"role": "system", "content": "sys"}]
    assert agent_client._agent.model == agent_client.OPENAI_MODEL
    assert agent_client._agent.tools == []

    servers = [
        s
        for s in agent_client._agent.mcp_servers
        if isinstance(s, agent_client.MCPServerSse)
    ]
    assert servers and servers[0].params["url"] == "http://example.com/sse"

    fake_result = Mock(final_output="hello")
    run_mock = AsyncMock(return_value=fake_result)
    monkeypatch.setattr(agent_client.Runner, "run", run_mock)

    reply = await agent_client.ask_agent([{"role": "user", "content": "hi"}], chat_id=1)

    assert reply == "hello"
    run_mock.assert_awaited_once()
    assert run_mock.await_args.args[0] is agent_client._agent
    # API input: system prompt + user message
    api_input = run_mock.await_args.args[1]
    assert api_input[0] == {"role": "system", "content": "sys"}
    assert api_input[1]["content"] == [{"type": "input_text", "text": "hi"}]
    # User message + assistant reply stored in history
    assert len(agent_client._histories[1]) == 2
    assert agent_client._histories[1][0] == {"role": "user", "content": "hi"}
    assert agent_client._histories[1][1] == {"role": "assistant", "content": "hello"}


@pytest.mark.asyncio
async def test_ask_without_init(monkeypatch):
    monkeypatch.setattr(agent_client, "_agent", None)
    with pytest.raises(RuntimeError):
        await agent_client.ask_agent([{"role": "user", "content": "hi"}], chat_id=1)


@pytest.mark.asyncio
async def test_history_keeps_last_n(monkeypatch):
    monkeypatch.setenv("MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client, "MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client.MCPServerSse, "connect", AsyncMock())
    await agent_client.create_thread_with_system_prompt("sys", bot_name="bot")

    fake_result = Mock(final_output="ok")
    run_mock = AsyncMock(return_value=fake_result)
    monkeypatch.setattr(agent_client.Runner, "run", run_mock)

    # Send more messages than MAX_HISTORY
    for i in range(8):
        await agent_client.ask_agent([{"role": "user", "content": f"msg{i}"}], chat_id=2)

    # 8 user + 8 assistant = 16 messages, all fit in MAX_HISTORY (20)
    history = agent_client._histories[2]
    assert len(history) == 16
    assert history[0] == {"role": "user", "content": "msg0"}
    assert history[1] == {"role": "assistant", "content": "ok"}
    assert history[-2] == {"role": "user", "content": "msg7"}
    assert history[-1] == {"role": "assistant", "content": "ok"}


@pytest.mark.asyncio
async def test_system_messages_not_stored(monkeypatch):
    monkeypatch.setenv("MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client, "MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client.MCPServerSse, "connect", AsyncMock())
    await agent_client.create_thread_with_system_prompt("sys", bot_name="bot")

    fake_result = Mock(final_output="ok")
    run_mock = AsyncMock(return_value=fake_result)
    monkeypatch.setattr(agent_client.Runner, "run", run_mock)

    # Send user message + system hint (like CHAT_REACT_PROMPT)
    await agent_client.ask_agent([
        {"role": "user", "content": "hello"},
        {"role": "system", "content": "react to this"},
    ], chat_id=3)

    # User message + assistant reply stored (no system messages)
    assert len(agent_client._histories[3]) == 2
    assert agent_client._histories[3][0]["role"] == "user"
    assert agent_client._histories[3][1]["role"] == "assistant"

    # But system hint was sent to API
    api_input = run_mock.await_args.args[1]
    roles = [m["role"] for m in api_input]
    assert "system" in roles  # system prompt and/or system hint


@pytest.mark.asyncio
async def test_history_normalization(monkeypatch):
    monkeypatch.setenv("MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client, "MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client.MCPServerSse, "connect", AsyncMock())
    await agent_client.create_thread_with_system_prompt("sys", bot_name="bot")

    fake_result = Mock(final_output="ok")
    run_mock = AsyncMock(return_value=fake_result)
    monkeypatch.setattr(agent_client.Runner, "run", run_mock)

    await agent_client.ask_agent([{"role": "user", "content": "hi"}], chat_id=1)

    # Check that API received normalized content
    api_input = run_mock.await_args.args[1]
    user_msg = api_input[1]
    assert isinstance(user_msg["content"], list)
    assert user_msg["content"][0]["type"] == "input_text"


def test_save_and_load_histories_roundtrip(monkeypatch, tmp_path):
    history_dir = str(tmp_path / "history")
    monkeypatch.setattr(agent_client, "HISTORY_DIR", history_dir)
    agent_client._histories.clear()

    agent_client._histories[100] = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    agent_client._histories[200] = [
        {"role": "user", "content": "test"},
    ]

    agent_client.save_histories_to_disk()

    # Clear and reload
    agent_client._histories.clear()
    agent_client.load_histories_from_disk()

    assert 100 in agent_client._histories
    assert 200 in agent_client._histories
    assert len(agent_client._histories[100]) == 2
    assert agent_client._histories[100][0]["content"] == "hi"
    assert agent_client._histories[200][0]["content"] == "test"


def test_load_histories_filters_invalid_roles(monkeypatch, tmp_path):
    history_dir = str(tmp_path / "history")
    os.makedirs(history_dir)
    monkeypatch.setattr(agent_client, "HISTORY_DIR", history_dir)
    agent_client._histories.clear()

    # Write a file with a mix of valid and invalid role entries
    data = [
        {"role": "user", "content": "good"},
        {"role": "system", "content": "should be filtered"},
        {"role": "assistant", "content": "also good"},
    ]
    with open(os.path.join(history_dir, "300.json"), "w") as f:
        json.dump(data, f)

    agent_client.load_histories_from_disk()
    assert len(agent_client._histories[300]) == 2
    roles = [m["role"] for m in agent_client._histories[300]]
    assert "system" not in roles


def test_inject_external_message():
    agent_client._histories.clear()
    agent_client.inject_external_message(100, "other_bot", "hey there")

    assert 100 in agent_client._histories
    assert len(agent_client._histories[100]) == 1
    msg = agent_client._histories[100][0]
    assert msg["role"] == "user"
    assert "other_bot: hey there" == msg["content"]


def test_inject_external_message_respects_max_history(monkeypatch):
    monkeypatch.setattr(agent_client, "MAX_HISTORY", 3)
    agent_client._histories.clear()

    for i in range(5):
        agent_client.inject_external_message(100, "bot", f"msg{i}")

    assert len(agent_client._histories[100]) == 3
    # Should keep the last 3
    assert agent_client._histories[100][0]["content"] == "bot: msg2"
    assert agent_client._histories[100][2]["content"] == "bot: msg4"


def test_clear_history():
    agent_client._histories.clear()
    agent_client._histories[100] = [{"role": "user", "content": "hi"}]
    agent_client.clear_history(100)
    assert 100 not in agent_client._histories


def test_clear_history_nonexistent():
    agent_client._histories.clear()
    # Should not raise
    agent_client.clear_history(999)
    assert 999 not in agent_client._histories


@pytest.mark.asyncio
async def test_image_message_stores_text_summary(monkeypatch):
    """Image messages should store the text prompt in history, not be skipped."""
    monkeypatch.setenv("MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client, "MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client.MCPServerSse, "connect", AsyncMock())
    await agent_client.create_thread_with_system_prompt("sys", bot_name="bot")

    fake_result = Mock(final_output="nice photo")
    run_mock = AsyncMock(return_value=fake_result)
    monkeypatch.setattr(agent_client.Runner, "run", run_mock)

    contents = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "What is in this image?"},
                {"type": "input_image", "file_id": "file-abc123"},
            ],
        }
    ]
    reply = await agent_client.ask_agent(contents, chat_id=50)

    assert reply == "nice photo"
    # History should contain a text-only summary, not be empty
    history = agent_client._histories[50]
    assert len(history) == 2  # user summary + assistant reply
    assert history[0]["role"] == "user"
    assert "What is in this image?" in history[0]["content"]
    assert "file_id" not in str(history[0]["content"])  # no image reference


@pytest.mark.asyncio
async def test_image_message_without_text_uses_placeholder(monkeypatch):
    """Image-only messages (no text) should store a placeholder in history."""
    monkeypatch.setenv("MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client, "MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client.MCPServerSse, "connect", AsyncMock())
    await agent_client.create_thread_with_system_prompt("sys", bot_name="bot")

    fake_result = Mock(final_output="interesting")
    run_mock = AsyncMock(return_value=fake_result)
    monkeypatch.setattr(agent_client.Runner, "run", run_mock)

    contents = [
        {
            "role": "user",
            "content": [
                {"type": "input_image", "file_id": "file-xyz"},
            ],
        }
    ]
    await agent_client.ask_agent(contents, chat_id=51)

    history = agent_client._histories[51]
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "[user sent a photo]"


@pytest.mark.asyncio
async def test_mcp_reconnect_on_runner_failure(monkeypatch):
    """ask_agent should reconnect MCP and retry once if Runner.run fails."""
    monkeypatch.setenv("MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client, "MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client.MCPServerSse, "connect", AsyncMock())
    await agent_client.create_thread_with_system_prompt("sys", bot_name="bot")

    # First call fails, second succeeds (simulates reconnect recovery)
    fake_result = Mock(final_output="recovered")
    call_count = 0

    async def run_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("SSE stream dead")
        return fake_result

    monkeypatch.setattr(agent_client.Runner, "run", run_side_effect)
    cleanup_mock = AsyncMock()
    connect_mock = AsyncMock()
    monkeypatch.setattr(agent_client._mcp_server, "cleanup", cleanup_mock)
    monkeypatch.setattr(agent_client._mcp_server, "connect", connect_mock)

    reply = await agent_client.ask_agent(
        [{"role": "user", "content": "hello"}], chat_id=60
    )

    assert reply == "recovered"
    assert call_count == 2
    cleanup_mock.assert_awaited_once()
    connect_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_reconnect_preserves_history_on_retry(monkeypatch):
    """History should be correct after a reconnect-retry cycle."""
    monkeypatch.setenv("MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client, "MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client.MCPServerSse, "connect", AsyncMock())
    await agent_client.create_thread_with_system_prompt("sys", bot_name="bot")

    call_count = 0

    async def run_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("dead")
        return Mock(final_output="ok")

    monkeypatch.setattr(agent_client.Runner, "run", run_side_effect)
    monkeypatch.setattr(agent_client._mcp_server, "cleanup", AsyncMock())
    monkeypatch.setattr(agent_client._mcp_server, "connect", AsyncMock())

    await agent_client.ask_agent(
        [{"role": "user", "content": "test msg"}], chat_id=61
    )

    history = agent_client._histories[61]
    # User message + assistant reply, no duplicates from retry
    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "test msg"}
    assert history[1] == {"role": "assistant", "content": "ok"}


@pytest.mark.asyncio
async def test_mcp_reconnect_raises_on_second_failure(monkeypatch):
    """If retry also fails, the error should propagate."""
    monkeypatch.setenv("MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client, "MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client.MCPServerSse, "connect", AsyncMock())
    await agent_client.create_thread_with_system_prompt("sys", bot_name="bot")

    monkeypatch.setattr(
        agent_client.Runner, "run", AsyncMock(side_effect=ConnectionError("still dead"))
    )
    monkeypatch.setattr(agent_client._mcp_server, "cleanup", AsyncMock())
    monkeypatch.setattr(agent_client._mcp_server, "connect", AsyncMock())

    with pytest.raises(ConnectionError, match="still dead"):
        await agent_client.ask_agent(
            [{"role": "user", "content": "hello"}], chat_id=62
        )


@pytest.mark.asyncio
async def test_mcp_cleanup_failure_does_not_block_reconnect(monkeypatch):
    """If cleanup raises, connect should still be attempted."""
    monkeypatch.setenv("MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client, "MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client.MCPServerSse, "connect", AsyncMock())
    await agent_client.create_thread_with_system_prompt("sys", bot_name="bot")

    call_count = 0

    async def run_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("dead")
        return Mock(final_output="ok")

    monkeypatch.setattr(agent_client.Runner, "run", run_side_effect)
    monkeypatch.setattr(
        agent_client._mcp_server, "cleanup", AsyncMock(side_effect=RuntimeError("cleanup boom"))
    )
    connect_mock = AsyncMock()
    monkeypatch.setattr(agent_client._mcp_server, "connect", connect_mock)

    reply = await agent_client.ask_agent(
        [{"role": "user", "content": "hi"}], chat_id=63
    )

    assert reply == "ok"
    connect_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_server_timeout_params(monkeypatch):
    """MCPServerSse should be created with extended timeouts."""
    monkeypatch.setenv("MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client, "MCP_SERVER_URL", "http://example.com/sse")
    monkeypatch.setattr(agent_client.MCPServerSse, "connect", AsyncMock())
    await agent_client.create_thread_with_system_prompt("sys", bot_name="bot")

    server = agent_client._mcp_server
    assert server.params["sse_read_timeout"] == 60 * 30
    assert server.client_session_timeout_seconds == 30
