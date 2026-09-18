"""Focused contracts for Brief 171 backend boundaries."""

import asyncio
import hashlib
from datetime import date

import pytest


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_remote_capability_matrix_is_fail_closed(monkeypatch):
    from core import config_loader
    from core.deployment_capabilities import capability_projection, tool_allowed

    monkeypatch.setattr(
        config_loader,
        "get_config",
        lambda: {"deployment": {"mode": "remote_server"}},
    )

    assert tool_allowed("fs_read") == (True, None)
    assert tool_allowed("workspace_read") == (False, "disabled_remote_server_local_capability")
    assert tool_allowed("get_time") == (True, None)
    rows = {row.logical_name: row for row in capability_projection()}
    assert rows["device_shutdown"].status == "disabled"
    assert rows["desktop_open_url"].status == "online_required"


@pytest.mark.asyncio
async def test_owner_turn_receipt_is_serial_and_conflict_safe(sandbox, monkeypatch):
    from core import owner_turn_service as service

    context = service.owner_input_context("owner-input")
    calls = []

    async def executor(*_args, **_kwargs):
        calls.append(True)
        await asyncio.sleep(0.03)
        return {"reply": "ok", "turn_id": "turn-1", "msg_id": "turn-1"}

    monkeypatch.setattr(
        service,
        "_project_canonical_result",
        lambda _turn_id: {"reply": "ok", "turn_id": "turn-1", "msg_id": "turn-1"},
    )

    async def run(message: str):
        return await service.execute_idempotent_owner_turn(
            client_turn_id="client-1",
            message=message,
            reply_to=None,
            upload_ids=[],
            context=context,
            executor=executor,
        )

    first, duplicate = await asyncio.gather(run("hello"), run("hello"))
    assert {first[0], duplicate[0]} == {"completed", "in_flight"}
    assert len(calls) == 1

    replay = await run("hello")
    assert replay[0] == "completed_replay"
    conflict = await run("different")
    assert conflict[0] == "conflict"


@pytest.mark.asyncio
async def test_remote_diary_mirror_revision_and_tombstone(sandbox, monkeypatch):
    from core import config_loader, diary_mirror
    from core.tools import diary_reader

    monkeypatch.setattr(
        config_loader,
        "get_config",
        lambda: {
            "deployment": {"mode": "remote_server"},
            "scheduler": {"owner_id": "owner"},
        },
    )

    content = "today's bounded note"
    applied = await diary_mirror.apply_batch(
        generation="generation-1",
        entries=[{
            "logical_date": "2026-08-09",
            "content": content,
            "sha256": _digest(content),
            "revision": 2,
        }],
    )
    assert applied["changed"] == 1
    assert diary_mirror.read_entry(date(2026, 8, 9)) == content
    assert diary_mirror.has_any_entry() is True

    repeated = await diary_mirror.apply_batch(
        generation="generation-1",
        entries=[{
            "logical_date": "2026-08-09",
            "content": content,
            "sha256": _digest(content),
            "revision": 2,
        }],
    )
    assert repeated["entries"][0]["status"] == "idempotent"

    old = "old revision"
    stale = await diary_mirror.apply_batch(
        generation="generation-2",
        entries=[{
            "logical_date": "2026-08-09",
            "content": old,
            "sha256": _digest(old),
            "revision": 1,
        }],
    )
    assert stale["entries"][0]["status"] == "stale_revision"
    assert diary_mirror.read_entry(date(2026, 8, 9)) == content

    tombstone = await diary_mirror.apply_batch(
        generation="generation-3",
        entries=[{
            "logical_date": "2026-08-09",
            "content": "",
            "sha256": _digest(""),
            "revision": 3,
            "deleted": True,
        }],
    )
    assert tombstone["entries"][0]["status"] == "applied"
    assert diary_mirror.has_any_entry() is False
    assert diary_reader.has_any_diary_entry() is False
