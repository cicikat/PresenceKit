"""Regression coverage for malformed OpenAI-compatible chat responses."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


def _model_client(response, *, tool_call_mode: str = "function_calling"):
    create = AsyncMock(return_value=response)
    return SimpleNamespace(
        name="test-preset",
        provider_kind="openai",
        model="test-model",
        tool_call_mode=tool_call_mode,
        prompt_style="narrative",
        params={},
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        ),
    )


def test_chat_turn_rejects_a_string_gateway_response_and_records_failure(monkeypatch):
    import core.llm_client as llm_client

    model_client = _model_client("not a chat completion")
    monkeypatch.setattr(llm_client, "_prepare_call", lambda *_args, **_kwargs: (model_client, [], {}))
    failures = []
    monkeypatch.setattr(llm_client, "_record_api_call", lambda **kwargs: failures.append(kwargs))
    monkeypatch.setattr(llm_client, "log_error", lambda *_args: None)

    with pytest.raises(llm_client.UpstreamResponseFormatError, match="expected an OpenAI ChatCompletion"):
        asyncio.run(llm_client.chat_turn([], []))

    assert len(failures) == 1
    assert failures[0]["provider"] == "openai"
    assert failures[0]["model"] == "test-model"
    assert failures[0]["purpose"] == "chat"
    assert isinstance(failures[0]["started_at"], float)
    assert failures[0]["ok"] is False
    assert failures[0]["output_hint"] == "UpstreamResponseFormatError"


def test_regular_chat_rejects_the_same_malformed_response(monkeypatch):
    import core.llm_client as llm_client

    model_client = _model_client("not a chat completion")

    async def passthrough(messages, **_kwargs):
        return messages

    monkeypatch.setattr(llm_client, "get_model_client", lambda *_args, **_kwargs: model_client)
    monkeypatch.setattr(llm_client.thinking, "maybe_apply", passthrough)
    monkeypatch.setattr(llm_client, "_record_api_call", lambda **_kwargs: None)
    monkeypatch.setattr(llm_client, "log_error", lambda *_args: None)

    with pytest.raises(llm_client.UpstreamResponseFormatError, match="expected an OpenAI ChatCompletion"):
        asyncio.run(llm_client.chat([{"role": "user", "content": "hello"}]))


def test_desktop_chat_maps_an_incompatible_gateway_response_to_502(monkeypatch):
    import admin.routers.chat as chat
    from core.llm_client import UpstreamResponseFormatError

    monkeypatch.setattr(chat, "_check_reality_not_in_dream", lambda _uid: None)
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"scheduler": {"owner_id": "owner"}})

    async def fail_turn(*_args, **_kwargs):
        raise UpstreamResponseFormatError("bad completion")

    monkeypatch.setattr(chat, "run_owner_chat_turn", fail_turn)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(chat.desktop_chat({"message": "hello"}, _auth="dummy"))

    assert exc.value.status_code == 502
    assert "API 协议" in exc.value.detail


def test_desktop_chat_preserves_model_not_found_as_404(monkeypatch):
    import admin.routers.chat as chat
    from core.llm_client import UpstreamResponseFormatError

    monkeypatch.setattr(chat, "_check_reality_not_in_dream", lambda _uid: None)
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"scheduler": {"owner_id": "owner"}})

    async def fail_turn(*_args, **_kwargs):
        raise UpstreamResponseFormatError(
            "upstream model missing", http_status=404, category="upstream_not_found",
        )

    monkeypatch.setattr(chat, "run_owner_chat_turn", fail_turn)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(chat.desktop_chat({"message": "hello"}, _auth="dummy"))

    assert exc.value.status_code == 404
    assert "模型" in exc.value.detail


@pytest.mark.parametrize("raw_text,reasoning,expected", [
    ("", "private reasoning", "raw_text_chars=0 cleaned_text_chars=0 reasoning_chars=17"),
    ("<think>private</think>", "", "raw_text_chars=22 cleaned_text_chars=0 reasoning_chars=0"),
])
def test_empty_response_diagnostics_distinguish_upstream_and_cleanup(monkeypatch, caplog, raw_text, reasoning, expected):
    import core.llm_client as llm_client

    message = SimpleNamespace(content=raw_text, reasoning_content=reasoning, tool_calls=[])
    response = SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])
    model_client = _model_client(response)

    async def passthrough(messages, **_kwargs):
        return messages

    monkeypatch.setattr(llm_client, "get_model_client", lambda *_args, **_kwargs: model_client)
    monkeypatch.setattr(llm_client.thinking, "maybe_apply", passthrough)
    monkeypatch.setattr(llm_client, "_record_api_call", lambda **_kwargs: None)
    assert asyncio.run(llm_client.chat([{"role": "user", "content": "hello"}])) == ""
    assert expected in caplog.text
    assert "private" not in caplog.text
