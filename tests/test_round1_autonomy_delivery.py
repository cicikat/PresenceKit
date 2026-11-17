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
    assert payload["acknowledgement_available"] is True
    assert payload["acknowledgement_channels"]=={"mobile":"durable_client_inbox","desktop":"not_available"}
    assert payload["receipts"][0]["acknowledged"] is False
    assert payload["automatic_replay"] is False


@pytest.mark.parametrize("early_ack",[False,True])
async def test_mobile_ack_confirms_only_matching_autonomy_receipt(sandbox,monkeypatch,enabled_client_channels,early_ack):
    """Actual queue/HTTP ACK, including confirmation before fanout returns."""
    from channels import registry
    from channels.mobile import MobileChannel
    from core.autonomy import store,talk_gate
    from tests.test_turn_sink import _FakePipeline
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from admin.routers import mobile as mobile_router
    class Receiver(MobileChannel):
        async def send(self,*args,**kwargs):
            await super().send(*args,**kwargs)
            if early_ack:
                await self.ack(self._load_queue()[-1]["seq"])
    mobile=Receiver();mobile.set_active(True)
    monkeypatch.setattr(registry,"_channels",{"mobile":mobile})
    monkeypatch.setattr("channels.mobile.schedule_signal_publish",lambda *a,**k:None)
    monkeypatch.setattr("channels.fcm_publisher.schedule",lambda *a,**k:False)
    monkeypatch.setattr("channels.mobile._is_tts_available",lambda:False)
    monkeypatch.setattr("core.pipeline_registry.get",lambda:_FakePipeline())
    monkeypatch.setattr(talk_gate,"check",lambda *a,**k:("allow","ok"))
    monkeypatch.setattr("core.scheduler.proactive_ledger.record_send",lambda *a,**k:None)
    assert await talk_gate.send("owner","fixture_character","synthetic proactive",source="test",run_id="mobile-run")== (True,"sent")
    receipt=store.load("owner","fixture_character")["delivery_receipts"][-1]
    assert store.claim_delivery_correlation("owner","another-character","other-run")
    assert store.record_delivery("owner","another-character","other-run",status="accepted",turn_id=receipt["turn_id"],channels={"mobile":"accepted"})
    if not early_ack:
        assert receipt["acknowledged"] is False
        item=mobile._load_queue()[-1]
        monkeypatch.setattr(mobile_router,"_get_mobile_channel",lambda:mobile)
        app=FastAPI();app.include_router(mobile_router.router)
        route=next(row for row in app.routes if row.path=="/mobile/ack")
        app.dependency_overrides[route.dependant.dependencies[0].call]=lambda:"synthetic-mobile"
        with TestClient(app) as client:
            with monkeypatch.context() as fault:
                fault.setattr(store,"safe_write_json",lambda *a,**k:False)
                response=client.post("/mobile/ack",json={"ack_seq":item["seq"]})
                assert response.status_code==503
                assert len(mobile._load_queue())==1
                assert store.load("owner","fixture_character")["delivery_receipts"][-1]["acknowledged"] is False
            response=client.post("/mobile/ack",json={"ack_seq":item["seq"]})
        assert response.status_code==200 and response.json()["remaining"]==0
    receipt=store.load("owner","fixture_character")["delivery_receipts"][-1]
    assert receipt["acknowledged"] is True
    assert receipt["channels"]["mobile"]=="acknowledged"
    assert store.load("owner","another-character")["delivery_receipts"][-1]["acknowledged"] is False
    assert mobile._load_queue()==[]


def test_interrupted_delivery_is_explicit_unknown_after_process_restart(sandbox,tmp_path):
    import subprocess,sys,json
    from pathlib import Path
    script='''
import os,sys,json
from pathlib import Path
from core import sandbox
from core.autonomy import store
paths=sandbox.DataPaths(mode="test",test_session_id="isolated");paths._base=Path(sys.argv[1]);sandbox._instance=paths
if sys.argv[2]=="crash":
    assert store.claim_delivery_correlation("owner","fixture_character","synthetic-opportunity")
    assert store.record_delivery("owner","fixture_character","synthetic-opportunity",status="attempted",transport_id="synthetic-transport")
    os._exit(23)
else:
    row=store.load("owner","fixture_character")["delivery_receipts"][-1]
    print(json.dumps({"status":row["status"],"reason":row["reason_code"],"retry_claim":store.claim_delivery_correlation("owner","fixture_character","synthetic-opportunity")}))
'''
    for mode in ("crash","recover"):
        result=subprocess.run([sys.executable,"-X","utf8","-c",script,str(tmp_path),mode],
            cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,encoding="utf-8",timeout=20)
        assert result.returncode==(23 if mode=="crash" else 0),result.stderr
    assert json.loads(result.stdout.splitlines()[-1])=={"status":"outcome_unknown","reason":"process_interrupted","retry_claim":False}
