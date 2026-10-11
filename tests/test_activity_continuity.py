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
