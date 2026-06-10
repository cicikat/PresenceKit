"""
tests/test_r4_prompt_layer_boundary.py
=======================================
Fable R4-A: PromptLayer structure and LLM API boundary sanitization.

Coverage:
  1. PromptLayer constructs correctly and converts to a message dict.
  2. _layer is stripped by sanitize_messages (never reaches the LLM).
  3. Any _internal key is stripped (generic underscore-prefix rule).
  4. sanitize_messages does not mutate the original list or dicts.
  5. Standard fields role/content are preserved after sanitization.
  6. _layer can exist in pre-LLM messages but must be absent in the
     dict that chat() actually sends to the API.
  7. drop_priority / other metadata fields do not leak to the vendor.
  8. Existing llm_client.chat() call signature does not regress
     (smoke test: call path reachable with mocked API).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.prompt_layer import (
    PromptLayer,
    prompt_layer_to_message,
    sanitize_messages,
)

# ---------------------------------------------------------------------------
# 1. PromptLayer construction + prompt_layer_to_message
# ---------------------------------------------------------------------------

class TestPromptLayerConstruction:
    def test_required_fields(self):
        layer = PromptLayer(name="6c_episodic", content="some memory text")
        assert layer.name == "6c_episodic"
        assert layer.content == "some memory text"

    def test_defaults(self):
        layer = PromptLayer(name="x", content="y")
        assert layer.role == "system"
        assert layer.drop_priority is None

    def test_custom_role(self):
        layer = PromptLayer(name="9_history", content="hi", role="user")
        assert layer.role == "user"

    def test_drop_priority(self):
        layer = PromptLayer(name="6b_event_search", content="...", drop_priority=10)
        assert layer.drop_priority == 10

    def test_frozen(self):
        layer = PromptLayer(name="x", content="y")
        with pytest.raises((AttributeError, TypeError)):
            layer.name = "z"  # type: ignore[misc]

    def test_to_message_has_layer_key(self):
        layer = PromptLayer(name="mid_term", content="some context")
        msg = prompt_layer_to_message(layer)
        assert msg["_layer"] == "mid_term"
        assert msg["role"] == "system"
        assert msg["content"] == "some context"

    def test_to_message_includes_drop_priority_for_trimmer(self):
        # R4-B: _drop_priority IS included in the internal message dict so the
        # trimmer can read it.  sanitize_messages() strips it before the API call.
        layer = PromptLayer(name="x", content="y", drop_priority=5)
        msg = prompt_layer_to_message(layer)
        assert msg["_drop_priority"] == 5
        assert "drop_priority" not in msg  # public (non-underscore) key must not exist

    def test_to_message_omits_drop_priority_when_none(self):
        layer = PromptLayer(name="x", content="y")  # drop_priority=None default
        msg = prompt_layer_to_message(layer)
        assert "_drop_priority" not in msg


# ---------------------------------------------------------------------------
# 2 + 3. sanitize_messages strips _layer and any _internal key
# ---------------------------------------------------------------------------

class TestSanitizeMessages:
    def test_strips_layer(self):
        msgs = [{"role": "system", "content": "hello", "_layer": "1_system_prompt"}]
        result = sanitize_messages(msgs)
        assert "_layer" not in result[0]

    def test_strips_debug(self):
        msgs = [{"role": "user", "content": "hi", "_debug": True}]
        result = sanitize_messages(msgs)
        assert "_debug" not in result[0]

    def test_strips_drop_priority(self):
        msgs = [{"role": "system", "content": "...", "_drop_priority": 3}]
        result = sanitize_messages(msgs)
        assert "_drop_priority" not in result[0]

    def test_strips_any_underscore_key(self):
        msgs = [{"role": "system", "content": "x", "_foo": 1, "_bar": 2}]
        result = sanitize_messages(msgs)
        for key in result[0]:
            assert not key.startswith("_"), f"underscore key leaked: {key!r}"

    def test_multiple_messages(self):
        msgs = [
            {"role": "system", "content": "sys", "_layer": "1_system"},
            {"role": "user", "content": "user msg", "_layer": "12_user_message"},
        ]
        result = sanitize_messages(msgs)
        assert len(result) == 2
        for m in result:
            assert "_layer" not in m

    # 4. Does not mutate originals
    def test_does_not_mutate_original_list(self):
        msgs = [{"role": "system", "content": "x", "_layer": "test"}]
        original_id = id(msgs)
        result = sanitize_messages(msgs)
        assert id(result) != original_id
        assert "_layer" in msgs[0], "original dict was mutated"

    def test_does_not_mutate_original_dicts(self):
        original = {"role": "system", "content": "x", "_layer": "test"}
        msgs = [original]
        sanitize_messages(msgs)
        assert "_layer" in original, "original dict was mutated"

    def test_new_dict_objects(self):
        original_dict = {"role": "system", "content": "x", "_layer": "test"}
        msgs = [original_dict]
        result = sanitize_messages(msgs)
        assert result[0] is not original_dict

    # 5. Standard fields preserved
    def test_preserves_role_and_content(self):
        msgs = [{"role": "assistant", "content": "reply text", "_layer": "9_history"}]
        result = sanitize_messages(msgs)
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "reply text"

    def test_preserves_tool_calls(self):
        msgs = [{"role": "assistant", "content": "", "tool_calls": [{"id": "x"}], "_layer": "x"}]
        result = sanitize_messages(msgs)
        assert "tool_calls" in result[0]

    def test_empty_list(self):
        assert sanitize_messages([]) == []


# ---------------------------------------------------------------------------
# 6. _layer present pre-LLM but absent in API call (via llm_client.chat)
# ---------------------------------------------------------------------------

class TestLLMBoundaryStrip:
    """Verify that when chat() is called with _layer fields, the mocked API
    never sees them."""

    def _make_mock_response(self, text: str):
        choice = MagicMock()
        choice.finish_reason = "stop"
        choice.message.content = text
        choice.message.tool_calls = None
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    def test_layer_stripped_before_api(self):
        captured: list[dict] = []

        async def fake_create(**kwargs):
            captured.extend(kwargs.get("messages", []))
            return self._make_mock_response("ok")

        messages_with_layer = [
            {"role": "system", "content": "sys prompt", "_layer": "1_system_prompt"},
            {"role": "user", "content": "hello", "_layer": "12_user_message"},
        ]

        with patch("core.llm_client._get_client") as mock_get_client, \
             patch("core.llm_client.get_config") as mock_cfg:
            mock_cfg.return_value = {
                "llm": {
                    "api_key": "test",
                    "base_url": "http://localhost",
                    "model": "test-model",
                    "tool_call_mode": "plain",
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "max_tokens": 100,
                    "frequency_penalty": 0.0,
                }
            }
            client_mock = MagicMock()
            client_mock.chat.completions.create = AsyncMock(side_effect=fake_create)
            mock_get_client.return_value = client_mock

            from core.llm_client import chat
            asyncio.get_event_loop().run_until_complete(chat(messages_with_layer))

        for m in captured:
            assert "_layer" not in m, f"_layer leaked to API: {m}"

    def test_internal_debug_stripped_before_api(self):
        captured: list[dict] = []

        async def fake_create(**kwargs):
            captured.extend(kwargs.get("messages", []))
            return self._make_mock_response("ok")

        messages_with_internal = [
            {"role": "system", "content": "sys", "_layer": "1", "_debug": "verbose"},
        ]

        with patch("core.llm_client._get_client") as mock_get_client, \
             patch("core.llm_client.get_config") as mock_cfg:
            mock_cfg.return_value = {
                "llm": {
                    "api_key": "test",
                    "base_url": "http://localhost",
                    "model": "test-model",
                    "tool_call_mode": "plain",
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "max_tokens": 100,
                    "frequency_penalty": 0.0,
                }
            }
            client_mock = MagicMock()
            client_mock.chat.completions.create = AsyncMock(side_effect=fake_create)
            mock_get_client.return_value = client_mock

            from core.llm_client import chat
            asyncio.get_event_loop().run_until_complete(chat(messages_with_internal))

        for m in captured:
            for key in m:
                assert not key.startswith("_"), f"internal key {key!r} leaked to API"

    # 7. drop_priority / metadata does not leak
    def test_metadata_does_not_leak(self):
        captured: list[dict] = []

        async def fake_create(**kwargs):
            captured.extend(kwargs.get("messages", []))
            return self._make_mock_response("ok")

        messages_with_meta = [
            {
                "role": "system",
                "content": "content",
                "_layer": "5.5_lore",
                "_drop_priority": 8,
                "_token_estimate": 123,
            },
        ]

        with patch("core.llm_client._get_client") as mock_get_client, \
             patch("core.llm_client.get_config") as mock_cfg:
            mock_cfg.return_value = {
                "llm": {
                    "api_key": "test",
                    "base_url": "http://localhost",
                    "model": "test-model",
                    "tool_call_mode": "plain",
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "max_tokens": 100,
                    "frequency_penalty": 0.0,
                }
            }
            client_mock = MagicMock()
            client_mock.chat.completions.create = AsyncMock(side_effect=fake_create)
            mock_get_client.return_value = client_mock

            from core.llm_client import chat
            asyncio.get_event_loop().run_until_complete(chat(messages_with_meta))

        for m in captured:
            for key in m:
                assert not key.startswith("_"), f"metadata key {key!r} leaked to API"

    # 8. Smoke: original messages list is not mutated by chat()
    def test_chat_does_not_mutate_original_messages(self):
        original = [
            {"role": "system", "content": "sys", "_layer": "1_system_prompt"},
            {"role": "user", "content": "hi", "_layer": "12_user_message"},
        ]
        originals_copy = [dict(m) for m in original]

        async def fake_create(**kwargs):
            return self._make_mock_response("reply")

        with patch("core.llm_client._get_client") as mock_get_client, \
             patch("core.llm_client.get_config") as mock_cfg:
            mock_cfg.return_value = {
                "llm": {
                    "api_key": "test",
                    "base_url": "http://localhost",
                    "model": "test-model",
                    "tool_call_mode": "plain",
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "max_tokens": 100,
                    "frequency_penalty": 0.0,
                }
            }
            client_mock = MagicMock()
            client_mock.chat.completions.create = AsyncMock(side_effect=fake_create)
            mock_get_client.return_value = client_mock

            from core.llm_client import chat
            asyncio.get_event_loop().run_until_complete(chat(original))

        for i, (orig, saved) in enumerate(zip(original, originals_copy)):
            assert orig == saved, f"message[{i}] was mutated by chat()"
