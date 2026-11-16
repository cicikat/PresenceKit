"""329: observable scoped profile corrections and read-side temporal authority."""
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.memory import temporal_facts as facts, user_profile, event_store, event_query
from core.memory.scope import MemoryScope
from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID


@pytest.fixture
def scope(sandbox):
    return MemoryScope.reality_scope("test_owner", TEST_CHAR_ID)


def seed(scope, text, timestamp, *, event_id=None, source="user_chat", actor="user", low_voice=False):
    result = event_store.append_event(scope, {"event_id":event_id or f"event-{timestamp}", "turn_id":f"turn-{timestamp}",
        "occurred_at":timestamp,"actor":actor,"kind":"user_message","source":source,"realm":"reality",
        "raw_text":text,"memory_text":text,"raw_payload_json":{"input_modality":"voice" if low_voice else "text","asr_low_confidence":low_voice}})
    assert result.ok
    return result.event_id


def test_original_user_evidence_corrects_current_profile_and_preserves_history(scope):
    old = seed(scope, "我住在青旅", 1_700_000_000)
    new = seed(scope, "我现在住在自己租的房间", 1_700_100_000)
    user_profile.save(scope.uid, {"location":"青旅","important_facts":[{"text":"住在青旅","tag":"habit","ts":time.time()}]}, char_id=scope.character_id)
    original = user_profile.load(scope.uid, char_id=scope.character_id)
    projected = facts.project(scope, original, {"episodic_result":"用户住在青旅", "mid_term":"目前住青旅"})
    from core.memory.user_profile import select_for_prompt
    actual = select_for_prompt(projected["profile"])
    assert "自己租的房间" in actual["core_text"] and "青旅" not in actual["core_text"]
    assert "青旅" not in actual["pref_text"]
    assert projected["episodic_result"].startswith("【历史/派生记录")
    assert "青旅" in event_query.get_event(scope, old)["raw_text"]
    assert new in projected["_temporal_fact_decisions"][0]["source_ids"]
    assert user_profile.load(scope.uid, char_id=scope.character_id) == original, "read projection must never migrate production data"


def test_late_old_event_and_expiration_never_resurrect_previous_state(scope):
    seed(scope,"我现在住在出租屋",200)
    seed(scope,"我住在青旅",100)  # inserted later, happened earlier
    projection=facts.project(scope,{"location":"青旅"},now=300)
    assert "出租屋" in projection["profile"]["location"]
    facts.record(scope,[facts.claim("residence","短租房",source_id="expiry",occurred_at=250,valid_to=280)])
    projection=facts.project(scope,user_profile.load(scope.uid,char_id=scope.character_id),now=300)
    assert "未知" in projection["profile"]["location"]
    assert all(row["status"]!="valid" for row in projection["profile"][facts.FIELD])


def test_plans_retractions_employment_cancellation_and_derived_compression(scope):
    facts.record(scope,facts.extract("我现在住在出租屋",source_id="housing",occurred_at=100))
    facts.record(scope,facts.extract("我打算搬家了",source_id="plan",occurred_at=200))
    facts.record(scope,facts.extract("我在某公司工作",source_id="job",occurred_at=100))
    facts.record(scope,facts.extract("我已经离职了",source_id="quit",occurred_at=200))
    facts.record(scope,facts.extract("我取消了购买订单",source_id="cancel",occurred_at=200))
    facts.record(scope,[facts.claim("residence","青旅",source_id="compressed-old",occurred_at=999,source="derived")])
    projected=facts.project(scope,user_profile.load(scope.uid,char_id=scope.character_id),include_evidence=False)
    assert "出租屋" in projected["profile"]["location"]
    assert "已离开原工作" in projected["profile"]["occupation"]
    assert "某公司" not in projected["profile"]["occupation"]
    purchase=facts.adjudicate(projected["profile"][facts.FIELD])["current"]["purchase"]
    assert "已取消" in purchase["value"] and "不代表已消费" in purchase["value"]
    assert "购买已取消" in user_profile.select_for_prompt(projected["profile"])["core_text"]
    facts.record(scope,facts.extract("我不是住青旅",source_id="deny",occurred_at=300))
    assert "出租屋" in facts.project(scope,user_profile.load(scope.uid,char_id=scope.character_id),include_evidence=False)["profile"]["location"]


