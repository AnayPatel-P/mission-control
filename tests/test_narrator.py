import asyncio
import json

import httpx
import pytest

from mission_control.narrator import Narrator

pytestmark = pytest.mark.asyncio


def ev(n, note="Claude said this"):
    return {
        "type": "edit_result", "session_id": "s1", "agent_id": "a", "tool_use_id": f"t{n}",
        "file_path": "/p/app.py", "kind": "Edit", "note": note,
        "patch": [{"lines": [" ctx", "-old", "+new"]}],
    }


async def run(narrator, events):
    for e in events:
        narrator.submit(e)
    await narrator.drain()


def make(handler, **kw):
    out = []

    async def post(b):
        out.append(b)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Narrator(post, client=client, **kw), out, client


def ollama_reply(text):
    return httpx.Response(200, json={"message": {"content": text}})


async def test_ollama_bubble():
    seen = []

    async def handler(req):
        seen.append(req)
        return ollama_reply("It now saves edits.")

    n, out, c = make(handler, backend="ollama")
    await run(n, [ev(1)])
    await c.aclose()
    assert out == [{"type": "bubble", "session_id": "s1", "agent_id": "a", "tool_use_id": "t1",
                    "text": "It now saves edits.", "source": "model"}]
    assert seen[0].url.path == "/api/chat"
    body = json.loads(seen[0].content)
    assert body["model"] == "qwen3-coder:30b" and body["stream"] is False
    assert "+new" in body["messages"][-1]["content"]


async def test_fallback_to_note():
    async def handler(req):
        raise httpx.ConnectError("down")

    n, out, c = make(handler, backend="ollama")
    await run(n, [ev(1)])
    await run(n, [ev(2, note="")])
    await c.aclose()
    assert [(b["text"], b["source"]) for b in out] == [("Claude said this", "note")]


async def test_latest_only():
    gate = asyncio.Event()
    calls = []

    async def handler(req):
        calls.append(req)
        if len(calls) == 1:
            await gate.wait()
        return ollama_reply("ok.")

    n, out, c = make(handler, backend="ollama")
    n.submit(ev(1))
    await asyncio.sleep(0.05)
    for i in range(2, 6):
        n.submit(ev(i))
    gate.set()
    await n.drain()
    await c.aclose()
    assert len(calls) == 2
    assert [b["tool_use_id"] for b in out] == ["t1", "t5"]


async def test_trims_to_two_sentences():
    async def handler(req):
        return ollama_reply("One. Two! Three? Four.")

    n, out, c = make(handler, backend="ollama")
    await run(n, [ev(1)])
    await c.aclose()
    assert out[0]["text"] == "One. Two!"


async def test_haiku_backend(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    seen = []

    async def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "Done."}]})

    n, out, c = make(handler, backend="haiku")
    await run(n, [ev(1)])
    await c.aclose()
    assert str(seen[0].url) == "https://api.anthropic.com/v1/messages"
    assert seen[0].headers["x-api-key"] == "k"
    assert seen[0].headers["anthropic-version"] == "2023-06-01"
    assert json.loads(seen[0].content)["model"] == "claude-haiku-5-5"
    assert out[0]["text"] == "Done." and out[0]["source"] == "model"
