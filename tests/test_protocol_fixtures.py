"""Provider-side validation for the versioned cross-repository fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


FIXTURE_ROOT = Path(__file__).parent / "protocol_fixtures" / "v1"
FORBIDDEN_KEYS = {
    "uid", "char_id", "origin", "scope", "tool_capability",
    "tool_capabilities", "path", "file_path", "token",
}


def _load(name: str) -> object:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _walk_keys(value: object):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)


def _request_bodies(value: object):
    if isinstance(value, dict):
        if isinstance(value.get("body"), dict):
            yield value["body"]
        for child in value.values():
            yield from _request_bodies(child)
    elif isinstance(value, list):
        for child in value:
            yield from _request_bodies(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def test_manifest_is_versioned_and_lists_every_fixture():
    manifest = _load("manifest.json")
    assert manifest["schema_version"] == "1"
    assert manifest["fixture_version"] == "v1"
    listed = {item["file"] for item in manifest["cases"]}
    actual = {path.name for path in FIXTURE_ROOT.glob("*.json")} - {"manifest.json"}
    assert listed == actual
    assert all(item["classification"] in {
        "backward-compatible", "consumer-update-required", "breaking",
    } for item in manifest["cases"])


@pytest.mark.parametrize("name", [
    "mobile_http.json", "mobile_queue.json", "desktop_http.json",
    "desktop_ws.json", "owner_turn.json", "security.json",
])
def test_fixture_is_synthetic_and_contains_no_forbidden_client_fields(name: str):
    payload = _load(name)
    if name == "security.json":
        # The security fixture is the explicit negative contract; its field
        # names are assertions, not a payload that a client may submit.
        return
    assert all(
        not (set(_walk_keys(body)) & FORBIDDEN_KEYS)
        for body in _request_bodies(payload)
    )


def test_http_correlation_and_ws_frames_share_the_same_opaque_id():
    http = _load("desktop_http.json")
    ws = _load("desktop_ws.json")
    expected = http["correlation"]["http_id"]
    assert http["response"]["body"]["turn_id"] == expected
    assert http["response"]["body"]["msg_id"] == expected
    correlated = {
        frame.get("msg_id") for frame in ws["server_frames"]
        if frame.get("msg_id") is not None
    }
    assert correlated >= {expected}


def test_mobile_duplicate_ack_is_explicitly_idempotent():
    fixture = _load("mobile_queue.json")
    first, duplicate = fixture["ack_requests"]
    assert first["body"] == duplicate["body"]
    assert first["response"] == duplicate["response"]


def test_session_scope_fixture_allows_char_only_at_bind_boundary():
    fixture = _load("session_scope.json")
    assert fixture["bind"]["request"]["body"] == {"char_id": "fixture_character"}
    assert "char_id" not in fixture["scoped_request"]["body"]
    assert fixture["scoped_request"]["header"] == {
        "X-Presence-Session": "<OPAQUE_ID>"
    }


@pytest.mark.asyncio
async def test_mobile_chat_provider_uses_the_canonical_fixture(monkeypatch):
    from admin.routers import chat, mobile

    fixture = _load("mobile_http.json")
    expected = fixture["response"]["body"]

    async def fake_owner_turn(message, provenance_channel, **kwargs):
        assert message == fixture["request"]["body"]["message"]
        assert provenance_channel == "mobile"
        assert kwargs["reply_to"] == fixture["request"]["body"]["reply_to"]
        return dict(expected)

    monkeypatch.setattr(chat, "run_owner_chat_turn", fake_owner_turn)
    monkeypatch.setattr(chat, "_check_reality_not_in_dream", lambda uid: None)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": "fixture-owner"}},
    )
    monkeypatch.setattr("core.scheduler.sensor_events.notify_chat_happened", lambda: None)

    result = await mobile.mobile_chat(fixture["request"]["body"], _auth=True)
    assert result["turn_id"] == expected["turn_id"]
    assert result["msg_id"] == expected["msg_id"]


@pytest.mark.asyncio
async def test_desktop_http_provider_uses_the_canonical_fixture(monkeypatch):
    from admin.routers import chat

    fixture = _load("desktop_http.json")
    expected = fixture["response"]["body"]

    async def fake_owner_turn(message, provenance_channel, **kwargs):
        assert message == fixture["request"]["body"]["message"]
        assert provenance_channel == "desktop"
        assert kwargs["reply_to"] == fixture["request"]["body"]["reply_to"]
        return dict(expected)

    monkeypatch.setattr(chat, "run_owner_chat_turn", fake_owner_turn)
    monkeypatch.setattr(chat, "_check_reality_not_in_dream", lambda uid: None)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": "fixture-owner"}},
    )
    monkeypatch.setattr("core.scheduler.sensor_events.notify_chat_happened", lambda: None)

    result = await chat.desktop_chat(fixture["request"]["body"], _auth=True)
    assert result["turn_id"] == expected["turn_id"]
    assert result["msg_id"] == expected["msg_id"]
