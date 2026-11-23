"""F3: authenticated phone facts, separate consent, debounce and revocation."""
from collections import OrderedDict
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin import auth, token_registry
from admin.routers import sensor, chat, autonomy
from core import session_scope
from core.autonomy import store
from core.perception import ambient_state, phone_ambient


@pytest.fixture
def phone(sandbox, monkeypatch):
    clock = [time.time()]
    monkeypatch.setattr(phone_ambient.time, "time", lambda: clock[0])
    tokens = {"synthetic-mobile":"mobile", "synthetic-other":"mobile", "synthetic-desktop":"desktop", "synthetic-admin":"admin"}
    active = set(tokens)
    def resolve(raw):
        return auth.TokenInfo(raw, frozenset({"chat","sensor.write","state.read","persona"} | ({"admin"} if tokens[raw]=="admin" else set())), tokens[raw]) if raw in active else None
    def by_hash(digest):
        return next((SimpleNamespace(profiles={tokens[raw]},label=raw) for raw in active if token_registry.hash_token(raw)==digest), None)
    monkeypatch.setattr(auth,"resolve_token",resolve)
    monkeypatch.setattr(ambient_state,"find_by_hash",by_hash)
    monkeypatch.setattr(phone_ambient,"find_by_hash",by_hash)
    monkeypatch.setattr(ambient_state,"_devices",OrderedDict())
    monkeypatch.setattr(phone_ambient,"_detectors",OrderedDict())
    monkeypatch.setattr(phone_ambient,"_latest",{})
    monkeypatch.setattr(session_scope,"_SESSIONS",{})
    monkeypatch.setattr(session_scope,"_owner_id",lambda:"fixture-owner")
    monkeypatch.setattr(session_scope,"_validate_character",lambda value:value)
    monkeypatch.setattr('admin.session_requests.owner_media_scope',lambda:("fixture-owner","fixture-character"))
    monkeypatch.setattr(autonomy,"_scope",lambda:("fixture-owner","fixture-character"))
    grant=session_scope.create_session(token_label="synthetic-mobile",char_id="fixture-character")
    headers={"Authorization":"Bearer synthetic-mobile","X-Presence-Session":grant.session_id}
    state=store.load("fixture-owner","fixture-character")
    state["config"]["enabled"]=True
    state["config"]["min_interval_seconds"]=0
    store.save("fixture-owner","fixture-character",state)
    app=FastAPI();app.include_router(sensor.router);app.include_router(autonomy.router)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client,clock=clock,headers=headers,grant=grant,active=active)


def send(phone,kind,value,*,status="available",confidence=.9):
    fact={"observed_at":phone.clock[0],"status":status,"confidence":confidence}
    if status=="available":
        if kind=="light":fact["level"]=value;fact["lux"]=10 if value=="dark" else 200
        elif kind=="motion":fact["kind"]=value
        else:
            fact.update(window_start=phone.clock[0]-3600,interactive_seconds=300,
                category_seconds={value:180} if value in {"games","media"} else {})
    response=phone.client.post("/sensor/ambient",headers=phone.headers,json={kind:fact})
    assert response.status_code==200,response.text


def candidate(phone,kind="light"):
    before,after={"light":("bright","dark"),"motion":("upright","face_down"),"usage":("other","games")}[kind]
    for value in (before,before,after,after):
        send(phone,kind,value)
        phone.clock[0]+=30
    return store.load("fixture-owner","fixture-character")["pending_signals"]


def test_phone_policy_is_separate_default_off_and_validated(phone):
    policy=phone.client.get("/sensor/ambient/policy",headers=phone.headers)
    assert policy.status_code==200
    assert policy.json()["configured"]["enabled"] is False
    assert candidate(phone)==[]
    assert phone.client.patch("/sensor/ambient/policy",headers=phone.headers,json={"enabled":True}).status_code==200
    for invalid in ({"enabled":"true"},{"unknown":True},{"revision":100}):
        assert phone.client.patch("/sensor/ambient/policy",headers=phone.headers,json=invalid).status_code==422
    for credential in ("synthetic-other","synthetic-desktop"):
        denied=phone.client.patch("/sensor/ambient/policy",headers={**phone.headers,"Authorization":"Bearer "+credential},json={"enabled":True})
        assert denied.status_code==403


