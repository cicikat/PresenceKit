import asyncio
from types import SimpleNamespace
import pytest
from pydantic import BaseModel
from core.activity import continuity, records, store, transcript, activity_summary


class Body(BaseModel):
    session_id: str
    message: str
    request_id: str = "request"
    uid: str = ""


def test_unique_sink_and_no_surface_broadcast(sandbox, monkeypatch):
    import admin.routers._common as common
    monkeypatch.setattr(common, "active_char_id", lambda: "companion")
    monkeypatch.setattr(continuity, "get_config", lambda: {"scheduler": {"owner_id": "owner"}})
    value = store.create_session("owner", "companion", "chess")
    calls = []
    async def sink(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(written_to_memory=True, turn_id="canonical", msg_id="canonical")
    monkeypatch.setattr(continuity, "record_assistant_turn", sink)
    @continuity.reality_dialogue("chess")
    async def generate(body: Body, auth=None):
        return {"reply": "response"}
    async def run():
        body = Body(session_id=value.session_id, message="hello")
        first = await generate(body)
        second = await generate(body)
        assert first == second
        assert first["msg_id"] == "canonical"
        with pytest.raises(Exception):
            await generate(body.model_copy(update={"uid": "other"}))
    asyncio.run(run())
    assert len(calls) == 1
    assert calls[0]["fanout"] == []
    assert calls[0].get("coplay_echo", False) is False
    assert calls[0]["user_text"] == "hello"


def test_projection_changes_and_source_removal(sandbox):
    value = store.create_session("owner", "companion", "chess")
    transcript.append_entry("companion", "owner", "chess", value.session_id,
                            {"type": "user_chat", "text": "real dialogue"})
    asyncio.run(activity_summary.generate_and_reflow("owner", "companion", "chess", value.session_id))
    ref = f"activity:chess:{value.session_id}:summary"
    assert records.read("owner", "companion", ref)["total"] == 1
    transcript.append_entry("companion", "owner", "chess", value.session_id,
                            {"type": "assistant_chat", "text": "next"})
    assert records.read("owner", "companion", ref)["status"] == "pending"


@pytest.mark.asyncio
async def test_real_sink_canonical_history_reference_and_retry(sandbox, monkeypatch):
    import admin.routers._common as common
    from core.pipeline import Pipeline
    from core.turn_sink import record_assistant_turn
    from core.memory import short_term, event_query
    from core.memory.scope import MemoryScope
    monkeypatch.setattr(common, "active_char_id", lambda: "companion")
    monkeypatch.setattr(continuity, "get_config", lambda: {"scheduler": {"owner_id": "owner"}})
    monkeypatch.setattr("core.post_process.slow_queue.enqueue", lambda *a, **k: None)
    class Character:
        name = "Companion"
    pipeline = Pipeline(Character(), lore_engine=None)
    async def sink(**kwargs):
        return await record_assistant_turn(pipeline=pipeline, schedule_slow=False, **kwargs)
    monkeypatch.setattr(continuity, "record_assistant_turn", sink)
    value = store.create_session("owner", "companion", "chess")
    @continuity.reality_dialogue("chess")
    async def generate(body: Body, auth=None):
        for role, text in (("user_chat", body.message), ("assistant_chat", "response")):
            transcript.append_entry("companion", "owner", "chess", value.session_id,
                                    {"type": role, "text": text})
        return {"reply": "response"}
    body = Body(session_id=value.session_id, message="hello")
    first = await generate(body)
    assert await generate(body) == first
    assert first["turn_id"] == first["msg_id"]
    history = short_term.get_history("owner", char_id="companion")
    assert [m["role"] for m in history] == ["user", "assistant"]
    event = event_query.get_event(MemoryScope.reality_scope("owner", "companion"), first["turn_id"] + ":assistant")
    assert event["media_refs"][0]["source_ref"] == first["source_ref"]
    assert records.read("owner", "companion", first["source_ref"])["total"] == 2


@pytest.mark.asyncio
async def test_activity_source_ref_survives_midterm_and_episode(sandbox, monkeypatch):
    from unittest.mock import AsyncMock
    import json
    from core.memory import fixation_pipeline, mid_term, episodic_memory
    ref = "activity:chess:session:dialogue"
    monkeypatch.setattr("core.llm_client.summarize_turn", AsyncMock(return_value="evidenced dialogue"))
    monkeypatch.setattr("core.post_process.slow_queue.enqueue", lambda *a, **k: None)
    mid = await fixation_pipeline.summarize_to_midterm("turn", "owner", "hello", "response", [],
        char_id="companion", source="activity:chess", source_refs=[ref])
    assert mid_term.load("owner", char_id="companion")[0]["source_refs"] == [ref]
    monkeypatch.setattr("core.llm_client.chat", AsyncMock(return_value=json.dumps({
        "raw_facts": ["played chess"], "narrative_summary": "evidenced dialogue", "strength": 0.5,
        "topic_keywords": ["chess"], "emotion_peak": "neutral", "emotion_texture": "",
        "emotion_arc": "", "user_state": ""})))
    await fixation_pipeline.reflect_to_episodic("owner", mid_ids=[mid], char_id="companion")
    episodes = episodic_memory._load_memories("owner", char_id="companion")
    assert episodes[0]["source_refs"] == [ref]
    assert ref in episodic_memory.format_for_prompt(episodes, char_name="Companion")
