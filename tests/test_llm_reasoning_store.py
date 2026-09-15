"""Reasoning archive isolation, protocol capture and privileged read regressions."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import llm_protocol, llm_reasoning_store as store


def mc(protocol="chat_completions", **kwargs):
    return NS(name="fixture-preset", model="fixture-model", api_protocol=protocol,
              provider_kind="openai", tool_call_mode="function_calling", **kwargs)


def parts():
    return store.query(call_id=store.query()[0]["call_id"])["parts"]


async def test_chat_completion_capture_keeps_native_and_inline_separate():
    response = NS(choices=[NS(finish_reason="stop", message=NS(
        content="<think>inline private</think>visible", reasoning_content="native private",
        tool_calls=[],
    ))])
    model = mc(client=NS(chat=NS(completions=NS(create=AsyncMock(return_value=response)))))
    result = await llm_protocol.create(model, [], gen_kwargs={})
    assert result.assistant_text.endswith("visible")
    assert parts() == [
        {"source": "reasoning_content", "text": "native private"},
        {"source": "inline_think", "text": "inline private"},
    ]
    assert "parts" not in store.query()[0]
    assert "visible" not in str(parts())


async def test_archive_failure_does_not_break_generation(monkeypatch, caplog):
    def fail(*args):
        raise OSError("private content must not be logged")
    monkeypatch.setattr(store, "_append", fail)
    capture = store.Capture(mc())
    capture.add("thinking", "private")
    await capture.save()
    assert "write_failed" in caplog.text
    assert "private" not in caplog.text


async def test_empty_and_missing_reads_do_not_create_database(sandbox):
    assert store.query() == []
    assert store.query(call_id="missing") is None
    await store.Capture(mc()).save()
    assert not sandbox.llm_reasoning_db().exists()


async def test_concurrent_writes_and_stable_pagination():
    async def write(i):
        capture = store.Capture(mc())
        capture.add("thinking", str(i))
        await capture.save()
    await asyncio.gather(*(write(i) for i in range(8)))
    first = store.query(limit=3)
    rest = store.query(before=first[-1]["seq"])
    assert len(first + rest) == 8
    assert len({r["call_id"] for r in first + rest}) == 8
    assert store.query(model="other") == []
    assert store.query(call_id="' OR 1=1 --") is None


async def test_interrupted_stream_retains_native_and_split_inline():
    async def chunks():
        for text, reasoning in [("<thi", "native"), ("nk>partial", None)]:
            yield NS(choices=[NS(delta=NS(content=text, reasoning_content=reasoning))])
        raise RuntimeError("stream interrupted")
    model = mc(client=NS(chat=NS(completions=NS(create=AsyncMock(return_value=chunks())))))
    with pytest.raises(RuntimeError):
        async for _ in llm_protocol.stream_text(model, [], gen_kwargs={}):
            pass
    assert store.query()[0]["status"] == "interrupted"
    assert parts() == [
        {"source": "reasoning_content", "text": "native"},
        {"source": "inline_think", "text": "partial"},
    ]


async def test_explicit_stream_close_retains_received_reasoning():
    async def chunks():
        yield NS(choices=[NS(delta=NS(content="visible", reasoning_content="received"))])
        await asyncio.sleep(10)
    model = mc(client=NS(chat=NS(completions=NS(create=AsyncMock(return_value=chunks())))))
    stream = llm_protocol.stream_text(model, [], gen_kwargs={})
    assert await anext(stream) == "visible"
    await stream.aclose()
    assert parts()[0]["text"] == "received"
    assert store.query()[0]["status"] == "interrupted"


async def test_responses_stream_summary_is_not_duplicated():
    final = NS(status="completed", output=[
        NS(type="reasoning", summary=[NS(text="summary")]),
        NS(type="message", content=[NS(type="output_text", text="visible")]),
    ])
    async def events():
        yield NS(type="response.reasoning_summary_text.delta", delta="summary")
        yield NS(type="response.completed", response=final)
    model = mc("responses", client=NS(responses=NS(create=AsyncMock(return_value=events()))))
    assert "".join([p async for p in llm_protocol.stream_text(model, [], gen_kwargs={})]) == "visible"
    assert parts() == [{"source": "reasoning_summary", "text": "summary"}]


@pytest.mark.parametrize("streaming", [False, True])
async def test_anthropic_thinking_capture(streaming):
    payload = {"stop_reason": "end_turn", "content": [
        {"type": "thinking", "thinking": "private", "signature": "not-stored"},
        {"type": "text", "text": "visible"},
    ]}
    import json
    events = [
        {"type": "content_block_start", "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "private"}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "visible"}},
        {"type": "message_stop"},
    ]
    def respond(request):
        if streaming:
            return httpx.Response(200, text="\n\n".join("data: " + json.dumps(e) for e in events))
        return httpx.Response(200, json=payload)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = mc("anthropic_messages", client=client, base_url="https://example.invalid",
                   api_key="test-only", anthropic_auth_mode="x_api_key")
        if streaming:
            assert "".join([p async for p in llm_protocol.stream_text(model, [{"role": "user", "content": "hi"}], gen_kwargs={})]) == "visible"
        else:
            assert (await llm_protocol.create(model, [{"role": "user", "content": "hi"}], gen_kwargs={})).assistant_text == "visible"
    assert parts() == [{"source": "thinking", "text": "private"}]


async def test_read_api_content_is_admin_only(monkeypatch):
    from admin.routers.observability import router
    from admin.auth import TokenInfo
    import admin.auth as auth
    capture = store.Capture(mc())
    capture.add("thinking", "private")
    await capture.save()
    # Exercise the real scope dependency; token resolution alone is stubbed.
    monkeypatch.setattr(auth, "resolve_token", lambda token: TokenInfo(
        label=token, scopes=frozenset({"admin"} if token == "admin" else {"memory.read"}),
    ))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        url = "/observability/llm-reasoning"
        assert client.get(url, headers={"Authorization": "Bearer reader"}).status_code == 403
        assert client.get(url + "/" + capture.call_id,
                          headers={"Authorization": "Bearer reader"}).status_code == 403
        response = client.get(url, headers={"Authorization": "Bearer admin"})
        assert response.status_code == 200
        assert "private" not in response.text
        response = client.get(url + "/" + capture.call_id, headers={"Authorization": "Bearer admin"})
        assert response.status_code == 200
        assert response.json()["parts"][0]["text"] == "private"


@pytest.mark.parametrize("chunk_size", [1, 2, 5, 100])
async def test_inline_stream_split_tags_never_reach_visible_reply(monkeypatch, chunk_size):
    from core import llm_client
    raw = "before<thinking>private A</thinking>after<THINK>private B</THINK>done<think>partial"
    async def chunks():
        for i in range(0, len(raw), chunk_size):
            yield NS(choices=[NS(delta=NS(content=raw[i:i + chunk_size]))])
    model = mc(client=NS(chat=NS(completions=NS(create=AsyncMock(return_value=chunks())))),
               prompt_style="narrative", params={})
    monkeypatch.setattr(llm_client, "get_model_client", lambda *a, **kw: model)
    monkeypatch.setattr(llm_client.thinking, "maybe_apply", AsyncMock(return_value=[]))
    assert "".join([p async for p in llm_client.chat_stream([])]) == "beforeafterdone"
    assert [p["text"] for p in parts()] == ["private A", "private B", "partial"]


async def test_monologue_stream_does_not_join_owner_turn(monkeypatch):
    from core import llm_client

    async def chunks():
        yield NS(choices=[NS(delta=NS(content="visible", reasoning_content="monologue private"))])

    model = mc(client=NS(chat=NS(completions=NS(create=AsyncMock(return_value=chunks())))),
               prompt_style="narrative", params={})
    monkeypatch.setattr(llm_client, "get_model_client", lambda *a, **kw: model)
    monkeypatch.setattr(llm_client.thinking, "maybe_apply", AsyncMock(return_value=[]))

    @store.associate_owner_turn
    async def run(message, provenance_channel):
        async for _ in llm_client.chat_stream([], call_category="monologue"):
            pass
        return {"turn_id": "owner"}

    await run("hi", "desktop")
    assert store.query_turn("owner") == []
    assert parts()[0]["text"] == "monologue private"


async def test_prefixed_monologue_joins_owner_turn_and_sorts():
    @store.associate_owner_turn
    async def run(message, provenance_channel):
        native = store.Capture(mc())
        native.add("reasoning_content", "native later")
        await native.save()
        await store.archive_text("monologue", "inner voice")
        return {"turn_id": "owner"}

    await run("hi", "desktop")
    preferred = store.query_turn("owner")
    assert [entry["parts"][0]["text"] for entry in preferred] == ["inner voice", "native later"]
    native_first = store.query_turn("owner", prefer_monologue=False)
    assert [entry["parts"][0]["text"] for entry in native_first] == ["native later", "inner voice"]


async def test_empty_monologue_is_not_archived():
    await store.archive_text("monologue", "   ")
    assert store.query() == []


async def test_tool_continuation_strips_nested_inline_reasoning(monkeypatch):
    from core import llm_client
    result = llm_protocol.NormalizedResponse(
        assistant_text="<think>private</think>visible", tool_calls=[], status="completed",
        usage=None, raw_response=None,
        continuation_items=[{"role": "assistant", "content": [
            {"type": "text", "text": "<think>private</think>visible"},
        ]}],
    )
    model = mc()
    monkeypatch.setattr(llm_client, "_prepare_call", lambda *a, **kw: (model, [], {}))
    monkeypatch.setattr(llm_client, "create_protocol_response", AsyncMock(return_value=result))
    monkeypatch.setattr(llm_client, "_record_api_call", lambda **kw: None)
    turn = await llm_client.chat_turn([], [])
    assert turn.content == "visible"
    assert "private" not in str(turn.continuation_items)