@pytest.mark.parametrize("kind",["light","motion","usage"])
def test_phone_edges_need_stability_and_keep_only_device_facts(phone,kind):
    assert phone.client.patch("/sensor/ambient/policy",headers=phone.headers,json={"enabled":True}).status_code==200
    pending=candidate(phone,kind)
    assert len(pending)==1
    signal=pending[0]["signal"]
    assert signal["source"]=="phone_ambient" and signal["suggested_action"]=="silent"
    assert signal["evidence"][0]["kind"]==kind
    assert "packageName" not in str(signal)
    assert phone_ambient.source_active("fixture-owner","fixture-character",[signal])
    send(phone,kind,{"light":"dark","motion":"face_down","usage":"games"}[kind])
    assert len(store.load("fixture-owner","fixture-character")["pending_signals"])==1


@pytest.mark.parametrize("revocation",["switch","permission","credential","session","restart","stale","character"])
def test_queued_phone_signal_cannot_survive_revocation_or_restart(phone,revocation):
    assert phone.client.patch("/sensor/ambient/policy",headers=phone.headers,json={"enabled":True}).status_code==200
    signal=candidate(phone)[0]["signal"]
    assert phone_ambient.source_active("fixture-owner","fixture-character",[signal])
    if revocation=="switch":
        phone.client.patch("/sensor/ambient/policy",headers=phone.headers,json={"enabled":False})
        phone.client.patch("/sensor/ambient/policy",headers=phone.headers,json={"enabled":True})
    elif revocation=="permission":send(phone,"light",None,status="permission_denied")
    elif revocation=="credential":phone.active.remove("synthetic-mobile")
    elif revocation=="session":session_scope._SESSIONS.clear()
    elif revocation=="restart":phone_ambient._detectors.clear()
    elif revocation=="stale":phone.clock[0]+=121
    else:
        assert not phone_ambient.source_active("fixture-owner","another-character",[signal])
        return
    assert not phone_ambient.source_active("fixture-owner","fixture-character",[signal])


def test_phone_confidence_kind_consent_and_cooldown(phone):
    phone.client.patch("/sensor/ambient/policy",headers=phone.headers,json={"enabled":True,"motion":False})
    assert candidate(phone,"motion") == []
    send(phone,"light","bright",confidence=.3)
    response=phone.client.get("/sensor/ambient/policy",headers=phone.headers).json()
    assert response["effective"]["light"] is False
    assert response["reason_codes"]["light"] == "uncertain"
    phone.clock[0]+=30
    pending=candidate(phone)
    assert len(pending)==1
    for value in ("bright","bright","dark","dark"):
        send(phone,"light",value)
        phone.clock[0]+=30
    assert len(store.load("fixture-owner","fixture-character")["pending_signals"])==1
    phone.clock[0]+=601
    # A sampling gap resets the baseline; a fresh stable edge is required.
    assert len(candidate(phone))==2


def test_admin_phone_policy_and_redacted_diagnostics_share_mobile_state(phone):
    admin_headers={"Authorization":"Bearer synthetic-admin"}
    response=phone.client.patch("/admin/autonomy/config",headers=admin_headers,json={"phone_ambient":{"enabled":True}})
    assert response.status_code==200,response.text
    assert phone.client.get("/sensor/ambient/policy",headers=phone.headers).json()["configured"]["enabled"]
    candidate(phone)
    response=phone.client.get("/observability/phone-ambient",headers=admin_headers)
    assert response.status_code==200
    data=response.json()
    assert data["sources"][0]["kinds"]["light"]["reason_code"]=="queued"
    assert "synthetic-mobile" not in response.text and phone.grant.session_id not in response.text
    assert "credential_hash" not in response.text and "packageName" not in response.text
    assert phone.client.patch("/admin/autonomy/config",headers=phone.headers,json={"phone_ambient":{"enabled":False}}).status_code==403
    assert phone.client.patch("/admin/autonomy/config",headers=admin_headers,json={"phone_ambient":{"enabled":"true"}}).status_code==422