def test_equal_conflicts_unknown_and_future_effective_fact(scope):
    rows=[facts.claim("residence",value,source_id=value,occurred_at=100) for value in ["旧房","甲房","乙房"]]
    rows[0]["occurred_at"]=50
    result=facts.project(scope,{facts.FIELD:rows,"location":"旧房"},now=200,include_evidence=False)
    assert "未知" in result["profile"]["location"]
    future=facts.claim("residence","未来房",source_id="future",occurred_at=150,valid_from=300)
    result=facts.adjudicate([rows[0],future],now=200)
    assert result["current"]["residence"]["value"]=="旧房"
    assert facts.adjudicate([rows[0],future],now=301)["current"]["residence"]["value"]=="未来房"


def test_scope_and_untrusted_evidence_do_not_change_current_state(scope):
    peer=MemoryScope.reality_scope(scope.uid,TEST_PEER_CHAR_ID)
    for number,(source,actor,voice) in enumerate([("dream_echo","user",False),("web","user",False),("user_chat","assistant",False),("user_chat","user",True)]):
        seed(scope,"我现在住在青旅",100+number,source=source,actor=actor,low_voice=voice)
    seed(peer,"我住在青旅",200)
    seed(scope,"我现在住在出租屋",50)
    assert "出租屋" in facts.project(scope,{})["profile"]["location"]
    assert "青旅" in facts.project(peer,{})["profile"]["location"]
    with pytest.raises(ValueError): facts.record(MemoryScope.dream_scope(scope.uid,TEST_CHAR_ID,"dream"),[])
    assert not facts.extract('他说我住在青旅',source_id="quote",occurred_at=300)


def test_capture_persists_correction_once_and_readonly_route_hides_body(scope,monkeypatch):
    from core.memory.fixation_pipeline import capture_turn
    from core.write_envelope import stamp_user_chat
    from core.config_loader import get_config
    cfg=dict(get_config());cfg["scheduler"]={**cfg.get("scheduler",{}),"owner_id":scope.uid}
    monkeypatch.setattr("core.config_loader.get_config",lambda:cfg)
    for _ in range(2):
        capture_turn(scope.uid,"我现在住在出租屋","记住了",turn_id="correction",char_id=scope.character_id,envelope=stamp_user_chat())
    stored=user_profile.load(scope.uid,char_id=scope.character_id)
    assert len(stored[facts.FIELD])==1
    from admin.routers import memory
    app=FastAPI();app.include_router(memory.router)
    # Override authentication boundary, while the route and scoped projection run.
    from admin.auth import TokenInfo
    monkeypatch.setattr("admin.auth.resolve_token",lambda token:TokenInfo("fixture",frozenset({"memory.read"} if token=="synthetic" else {"state.read"})))
    client=TestClient(app)
    response=client.get(f"/{scope.uid}/temporal-facts?char_id={scope.character_id}",headers={"Authorization":"Bearer synthetic"})
    assert response.status_code==200, response.text
    assert response.json()["claims"][0]["source_id"]=="correction:user"
    assert "出租屋" not in response.text
    denied=client.get(f"/{scope.uid}/temporal-facts?char_id={scope.character_id}",headers={"Authorization":"Bearer observer-only"})
    assert denied.status_code==403


