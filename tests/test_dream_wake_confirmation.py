"""Brief 176: Dream WAKE confirmation, session identity, and one-shot archive."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


UID = "owner-brief176"
DREAM_ID = "dream-brief176"
CHAR_ID = "dreamer"


def _active_state() -> dict:
    return {
        "user_id": UID,
        "status": "DREAM_ACTIVE",
        "dream_id": DREAM_ID,
        "char_id": CHAR_ID,
        "dream_mode": "sandbox",
        "frozen_world": "reality_derived",
    }


def _close_background_tasks(monkeypatch):
    def fake_create_task(coro):
        coro.close()
        return SimpleNamespace()

    monkeypatch.setattr("core.dream.dream_pipeline.asyncio.create_task", fake_create_task)


def _seed_current(uid: str = UID) -> bytes:
    from core.dream.dream_log import append_turn
    from core.sandbox import get_paths

    append_turn(uid, DREAM_ID, "user", "梦里的第一句", char_id=CHAR_ID)
    append_turn(uid, DREAM_ID, "assistant", "我还在。", char_id=CHAR_ID)
    return (get_paths().dreams_tmp_dir(char_id=CHAR_ID) / f"current_dream_{uid}.jsonl").read_bytes()


@pytest.mark.asyncio
async def test_retention_confirmation_closes_same_session_and_archives_once(sandbox, monkeypatch):
    from admin.routers import dream as dream_router
    from core.dream.dream_state import read_state, write_state
    from core.sandbox import get_paths

    _close_background_tasks(monkeypatch)
    _seed_current()
    write_state(UID, _active_state())
    monkeypatch.setattr("core.dream.dream_pipeline._should_retain", lambda _state: True)
    monkeypatch.setattr(
        "core.dream.dream_pipeline._generate_retention_line",
        AsyncMock(return_value="再留一会儿。"),
    )

    with patch('admin.routers.dream_common._owner_uid', return_value=UID):
        retained = await dream_router.dream_wake({}, _auth=None)

    assert retained["retained"] is True
    assert retained["dream_id"] == DREAM_ID
    pending = read_state(UID)
    assert pending["status"] == "DREAM_EXIT_REQUESTED"
    assert pending["dream_id"] == DREAM_ID
    assert pending["last_wake_observation"]["confirmation"] == "pending"

    current_path = get_paths().dreams_tmp_dir(char_id=CHAR_ID) / f"current_dream_{UID}.jsonl"
    archive_path = get_paths().dreams_archive_dir(char_id=CHAR_ID) / f"dream_{DREAM_ID}.jsonl"
    before = current_path.read_bytes()
    assert not archive_path.exists()

    with patch('admin.routers.dream_common._owner_uid', return_value=UID):
        closed = await dream_router.dream_wake({"dream_id": DREAM_ID}, _auth=None)

    assert closed["closed_now"] is True
    assert closed["archive_ok"] is True
    assert closed["exit_reason"] == "user_wake_confirmed_after_retention"
    final = read_state(UID)
    assert final["status"] == "REALITY_AFTERGLOW"
    assert "dream_id" not in final
    assert final["last_dream_id"] == DREAM_ID
    assert final["last_wake_observation"]["confirmation"] == "confirmed_wake"
    assert not current_path.exists()
    assert archive_path.read_bytes() == before

    with patch('admin.routers.dream_common._owner_uid', return_value=UID):
        repeated = await dream_router.dream_wake({"dream_id": DREAM_ID}, _auth=None)
    assert repeated["already_closed"] is True
    assert repeated["dream_id"] == DREAM_ID
    assert archive_path.read_bytes() == before


@pytest.mark.asyncio
async def test_resume_requires_matching_dream_id_and_preserves_current_transcript(sandbox, monkeypatch):
    from admin.routers import dream as dream_router
    from core.dream.dream_state import read_state, write_state

    _close_background_tasks(monkeypatch)
    current_before = _seed_current(UID + "-resume")
    uid = UID + "-resume"
    state = _active_state()
    state["user_id"] = uid
    write_state(uid, state)
    monkeypatch.setattr("core.dream.dream_pipeline._should_retain", lambda _state: True)
    monkeypatch.setattr(
        "core.dream.dream_pipeline._generate_retention_line",
        AsyncMock(return_value="留下来。"),
    )

    with patch('admin.routers.dream_common._owner_uid', return_value=uid):
        await dream_router.dream_wake({}, _auth=None)
        with pytest.raises(Exception) as stale_resume:
            await dream_router.dream_resume({"dream_id": "dream-stale"}, _auth=None)
        with pytest.raises(Exception) as stale_wake:
            await dream_router.dream_wake({"dream_id": "dream-stale"}, _auth=None)

    assert stale_resume.value.status_code == 409
    assert stale_wake.value.status_code == 409
    assert read_state(uid)["status"] == "DREAM_EXIT_REQUESTED"

    with patch('admin.routers.dream_common._owner_uid', return_value=uid):
        resumed = await dream_router.dream_resume({"dream_id": DREAM_ID}, _auth=None)

    assert resumed == {"ok": True, "resumed": True, "dream_id": DREAM_ID}
    assert read_state(uid)["status"] == "DREAM_ACTIVE"
    from core.sandbox import get_paths

    current_path = get_paths().dreams_tmp_dir(char_id=CHAR_ID) / f"current_dream_{uid}.jsonl"
    assert current_path.read_bytes() == current_before


@pytest.mark.asyncio
async def test_first_wake_without_retention_uses_user_reason(sandbox, monkeypatch):
    from admin.routers import dream as dream_router
    from core.dream.dream_state import read_state, write_state

    _close_background_tasks(monkeypatch)
    _seed_current(UID + "-direct")
    uid = UID + "-direct"
    state = _active_state()
    state["user_id"] = uid
    write_state(uid, state)
    monkeypatch.setattr("core.dream.dream_pipeline._should_retain", lambda _state: False)

    with patch('admin.routers.dream_common._owner_uid', return_value=uid):
        result = await dream_router.dream_wake({}, _auth=None)

    assert result["closed_now"] is True
    assert result["exit_mechanism"] == "user_hard_exit"
    assert result["exit_initiator"] == "user"
    assert result["exit_reason"] == "user_wake_no_retention"
    assert read_state(uid)["status"] == "REALITY_AFTERGLOW"


@pytest.mark.asyncio
async def test_archive_failure_keeps_closing_and_current_for_retry(sandbox, monkeypatch):
    from core.dream import dream_pipeline
    from core.dream.dream_state import read_state, write_state
    from core.sandbox import get_paths

    _close_background_tasks(monkeypatch)
    uid = UID + "-archive-failure"
    state = _active_state()
    state["user_id"] = uid
    write_state(uid, state)
    _seed_current(uid)
    monkeypatch.setattr("core.dream.dream_log.archive_current", lambda *args, **kwargs: False)

    result = await dream_pipeline.force_exit_dream(uid)

    assert result["closed_now"] is False
    assert result["archive_ok"] is False
    assert result["error"] == "dream_archive_failed"
    assert read_state(uid)["status"] == "DREAM_CLOSING"
    assert (get_paths().dreams_tmp_dir(char_id=CHAR_ID) / f"current_dream_{uid}.jsonl").exists()


def test_corrupt_state_does_not_report_successful_exit(sandbox):
    from core.dream.dream_pipeline import force_exit_dream
    from core.sandbox import get_paths
    import asyncio

    path = get_paths().dream_state_path(UID + "-corrupt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")

    result = asyncio.run(force_exit_dream(UID + "-corrupt"))

    assert result["ok"] is False
    assert result["exited"] is False
    assert result["archive_ok"] is False
