"""Brief 193: external companion runtime boundary tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from core.companion.models import (
    CompanionResponse,
    OpportunityRequest,
    PhoneMessageRequest,
)


def _fixture_root() -> Path:
    # Ordinary pytest must be self-contained. Cross-repository compatibility is
    # exercised separately by an explicit pinned checkout job.
    return Path(__file__).parent / "fixtures" / "external_companion_v1"


def _fixture(name: str) -> dict:
    return json.loads((_fixture_root() / name).read_text(encoding="utf-8"))


def test_companion_fixtures_validate_against_local_models():
    opportunity = OpportunityRequest.model_validate(_fixture("request.opportunity.json"))
    phone = PhoneMessageRequest.model_validate(_fixture("request.phone-message.json"))
    assert opportunity.summary
    assert phone.content
    assert CompanionResponse.model_validate(_fixture("response.reply.json")).reply is not None


@pytest.mark.asyncio
async def test_companion_store_is_metadata_only_and_duplicate_after_completion(sandbox):
    from core.companion import store

    await store.reserve(
        caller_label="companion-fixture",
        session_id="session-a",
        event_id="event-a",
        created_at="2026-08-16T00:00:00Z",
        kind="opportunity",
        digest="digest-a",
    )
    await store.complete(
        caller_label="companion-fixture",
        session_id="session-a",
        event_id="event-a",
        result_status="muted",
        reply_generated=False,
        latency_ms=3,
    )
    duplicate = await store.reserve(
        caller_label="companion-fixture",
        session_id="session-a",
        event_id="event-a",
        created_at="2026-08-16T00:00:00Z",
        kind="opportunity",
        digest="digest-a",
    )
    assert duplicate["status"] == "completed"
    raw = json.dumps(duplicate, ensure_ascii=False)
    assert "summary" not in raw
    assert "content" not in raw
    assert "bounded reply" not in raw


@pytest.mark.asyncio
async def test_companion_old_session_and_digest_conflict(sandbox):
    from core.companion import store

    await store.reserve(
        caller_label="companion-fixture",
        session_id="session-a",
        event_id="event-a",
        created_at="2026-08-16T00:00:00Z",
        kind="opportunity",
        digest="digest-a",
    )
    await store.complete(
        caller_label="companion-fixture",
        session_id="session-a",
        event_id="event-a",
        result_status="expired",
        reply_generated=False,
        latency_ms=1,
    )
    await store.reserve(
        caller_label="companion-fixture",
        session_id="session-b",
        event_id="event-b",
        created_at="2026-08-16T00:00:01Z",
        kind="opportunity",
        digest="digest-b",
    )
    with pytest.raises(store.CompanionSessionMismatch):
        await store.reserve(
            caller_label="companion-fixture",
            session_id="session-a",
            event_id="event-c",
            created_at="2026-08-16T00:00:00.500Z",
            kind="opportunity",
            digest="digest-c",
        )
    with pytest.raises(store.CompanionReceiptConflict):
        await store.reserve(
            caller_label="companion-fixture",
            session_id="session-b",
            event_id="event-b",
            created_at="2026-08-16T00:00:01Z",
            kind="opportunity",
            digest="different-digest",
        )


@pytest.mark.asyncio
async def test_companion_service_freezes_context_and_disables_tools_and_fanout(
    sandbox, monkeypatch
):
    from core.companion import service
    from core.memory.scope import MemoryScope
    from core.perceive_event import PerceiveResult, PerceiveStatus

    calls: list[dict] = []

    class FakePipeline:
        character = SimpleNamespace(name="Fixture Companion")

        def _current_reality_scope(self, uid):
            return MemoryScope.reality_scope(uid, "fixture-character")

    async def fake_owner_turn(message, channel, **kwargs):
        calls.append({"message": message, "channel": channel, **kwargs})
        return {"reply": "bounded reply", "turn_id": "turn-fixture"}

    async def fake_perceive(event):
        return PerceiveResult(
            status=PerceiveStatus.ACCEPTED,
            event_id=event.event_id or "event",
            dedupe_key="dedupe-fixture",
            char_id=event.char_id,
        )

    monkeypatch.setattr(service, "_INFLIGHT", set())
    monkeypatch.setattr(service, "_guard_allows", lambda uid: True)
    monkeypatch.setattr(service, "_is_proactive_disabled", lambda char_id: False)
    monkeypatch.setattr("core.pipeline_registry.get", lambda: FakePipeline())
    monkeypatch.setattr("core.owner_chat.service.run_owner_chat_turn", fake_owner_turn)
    monkeypatch.setattr("core.perceive_event.receive_perceive_event", fake_perceive)
    monkeypatch.setattr("core.perceive_event.record_perceive_result", lambda event, result: None)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": "fixture-owner"}},
    )

    fixture_request = _fixture("request.opportunity.json")
    fixture_request["created_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    request = OpportunityRequest.model_validate(fixture_request)
    response = await service.handle_event(request, caller_label="companion-fixture")
    assert response.status.value == "accepted"
    assert response.reply is not None
    assert calls[0]["channel"] == "companion"
    assert calls[0]["tool_execution_enabled"] is False
    assert calls[0]["fanout"] == []
    assert calls[0]["envelope"].can_write_memory is False
    assert calls[0]["provenance_source"] == "external_companion"
    assert calls[0]["audit_extras"]["user_authored"] is False
    assert calls[0]["audit_extras"]["provenance_origin"] == "external_companion"

    phone_fixture = _fixture("request.phone-message.json")
    phone_fixture["created_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    phone_request = PhoneMessageRequest.model_validate(phone_fixture)
    phone_response = await service.handle_event(phone_request, caller_label="companion-fixture")
    assert phone_response.status.value == "accepted"
    assert calls[1]["provenance_source"] == "companion_phone_input"
    assert calls[1]["audit_extras"]["user_authored"] is True
    assert calls[1]["audit_extras"]["provenance_origin"] == "companion_phone_input"
    assert calls[1]["envelope"].can_write_memory is True
    assert calls[1]["fanout"] == []


def test_companion_auth_scope_isolated_and_runtime_failure_has_no_receipt(sandbox, monkeypatch):
    import yaml

    from admin import token_registry

    raw = "emt_companion_fixture"
    path = sandbox.auth_tokens_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({
            "tokens": [{
                "label": "companion-fixture",
                "hash": token_registry.hash_token(raw),
                "scopes": ["profile:companion"],
            }],
        }),
        encoding="utf-8",
    )
    token_registry._records = None
    token_registry._mtime = None
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: "")

    from admin.admin_server import app

    client = TestClient(app)
    body = _fixture("request.opportunity.json")
    assert client.post("/integrations/companion/events", json=body).status_code == 401
    assert client.post(
        "/integrations/companion/events",
        json=body,
        headers={"Authorization": "Bearer " + raw},
    ).status_code == 503
    assert not sandbox.companion_receipts_root().exists()
