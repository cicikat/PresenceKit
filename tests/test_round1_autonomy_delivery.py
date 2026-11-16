"""R4: a claimed attempt survives failure without pretending client receipt."""
import asyncio
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("outcome", ["partial", "failed", "exception", "cancelled"])
async def test_delivery_receipt_distinguishes_acceptance_and_unknown(sandbox, monkeypatch, outcome):
    from core.autonomy import store, talk_gate
    monkeypatch.setattr(talk_gate, "check", lambda *a, **k: ("allow", "ok"))
    monkeypatch.setattr("core.pipeline_registry.get", lambda: object())
    monkeypatch.setattr("channels.registry.get_active", lambda: [object()])
    monkeypatch.setattr("core.scheduler.proactive_ledger.record_send", lambda *a, **k: None)
    calls = []

    async def sink(**kwargs):
        calls.append(kwargs)
        if outcome == "exception":
            raise OSError("private diagnostic must not enter receipts")
        if outcome == "cancelled":
            raise asyncio.CancelledError()
        return SimpleNamespace(turn_id="canonical-turn", written_to_memory=True,
            fanout_targets=["desktop"] if outcome == "partial" else [],
            fanout_failures={"mobile": "private upstream error"})

    monkeypatch.setattr("core.turn_sink.record_assistant_turn", sink)
    if outcome in {"exception", "cancelled"}:
        with pytest.raises(OSError if outcome == "exception" else asyncio.CancelledError):
            await talk_gate.send("owner", "fixture_character", "hello", source="test", run_id="run", correlation_id="opportunity")
    else:
        ok, _ = await talk_gate.send("owner", "fixture_character", "hello", source="test", run_id="run", correlation_id="opportunity")
        assert ok is (outcome == "partial")
    # Load persisted state, then retry the same opportunity as after a restart.
    receipt = store.load("owner", "fixture_character")["delivery_receipts"][-1]
    assert receipt["status"] == {"partial": "accepted", "failed": "failed"}.get(outcome, "outcome_unknown")
    assert receipt["acknowledged"] is False
    assert "private" not in str(receipt)
    if outcome in {"partial", "failed"}:
        assert receipt["persisted"] is True
        assert receipt["channels"]["mobile"] == "failed"
    assert await talk_gate.send("owner", "fixture_character", "hello", source="test", run_id="run", correlation_id="opportunity") == (False, "duplicate")
    assert len(calls) == 1


def test_receipt_observability_is_scoped_and_does_not_claim_ack(sandbox, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from admin.routers import autonomy
    from core.autonomy import store
    monkeypatch.setattr(autonomy, "_scope", lambda: ("owner", "fixture_character"))
    assert store.claim_delivery_correlation("owner", "fixture_character", "opportunity")
    assert store.claim_delivery_correlation("other", "other_character", "other-opportunity")
    app = FastAPI()
    app.include_router(autonomy.router)
    route = next(r for r in app.routes if r.path == "/observability/autonomy-deliveries")
    app.dependency_overrides[route.dependant.dependencies[0].call] = lambda: "test-auth"
    with TestClient(app) as client:
        response = client.get("/observability/autonomy-deliveries")
    assert response.status_code == 200
    payload = response.json()
    assert [row["correlation_id"] for row in payload["receipts"]] == ["opportunity"]
    assert payload["acknowledgement_available"] is False
    assert payload["automatic_replay"] is False
