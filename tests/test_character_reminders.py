"""Character reminder lifecycle: Runtime store, tools, races, isolation (256 E)."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from core.agent_runtime import task_store
from core.agent_runtime.models import CausationRef, TaskPrincipal, TERMINAL_STATUSES
from core.agent_runtime import scheduler_capability as sched
from core.agent_runtime import task_manager
from core import tool_dispatcher
from core.autonomy.policy import tool_eligibility
from core.tools import reminder as reminder_mod
from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID


_UID = "owner-one"
_CHAR = TEST_CHAR_ID
_OTHER_CHAR = TEST_PEER_CHAR_ID
_OTHER_UID = "owner-two"


def _tool_payload(result: str | None) -> str:
    text = result or ""
    marker = "结果："
    if marker in text:
        text = text.split(marker, 1)[1]
    return text


class _Session:
    WAITING_CONFIRM = "waiting_confirm"
    IDLE = "idle"
    status = IDLE

    def set_waiting_confirm(self, tool_name, tool_args):
        self.status = self.WAITING_CONFIRM


@pytest.fixture(autouse=True)
def _fresh_process():
    task_store.reset_process_instance_for_tests("process-reminders")
    yield
    task_store.reset_process_instance_for_tests()


def _principal(uid=_UID, char=_CHAR) -> TaskPrincipal:
    return TaskPrincipal.reality(uid, char)


def _create(content="吃药", *, due_offset=-10, recurrence=None, uid=_UID, char=_CHAR, now=None):
    timestamp = time.time() if now is None else now
    return sched.create_schedule(
        _principal(uid, char),
        content=content,
        due_at=timestamp + due_offset,
        recurrence_seconds=recurrence,
        now=timestamp,
        causation_ref=CausationRef("tool_request", f"test:{content}"),
    )


def test_list_get_add_return_schedule_id_not_task_id(sandbox):
    created = _create("交材料")
    assert created["ok"] is True
    assert created["schedule_id"]
    assert created["revision"] == 1
    assert created["content"] == "交材料"
    assert created["status"] == "scheduled"
    assert "task_id" not in created
    listed = sched.list_schedules(_principal())
    assert listed[0]["schedule_id"] == created["schedule_id"]
    assert listed[0]["content"] == "交材料"
    assert "task_id" not in listed[0]
    got = sched.get_schedule(_principal(), created["schedule_id"])
    assert got["remind_at"]
    prompt = sched.prompt_reminders(_principal())
    assert prompt[0]["schedule_id"] == created["schedule_id"]
    assert prompt[0]["content"] == "交材料"


def test_cas_update_cancel_restore_and_completed_lease(sandbox):
    created = _create("开会")
    conflict = reminder_mod.update_reminder(
        _UID, created["schedule_id"], created["revision"] + 1, char_id=_CHAR, content="改期",
    )
    assert conflict["ok"] is False
    assert conflict["code"] == "revision_conflict"
    updated = reminder_mod.update_reminder(
        _UID, created["schedule_id"], created["revision"], char_id=_CHAR, content="改期开会",
    )
    assert updated["ok"] is True
    assert updated["revision"] == created["revision"] + 1
    assert updated["content"] == "改期开会"
    cancelled = reminder_mod.cancel_reminder(
        _UID, created["schedule_id"], updated["revision"], char_id=_CHAR,
    )
    assert cancelled["ok"] is True
    assert cancelled["status"] == "cancelled"
    restored = reminder_mod.restore_reminder(
        _UID, created["schedule_id"], cancelled["revision"], char_id=_CHAR,
    )
    assert restored["ok"] is True
    assert restored["status"] == "scheduled"
    assert restored["revision"] > cancelled["revision"]
    one_shot = _create("一次性", due_offset=-5)
    claimed = sched.begin_delivery(
        _principal(), one_shot["schedule_id"],
        expected_revision=one_shot["revision"],
        occurrence="occ-complete",
        now=time.time(),
    )
    assert claimed is not None
    assert sched.finish_delivery(
        _principal(), one_shot["schedule_id"], sent=True, occurrence="occ-complete",
    )
    completed = sched.get_schedule(_principal(), one_shot["schedule_id"])
    assert completed["status"] == "completed"
    revive = reminder_mod.restore_reminder(
        _UID, one_shot["schedule_id"], completed["revision"], char_id=_CHAR,
    )
    assert revive["ok"] is False
    assert revive["code"] == "path_not_found"
    assert revive.get("reason") == "completed_lease"


def test_stale_revision_is_not_delivered(sandbox):
    created = _create("旧版")
    updated = sched.update_schedule(
        _principal(), created["schedule_id"],
        expected_revision=created["revision"],
        content="新版",
        now=time.time(),
    )
    stale = sched.begin_delivery(
        _principal(), created["schedule_id"],
        expected_revision=created["revision"],
        occurrence="stale-occ",
        now=time.time(),
    )
    assert stale is None
    live = sched.begin_delivery(
        _principal(), created["schedule_id"],
        expected_revision=updated["revision"],
        occurrence="fresh-occ",
        now=time.time(),
    )
    assert live is not None
    assert live["content"] == "新版"


def test_cancel_during_in_flight_cannot_recall_sent(sandbox):
    created = _create("发送中")
    occ = "send-1"
    claimed = sched.begin_delivery(
        _principal(), created["schedule_id"],
        expected_revision=created["revision"], occurrence=occ, now=time.time(),
    )
    assert claimed["status"] == "in_flight"
    cancelled = sched.cancel_schedule(
        _principal(), created["schedule_id"], expected_revision=created["revision"],
        now=time.time(),
    )
    assert cancelled["status"] == "in_flight"
    assert cancelled["ok"] is True
    reloaded = sched.get_schedule(_principal(), created["schedule_id"])
    assert reloaded["status"] == "in_flight"
    assert sched.finish_delivery(_principal(), created["schedule_id"], sent=True, occurrence=occ)
    done = sched.get_schedule(_principal(), created["schedule_id"])
    assert done["status"] == "cancelled"
    assert reminder_mod.cancel_reminder(
        _UID, created["schedule_id"], done["revision"], char_id=_CHAR,
    )["status"] == "cancelled"


def test_unsent_in_flight_reverts(sandbox):
    created = _create("未发出")
    occ = "unsent"
    assert sched.begin_delivery(
        _principal(), created["schedule_id"],
        expected_revision=created["revision"], occurrence=occ, now=time.time(),
    )
    assert sched.finish_delivery(
        _principal(), created["schedule_id"], sent=False, occurrence=occ, now=time.time(),
    )
    row = sched.get_schedule(_principal(), created["schedule_id"])
    assert row["status"] == "scheduled"


def test_recurrence_spawns_new_task_and_does_not_revive_old_lease(sandbox):
    created = _create("每日药", recurrence=60)
    first_task = None
    with sched._lock_for(_UID, _CHAR):
        raw = sched._load_store(sched._path(_principal()))["schedules"][0]
        first_task = raw["task_id"]
    occ = "r1"
    claimed = sched.begin_delivery(
        _principal(), created["schedule_id"],
        expected_revision=created["revision"], occurrence=occ, now=time.time(),
    )
    assert claimed is not None
    assert sched.finish_delivery(
        _principal(), created["schedule_id"], sent=True, occurrence=occ, now=time.time(),
    )
    again = sched.get_schedule(_principal(), created["schedule_id"])
    assert again["status"] == "scheduled"
    assert again["revision"] > created["revision"]
    assert again["due_at"] > created["due_at"]
    with sched._lock_for(_UID, _CHAR):
        raw = next(
            item for item in sched._load_store(sched._path(_principal()))["schedules"]
            if item["schedule_id"] == created["schedule_id"]
        )
        second_task = raw["task_id"]
    assert second_task != first_task
    old = task_manager.get_task(_principal(), first_task)
    assert old["status"] in TERMINAL_STATUSES
    new = task_manager.get_task(_principal(), second_task)
    assert new["status"] not in TERMINAL_STATUSES


def test_year_wrap_and_past_hhmm(sandbox):
    clock = datetime(2026, 12, 31, 23, 30)
    wrapped = reminder_mod._parse_time("01-01 08:00", now=clock)
    assert wrapped == datetime(2027, 1, 1, 8, 0)
    tomorrow = reminder_mod._parse_time("08:00", now=datetime(2026, 5, 1, 9, 0))
    assert tomorrow == datetime(2026, 5, 2, 8, 0)
    past = reminder_mod._parse_time("2020-01-01 08:00")
    assert past == datetime(2020, 1, 1, 8, 0)
    naive = reminder_mod._parse_time("2026-09-19 18:00", now=datetime(2026, 9, 19, 10, 0))
    assert naive.tzinfo is None
    created = reminder_mod.add_reminder(_UID, "本地时间", "2026-09-19 18:00", char_id=_CHAR)
    payload = json.loads(created)
    assert payload["remind_at"] == "2026-09-19 18:00"
    assert datetime.fromtimestamp(payload["due_at"]) == naive


def test_cross_character_isolation(sandbox):
    mine = _create("只属于我", char=_CHAR)
    theirs = _create("别人的", char=_OTHER_CHAR)
    listed = sched.list_schedules(_principal())
    ids = {item["schedule_id"] for item in listed}
    assert mine["schedule_id"] in ids
    assert theirs["schedule_id"] not in ids
    with pytest.raises(sched.ScheduleError) as exc:
        sched.get_schedule(_principal(), theirs["schedule_id"])
    assert exc.value.code == "path_not_found"
    other_owner = _create("另一用户", uid=_OTHER_UID, char=_CHAR)
    assert other_owner["schedule_id"] not in {
        item["schedule_id"] for item in sched.list_schedules(_principal())
    }


def test_persist_failure_cancels_new_task(sandbox, monkeypatch):
    monkeypatch.setattr(sched, "safe_write_json", lambda *_args, **_kwargs: False)
    with pytest.raises(sched.ScheduleError) as exc:
        _create("写盘失败")
    assert exc.value.code == "quota_exhausted"
    snapshot = task_manager.observability_snapshot(uid=_UID, char_id=_CHAR)
    statuses = {entry["status"] for entry in snapshot.get("entries") or []}
    assert "queued" not in statuses
    assert "running" not in statuses


def test_concurrent_cas(sandbox):
    created = _create("并发")
    results = []

    def _update(content):
        try:
            results.append(sched.update_schedule(
                _principal(), created["schedule_id"],
                expected_revision=created["revision"],
                content=content,
            ))
        except sched.ScheduleError as exc:
            results.append(exc.code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_update, "A"), pool.submit(_update, "B")]
        for future in futures:
            future.result()
    codes = [item if isinstance(item, str) else "ok" for item in results]
    assert codes.count("ok") == 1
    assert codes.count("revision_conflict") == 1


def test_reminder_proposer_is_observation_only(sandbox, monkeypatch):
    from core.scheduler.triggers import reminders as rem_trigger

    _create("到点了")
    monkeypatch.setattr("core.scheduler.loop._owner_id", lambda: _UID)
    proposal = rem_trigger.propose()
    assert proposal is not None
    assert proposal.execute is None
    assert proposal.metadata["observation_only"] is True
    assert proposal.metadata["schedule_id"]


def test_self_note_does_not_create_schedule(sandbox):
    from core import character_self as self_mod
    self_mod.create_self("notes/明天提醒.md", "明天提醒买菜", user_id=_UID, char_id=_CHAR)
    assert sched.list_schedules(_principal()) == []


def test_prompt_sees_newly_added(sandbox):
    from core.tools.reminder import get_reminders
    assert get_reminders(_UID, char_id=_CHAR) == []
    _create("新加的可见", due_offset=3600)
    items = get_reminders(_UID, char_id=_CHAR)
    assert items[0]["content"] == "新加的可见"
    assert items[0]["schedule_id"]


@pytest.mark.asyncio
async def test_tools_are_discoverable_and_frozen_principal(sandbox, monkeypatch):
    monkeypatch.setattr(tool_dispatcher, "_is_tool_enabled", lambda _: True)
    monkeypatch.setattr(tool_dispatcher, "_current_mode", lambda: "safe")
    schemas = tool_dispatcher.get_tools_schema(categories=["info"])
    names = {(item.get("function") or {}).get("name") for item in schemas}
    for name in (
        "list_reminders", "get_reminder", "add_reminder",
        "update_reminder", "cancel_reminder", "restore_reminder",
    ):
        spec = tool_dispatcher._TOOL_REGISTRY[name]
        assert spec["category"] == "info"
        assert spec["dangerous"] is False
        assert spec.get("require_confirm") is not True
        assert spec["examples"]
        assert spec["keywords"]
        assert name in names
        eligible, reason = tool_eligibility(
            name, {"enabled": True}, registry=tool_dispatcher._TOOL_REGISTRY,
            effect=spec["effect"],
        )
        assert eligible is True, (name, reason)
    added = await tool_dispatcher.execute_structured(
        "add_reminder",
        {"content": "八点吃药", "remind_at": "08:00"},
        _UID, _UID, False, _Session(),
        origin="user_live", char_id=_CHAR,
    )
    payload = json.loads(_tool_payload(added.result))
    assert payload["ok"] is True
    assert payload["schedule_id"]
    injected = await tool_dispatcher.execute_structured(
        "list_reminders",
        {"user_id": "other", "char_id": "other-char"},
        _UID, _UID, False, _Session(),
        origin="user_live", char_id=_CHAR,
    )
    assert "grant_principal_mismatch" in (injected.result or "")
    cancelled = await tool_dispatcher.execute_structured(
        "cancel_reminder",
        {"schedule_id": payload["schedule_id"], "expected_revision": payload["revision"]},
        _UID, _UID, False, _Session(),
        origin="autonomy_loop", char_id=_CHAR,
    )
    assert cancelled.confirmation_request is None
    body = json.loads(_tool_payload(cancelled.result))
    assert body["ok"] is True
    assert body["status"] == "cancelled"


@pytest.mark.asyncio
async def test_check_reminders_uses_begin_finish_and_skips_stale(sandbox, monkeypatch):
    from core.scheduler import loop

    created = _create("到点")
    sent = []

    async def fake_send(uid, char_id, text, **kwargs):
        sent.append((uid, char_id, kwargs.get("correlation_id"), text))
        return True, "sent"

    monkeypatch.setattr(loop, "_cfg", lambda: {"enabled": True})
    monkeypatch.setattr(loop, "_owner_id", lambda: _UID)
    monkeypatch.setattr("core.autonomy.talk_gate.send", fake_send)

    async def fake_compose(oid, char_id, prompt, **kwargs):
        return "该喝水啦"

    monkeypatch.setattr(loop, "_compose_trigger_reply", fake_compose)
    await loop._check_reminders()
    row = sched.get_schedule(_principal(), created["schedule_id"])
    assert row["status"] == "completed"
    assert len(sent) == 1
    assert sent[0][0] == _UID
    assert sent[0][1] == _CHAR
    await loop._check_reminders()
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_check_reminders_delivers_generated_text_not_template(sandbox, monkeypatch):
    from core.scheduler import loop

    _create("喝水休息")
    sent, prompts = [], []

    async def fake_send(uid, char_id, text, **kwargs):
        sent.append(text)
        return True, "sent"

    async def fake_compose(oid, char_id, prompt, **kwargs):
        prompts.append((prompt, kwargs))
        return "去喝点水吧，歇一歇"

    monkeypatch.setattr(loop, "_cfg", lambda: {"enabled": True})
    monkeypatch.setattr(loop, "_owner_id", lambda: _UID)
    monkeypatch.setattr("core.autonomy.talk_gate.send", fake_send)
    monkeypatch.setattr(loop, "_compose_trigger_reply", fake_compose)
    await loop._check_reminders()
    assert sent == ["去喝点水吧，歇一歇"]
    assert len(prompts) == 1
    assert "喝水休息" in prompts[0][0]
    assert prompts[0][1]["recall_policy"] == "anchored"
    assert prompts[0][1]["search_query"] == "喝水休息"
    assert all("备忘录提醒时间到了" not in t for t in sent)


@pytest.mark.asyncio
async def test_check_reminders_compose_failure_retries_then_neutral(sandbox, monkeypatch):
    from core.scheduler import loop

    created = _create("吃药")
    sent, calls = [], []

    async def fake_send(uid, char_id, text, **kwargs):
        sent.append(text)
        return True, "sent"

    async def failing_compose(oid, char_id, prompt, **kwargs):
        calls.append(prompt)
        return None

    monkeypatch.setattr(loop, "_cfg", lambda: {"enabled": True})
    monkeypatch.setattr(loop, "_owner_id", lambda: _UID)
    monkeypatch.setattr("core.autonomy.talk_gate.send", fake_send)
    monkeypatch.setattr(loop, "_compose_trigger_reply", failing_compose)
    for _ in range(3):
        await loop._check_reminders()
    assert sent == []
    assert len(calls) == 3
    row = sched.get_schedule(_principal(), created["schedule_id"])
    assert row["status"] == "scheduled"
    await loop._check_reminders()
    assert len(calls) == 3
    assert sent == ["到时间啦：吃药"]


def test_observability_is_metadata_only(sandbox, monkeypatch):
    secret = "character-reminder-obs-secret"
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: secret)
    created = _create("不要泄露这条正文")
    from admin.admin_server import app

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/observability/character-reminders").status_code == 401
    response = client.get(
        "/observability/character-reminders",
        params={"uid": _UID, "char_id": _CHAR},
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert response.status_code == 200
    payload = response.json()
    blob = json.dumps(payload)
    assert payload["capability"] == "character-reminders.v1"
    assert payload["counts"]["scheduled"] >= 1
    assert payload["items"][0]["schedule_id"] == created["schedule_id"]
    assert "不要泄露这条正文" not in blob
    assert str(sandbox.agent_runtime_schedule_state(_UID, char_id=_CHAR)) not in blob
    assert "sk-" not in blob


def test_restart_persists_schedule(sandbox):
    created = _create("重启仍在")
    path = sandbox.agent_runtime_schedule_state(_UID, char_id=_CHAR)
    assert path.is_file()
    again = sched.list_schedules(_principal())
    assert again[0]["schedule_id"] == created["schedule_id"]
    assert again[0]["content"] == "重启仍在"
