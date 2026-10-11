import asyncio
from datetime import datetime

from admin.routers import activity
from core.activity import store
from core.activity.registry import ACTIVITY_REGISTRY


def test_empty_activity_does_not_invent_state(monkeypatch):
    monkeypatch.setattr(activity, "resolve_requested_char_id", lambda value, **kw: "companion")
    monkeypatch.setattr(activity, "_current_session", lambda cid: (None, ""))
    result = asyncio.run(activity.get_activity_state(char_id="companion", auth=None))
    assert result["text"] == ""
    assert result["started_at"] is None
    assert result["next_switch_at"] is None
    assert result["thinking_about_eligible"] is False


def test_real_session_uses_its_own_time(sandbox, monkeypatch):
    monkeypatch.setattr(activity, "get_config", lambda: {"scheduler": {"owner_id": "owner"}})
    monkeypatch.setattr(activity, "resolve_requested_char_id", lambda value, **kw: "companion")
    session = store.create_session(uid="owner", char_id="companion", activity_type="chess")
    result = asyncio.run(activity.get_activity_state(char_id="companion", auth=None))
    assert result["id"] == session.session_id
    assert result["text"] == next(meta.label for meta in ACTIVITY_REGISTRY if meta.id == "chess")
    assert result["started_at"] == datetime.fromisoformat(session.created_at).timestamp()
    assert result["next_switch_at"] is None


def test_session_is_scoped_to_owner_and_character(sandbox, monkeypatch):
    monkeypatch.setattr(activity, "get_config", lambda: {"scheduler": {"owner_id": "owner"}})
    store.create_session(uid="owner", char_id="other_char", activity_type="chess")
    store.create_session(uid="other_user", char_id="companion", activity_type="chess")
    assert activity._current_session("companion") == (None, "")


def test_current_reads_leave_expired_record_unchanged(sandbox, monkeypatch):
    monkeypatch.setattr(activity, "get_config", lambda: {"scheduler": {"owner_id": "owner"}})
    session = store.create_session(uid="owner", char_id="companion", activity_type="chess")
    session.created_at = session.updated_at = "2000-01-01T00:00:00+00:00"
    store.save_session(session)
    before = store.load_session("companion", "owner", "chess", session.session_id).to_dict()
    assert activity._current_session("companion") == (None, "")
    assert activity._current_session("companion") == (None, "")
    assert store.load_session("companion", "owner", "chess", session.session_id).to_dict() == before
