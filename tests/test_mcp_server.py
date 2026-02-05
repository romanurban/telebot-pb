import subprocess
import sys
import time
from datetime import datetime
import http.client
import json
import os

import anyio
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession


def test_mcp_server_responds():
    proc = subprocess.Popen([sys.executable, "mcp_server.py"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(10):
            time.sleep(0.5)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", 8888)
                conn.request("GET", "/sse")
                resp = conn.getresponse()
                content_type = resp.getheader("content-type")
                conn.close()
                assert resp.status == 200
                assert content_type and content_type.startswith("text/event-stream")
                break
            except Exception:
                continue
        else:
            raise AssertionError("server did not respond")

        result = anyio.run(_call_datetime_tool)
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    # ensure result contains an ISO datetime and a weekday
    iso_part, weekday_part = result.split(" ", 1)
    datetime.fromisoformat(iso_part)
    weekday = weekday_part.strip("()")
    assert weekday in [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]


def test_get_current_weather():
    proc = subprocess.Popen([sys.executable, "mcp_server.py"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(10):
            time.sleep(0.5)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", 8888)
                conn.request("GET", "/sse")
                resp = conn.getresponse()
                content_type = resp.getheader("content-type")
                conn.close()
                assert resp.status == 200
                assert content_type and content_type.startswith("text/event-stream")
                break
            except Exception:
                continue
        else:
            raise AssertionError("server did not respond")

        result = anyio.run(_call_weather_tool)
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    data = json.loads(result)
    assert "current" in data


def test_get_current_weather_cyrillic():
    proc = subprocess.Popen([sys.executable, "mcp_server.py"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(10):
            time.sleep(0.5)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", 8888)
                conn.request("GET", "/sse")
                resp = conn.getresponse()
                content_type = resp.getheader("content-type")
                conn.close()
                assert resp.status == 200
                assert content_type and content_type.startswith("text/event-stream")
                break
            except Exception:
                continue
        else:
            raise AssertionError("server did not respond")

        result = anyio.run(_call_weather_tool_cyrillic)
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    data = json.loads(result)
    assert "current" in data


def test_get_current_weather_with_country():
    proc = subprocess.Popen([sys.executable, "mcp_server.py"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(10):
            time.sleep(0.5)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", 8888)
                conn.request("GET", "/sse")
                resp = conn.getresponse()
                content_type = resp.getheader("content-type")
                conn.close()
                assert resp.status == 200
                assert content_type and content_type.startswith("text/event-stream")
                break
            except Exception:
                continue
        else:
            raise AssertionError("server did not respond")

        result = anyio.run(_call_weather_tool_with_country)
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    data = json.loads(result)
    assert "current" in data


def test_get_random_story():
    proc = subprocess.Popen([sys.executable, "mcp_server.py"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(10):
            time.sleep(0.5)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", 8888)
                conn.request("GET", "/sse")
                resp = conn.getresponse()
                content_type = resp.getheader("content-type")
                conn.close()
                assert resp.status == 200
                assert content_type and content_type.startswith("text/event-stream")
                break
            except Exception:
                continue
        else:
            raise AssertionError("server did not respond")

        result = anyio.run(_call_story_tool)
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    with open("dataset/neuroyury_bot/stories.txt", "r", encoding="utf-8") as f:
        content = f.read()
    entries = [e.strip() for e in content.split("---") if e.strip()]
    formatted = set()
    for entry in entries:
        lines = [line.strip() for line in entry.splitlines() if line.strip()]
        topic = lines[0]
        text = " ".join(lines[1:]) if len(lines) > 1 else ""
        formatted.add(f"{topic}: {text}".strip())

    assert result in formatted


def test_get_wikipedia_extract():
    proc = subprocess.Popen([sys.executable, "mcp_server.py"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(10):
            time.sleep(0.5)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", 8888)
                conn.request("GET", "/sse")
                resp = conn.getresponse()
                content_type = resp.getheader("content-type")
                conn.close()
                assert resp.status == 200
                assert content_type and content_type.startswith("text/event-stream")
                break
            except Exception:
                continue
        else:
            raise AssertionError("server did not respond")

        result = anyio.run(_call_wikipedia_tool)
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    assert "17 июня" in result


def test_get_random_proverb():
    proc = subprocess.Popen([sys.executable, "mcp_server.py"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(10):
            time.sleep(0.5)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", 8888)
                conn.request("GET", "/sse")
                resp = conn.getresponse()
                content_type = resp.getheader("content-type")
                conn.close()
                assert resp.status == 200
                assert content_type and content_type.startswith("text/event-stream")
                break
            except Exception:
                continue
        else:
            raise AssertionError("server did not respond")

        result = anyio.run(_call_proverb_tool)
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    assert ":" in result
    assert result.split(":", 1)[1].strip()


def test_get_picture_of_the_day():
    proc = subprocess.Popen([sys.executable, "mcp_server.py"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(10):
            time.sleep(0.5)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", 8888)
                conn.request("GET", "/sse")
                resp = conn.getresponse()
                content_type = resp.getheader("content-type")
                conn.close()
                assert resp.status == 200
                assert content_type and content_type.startswith("text/event-stream")
                break
            except Exception:
                continue
        else:
            raise AssertionError("server did not respond")

        result = anyio.run(_call_potd_tool)
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    data = json.loads(result)
    assert data.get("url")
    assert data.get("caption")


def test_retrieve_joke():
    proc = subprocess.Popen([sys.executable, "mcp_server.py"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(10):
            time.sleep(0.5)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", 8888)
                conn.request("GET", "/sse")
                resp = conn.getresponse()
                content_type = resp.getheader("content-type")
                conn.close()
                assert resp.status == 200
                assert content_type and content_type.startswith("text/event-stream")
                break
            except Exception:
                continue
        else:
            raise AssertionError("server did not respond")

        result = anyio.run(_call_joke_tool)
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    assert result.startswith("http")


async def _call_datetime_tool() -> str:
    async with sse_client("http://127.0.0.1:8888/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resp = await session.call_tool("get_current_datetime", {})
            assert resp.content
            assert resp.content[0].type == "text"
            return resp.content[0].text


async def _call_weather_tool() -> str:
    async with sse_client("http://127.0.0.1:8888/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resp = await session.call_tool("get_current_weather", {"location": "Riga"})
            assert resp.content
            assert resp.content[0].type == "text"
            return resp.content[0].text


async def _call_weather_tool_cyrillic() -> str:
    async with sse_client("http://127.0.0.1:8888/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resp = await session.call_tool("get_current_weather", {"location": "Рига"})
            assert resp.content
            assert resp.content[0].type == "text"
            return resp.content[0].text


async def _call_weather_tool_with_country() -> str:
    async with sse_client("http://127.0.0.1:8888/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resp = await session.call_tool("get_current_weather", {"location": "Рига, Латвия"})
            assert resp.content
            assert resp.content[0].type == "text"
            return resp.content[0].text


async def _call_story_tool() -> str:
    async with sse_client("http://127.0.0.1:8888/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resp = await session.call_tool("get_random_story", {})
            assert resp.content
            assert resp.content[0].type == "text"
            return resp.content[0].text


async def _call_wikipedia_tool() -> str:
    async with sse_client("http://127.0.0.1:8888/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resp = await session.call_tool("get_wikipedia_extract", {"date": "17 июня"})
            assert resp.content
            assert resp.content[0].type == "text"
            return resp.content[0].text


async def _call_proverb_tool() -> str:
    async with sse_client("http://127.0.0.1:8888/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resp = await session.call_tool("get_random_proverb", {})
            assert resp.content
            assert resp.content[0].type == "text"
            return resp.content[0].text


async def _call_potd_tool() -> str:
    async with sse_client("http://127.0.0.1:8888/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resp = await session.call_tool(
                "get_picture_of_the_day", {"date": "2025-06-20"}
            )
            assert resp.content
            assert resp.content[0].type == "text"
            return resp.content[0].text


async def _call_joke_tool() -> str:
    async with sse_client("http://127.0.0.1:8888/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resp = await session.call_tool("retrieve_joke", {})
            assert resp.content
            assert resp.content[0].type == "text"
            return resp.content[0].text
