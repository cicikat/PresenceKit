"""Native generateContent contract through the real protocol entry points."""
import json
from types import SimpleNamespace

import httpx
import pytest

from core.llm_protocol import create, stream_text, UpstreamResponseFormatError


def model(handler, **overrides):
    return SimpleNamespace(name="native-fixture", provider_kind="gemini", model="fixture-model",
        api_protocol="gemini_generate_content", base_url="https://native.invalid/v1beta",
        api_key="synthetic-key", force_stream=False,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), **overrides)


def response(parts, finish="STOP"):
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2, "totalTokenCount": 5}}


async def test_native_text_system_and_usage(sandbox):
    def handler(request):
        assert request.url.path == "/v1beta/models/fixture-model:generateContent"
        assert request.headers["x-goog-api-key"] == "synthetic-key"
        assert "key=" not in str(request.url)
        payload = json.loads(request.content)
        assert payload["systemInstruction"]["parts"] == [{"text": "system"}]
        assert payload["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]
        assert payload["generationConfig"] == {"temperature": 0.2, "maxOutputTokens": 20}
        return httpx.Response(200, json=response([{"text": "hello back"}]))
    mc = model(handler)
    try:
        result = await create(mc, [{"role": "system", "content": "system"}, {"role": "user", "content": "hello"}],
            gen_kwargs={"temperature": 0.2, "max_tokens": 20})
        assert result.assistant_text == "hello back"
        assert result.usage["total_tokens"] == 5
    finally:
        await mc.client.aclose()


async def test_native_parallel_tools_preserve_signature_and_receipt_identity(sandbox):
    packets = []
    raw_parts = [{"functionCall": {"id": "first", "name": "lookup", "args": {"q": "one"}}, "thoughtSignature": "opaque-fixture"},
                 {"functionCall": {"id": "second", "name": "lookup", "args": {"q": "two"}}}]
    def handler(request):
        packets.append(json.loads(request.content))
        return httpx.Response(200, json=response(raw_parts if len(packets) == 1 else [{"text": "done"}]))
    mc = model(handler)
    history = [{"role": "user", "content": "lookup"}]
    try:
        first = await create(mc, history, tools=[{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}], gen_kwargs={})
        assert [(call.id, call.arguments) for call in first.tool_calls] == [("first", {"q": "one"}), ("second", {"q": "two"})]
        history += first.continuation_items
        history += [{"role": "tool", "tool_call_id": call.id, "content": "receipt"} for call in first.tool_calls]
        last = await create(mc, history, gen_kwargs={})
        assert last.assistant_text == "done"
        assert packets[1]["contents"][1]["parts"] == raw_parts
        assert [p["functionResponse"]["id"] for p in packets[1]["contents"][2]["parts"]] == ["first", "second"]
        with pytest.raises(UpstreamResponseFormatError):
            await create(mc, history + [{"role": "tool", "tool_call_id": "first", "content": "duplicate"}], gen_kwargs={})
        assert len(packets) == 2
    finally:
        await mc.client.aclose()


@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_native_http_errors_do_not_echo_body_or_credentials(sandbox, status):
    mc = model(lambda request: httpx.Response(status, json={"error": {"message": "synthetic-key private-body"}}))
    try:
        with pytest.raises(UpstreamResponseFormatError) as error:
            await create(mc, [{"role": "user", "content": "hello"}], gen_kwargs={})
        assert error.value.http_status == status
        assert "private-body" not in str(error.value) and "synthetic-key" not in str(error.value)
    finally:
        await mc.client.aclose()


@pytest.mark.parametrize("complete", [True, False])
async def test_native_stream_requires_finish_and_preserves_unicode(sandbox, complete):
    chunks = [response([{"text": "hello"}], finish=""), response([{"text": "世界"}], finish="STOP" if complete else "")]
    body = "".join("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n" for chunk in chunks)
    mc = model(lambda request: httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}))
    try:
        parts = []
        if complete:
            async for part in stream_text(mc, [{"role": "user", "content": "hello"}], gen_kwargs={}):
                parts.append(part)
            assert "".join(parts) == "hello世界"
        else:
            with pytest.raises(UpstreamResponseFormatError):
                async for part in stream_text(mc, [{"role": "user", "content": "hello"}], gen_kwargs={}):
                    parts.append(part)
    finally:
        await mc.client.aclose()


@pytest.mark.parametrize("payload", [{"promptFeedback": {"blockReason": "SAFETY"}}, {"candidates": []}, response([{"functionCall": {"name": "write", "args": {}}}], "MAX_TOKENS")])
async def test_native_blocked_empty_or_truncated_never_exposes_executable_calls(sandbox, payload):
    mc = model(lambda request: httpx.Response(200, json=payload))
    try:
        with pytest.raises(UpstreamResponseFormatError):
            await create(mc, [{"role": "user", "content": "hello"}], gen_kwargs={})
    finally:
        await mc.client.aclose()


async def test_native_streamed_tools_are_validated_before_return(sandbox):
    parts = [{"functionCall": {"id": "first", "name": "lookup", "args": {"q": "one"}}, "thoughtSignature": "opaque-fixture"},
             {"functionCall": {"id": "second", "name": "lookup", "args": {"q": "two"}}}]
    packets = [response([parts[0]], finish=""), response([parts[0]], finish=""), response([parts[1]])]
    mc = model(lambda request: httpx.Response(200, text="".join("data: " + json.dumps(p) + "\n\n" for p in packets)))
    mc.force_stream = True
    try:
        result = await create(mc, [{"role": "user", "content": "hello"}], gen_kwargs={})
        assert [call.id for call in result.tool_calls] == ["first", "second"]
        assert result.continuation_items[0]["gemini_content"]["parts"] == parts
    finally:
        await mc.client.aclose()


async def test_native_chat_turn_preserves_opaque_signature_through_sanitization(sandbox, monkeypatch):
    from core import llm_client
    from core.llm_failover import PreparedAttempt
    packets = []
    part = {"functionCall": {"id": "call", "name": "lookup", "args": {}}, "thoughtSignature": "opaque-fixture"}
    def handler(request):
        packets.append(json.loads(request.content))
        return httpx.Response(200, json=response([part] if len(packets) == 1 else [{"text": "done"}]))
    mc = model(handler)
    mc.tool_call_mode, mc.prompt_style, mc.params = "function_calling", "narrative", {}
    monkeypatch.setattr(llm_client, "_prepare_call", lambda messages, *a, **k: (mc, messages, {}))
    monkeypatch.setattr("core.llm_failover.resolve_fallback_client", lambda *a, **k: (None, "not_configured"))
    monkeypatch.setattr("core.thinking.build_reasoning_kwargs", lambda *a, **k: {})
    try:
        messages = [{"role": "user", "content": "hello"}]
        first = await llm_client.chat_turn(messages, [])
        last = await llm_client.chat_turn([*messages, *first.continuation_items,
            {"role": "tool", "tool_call_id": "call", "content": "receipt"}], [])
        assert last.content == "done"
        assert packets[1]["contents"][1]["parts"] == [part]
    finally:
        await mc.client.aclose()
