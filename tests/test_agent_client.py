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