@pytest.mark.asyncio
async def test_legacy_extractor_cannot_promote_a_compressed_old_status(scope):
    facts.record(scope,facts.extract("我现在住在出租屋",source_id="original",occurred_at=100))
    await user_profile.update(scope.uid,{"location":"青旅","temporal_facts":[],"_temporal_current":[{"topic":"residence","value":"青旅"}]},char_id=scope.character_id)
    stored=user_profile.load(scope.uid,char_id=scope.character_id)
    assert stored[facts.FIELD] and "_temporal_current" not in stored
    projection=facts.project(scope,stored,include_evidence=False)
    assert "出租屋" in user_profile.select_for_prompt(projection["profile"])["core_text"]


def test_withdrawn_source_is_not_recalled_and_does_not_resurrect_old_home(scope):
    old=seed(scope,"我住在青旅",100)
    latest=seed(scope,"我现在住在出租屋",200)
    facts.record(scope,facts.extract("我现在住在出租屋",source_id=latest,occurred_at=200))
    assert event_store.tombstone_event(scope,latest).ok
    projection=facts.project(scope,user_profile.load(scope.uid,char_id=scope.character_id))
    assert "未知" in user_profile.select_for_prompt(projection["profile"])["core_text"]
    assert "青旅" in event_query.get_event(scope,old)["raw_text"]


@pytest.mark.asyncio
async def test_profile_tool_reuses_the_same_current_fact_projection(scope):
    from core.tools.memory_definitions import _get_profile_wrapper
    user_profile.save(scope.uid,{'location':'青旅'},char_id=scope.character_id)
    seed(scope,'我现在住在出租屋',200)
    result=await _get_profile_wrapper(scope.uid,char_id=scope.character_id)
    assert '出租屋' in result and '青旅' not in result


def test_explicit_admin_edit_remains_effective_after_temporal_projection(scope,monkeypatch):
    from admin.routers import users
    from admin.auth import TokenInfo
    facts.record(scope,facts.extract('我现在住在青旅',source_id='old',occurred_at=100))
    app=FastAPI();app.include_router(users.router,prefix='/users')
    monkeypatch.setattr('admin.auth.resolve_token',lambda token:TokenInfo('admin-fixture',frozenset({'admin'})))
    response=TestClient(app).put(f'/users/{scope.uid}/profile?char_id={scope.character_id}',json={'location':'新住处'},headers={'Authorization':'Bearer synthetic'})
    assert response.status_code==200,response.text
    stored=user_profile.load(scope.uid,char_id=scope.character_id)
    assert stored['location']=='新住处'
    assert '新住处' in facts.project(scope,stored)['profile']['location']
    cleared=TestClient(app).put(f'/users/{scope.uid}/profile?char_id={scope.character_id}',json={'location':' '},headers={'Authorization':'Bearer synthetic'})
    assert cleared.status_code==200
    assert '未知' in facts.project(scope,user_profile.load(scope.uid,char_id=scope.character_id))['profile']['location']


@pytest.mark.asyncio
async def test_dossier_tool_cannot_upgrade_an_old_understanding_to_current(scope):
    from tests.test_memory_dossier_tools import _seed
    from core.tools.memory_dossiers import read_memory_dossier
    dossier,_=_seed(scope,title='青旅')
    seed(scope,'我现在住在出租屋',200)
    result=await read_memory_dossier(scope.uid,dossier,char_id=scope.character_id)
    assert result.safe_summary.startswith('【历史/派生记录')
    assert '青旅' in result.raw_data, 'historical dossier must remain intact'


def test_unavailable_evidence_returns_unknown_instead_of_stale_scalar(scope,monkeypatch):
    def unavailable(*args,**kwargs):
        raise event_query.EventQueryError('database_error')
    monkeypatch.setattr(event_query,'search',unavailable)
    result=facts.project(scope,{'location':'青旅'})
    assert '未知' in user_profile.select_for_prompt(result['profile'])['core_text']
    assert '青旅' not in user_profile.select_for_prompt(result['profile'])['core_text']
    assert result['_temporal_fact_decisions'][0]['reason']=='evidence_unavailable'
