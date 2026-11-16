from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID


class _Session:
    WAITING_CONFIRM = "waiting_confirm"
    status = "idle"

    def set_waiting_confirm(self, *args):
        raise AssertionError("read-only memory tool must not request confirmation")


@pytest.mark.asyncio
async def test_get_episodic_wrapper_reads_explicit_non_default_character(sandbox):
    from core.memory.episodic_memory import write_episode
    from core.tools.memory_definitions import _get_episodic_wrapper

    uid = "memory-event-scope-owner"
    write_episode(uid, {
        "id": "default-only",
        "timestamp": 1.0,
        "summary": "default character placeholder",
        "narrative_summary": "default character placeholder",
        "strength": 0.9,
        "tags": ["scope"],
        "topic_keywords": ["scope"],
    }, char_id=TEST_CHAR_ID)
    write_episode(uid, {
        "id": "peer-only",
        "timestamp": 1.0,
        "summary": "peer character placeholder",
        "narrative_summary": "peer character placeholder",
        "strength": 0.9,
        "tags": ["scope"],
        "topic_keywords": ["scope"],
    }, char_id=TEST_PEER_CHAR_ID)

    result = await _get_episodic_wrapper(uid, "scope", char_id=TEST_PEER_CHAR_ID)
    assert "peer character placeholder" in result
    assert "default character placeholder" not in result

    with pytest.raises(TypeError):
        await _get_episodic_wrapper(uid, "scope")


def test_memory_read_scope_rejects_empty_uid_or_character(sandbox):
    from core.tool_runtime.policy import _require_memory_read_scope

    with pytest.raises(ValueError):
        _require_memory_read_scope("", TEST_CHAR_ID)
    with pytest.raises(ValueError):
        _require_memory_read_scope("owner", "")


def test_cleanup_event_log_isolated_per_character(sandbox, monkeypatch):
    from core.memory import event_log

    uid = "memory-event-retention-owner"
    old_day = "2000-01-01.md"
    char_a_dir = event_log._event_log_write_dir(uid, char_id=TEST_CHAR_ID)
    char_b_dir = event_log._event_log_write_dir(uid, char_id=TEST_PEER_CHAR_ID)
    char_a_dir.mkdir(parents=True, exist_ok=True)
    char_b_dir.mkdir(parents=True, exist_ok=True)
    (char_a_dir / old_day).write_text("old A", encoding="utf-8")
    (char_b_dir / old_day).write_text("old B", encoding="utf-8")

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"forensic_logs": {"event_log": {
            "day_archive_days": 1,
            "full_log_max_size_mb": 10,
            "full_log_keep": 3,
        }}},
    )

    event_log.cleanup_event_log(uid, char_id=TEST_CHAR_ID)
    assert not (char_a_dir / old_day).exists()
    assert (char_b_dir / old_day).exists()

    event_log.cleanup_event_log(uid, char_id=TEST_PEER_CHAR_ID)
    assert not (char_b_dir / old_day).exists()


@pytest.mark.asyncio
async def test_scheduler_retention_visits_all_registered_characters(monkeypatch):
    import core.scheduler.loop as scheduler_loop

    calls: list[str] = []

    class _Entry:
        def __init__(self, asset_id):
            self.id = asset_id

    class _Registry:
        def list_all(self, kind):
            assert kind == "character"
            return [_Entry(TEST_CHAR_ID), _Entry(TEST_PEER_CHAR_ID)]

    monkeypatch.setattr(scheduler_loop, "_is_ready", lambda name: True)
    monkeypatch.setattr(scheduler_loop, "_owner_id", lambda: "retention-owner")
    monkeypatch.setattr(scheduler_loop, "_cfg_retention", lambda: {})
    monkeypatch.setattr(scheduler_loop, "_mark", lambda name: None)
    monkeypatch.setattr("core.asset_registry.get_registry", lambda: _Registry())

    def _cleanup(uid, *, char_id):
        calls.append(f"{uid}:{char_id}")
        if char_id == TEST_CHAR_ID:
            raise RuntimeError("fixture failure for one character")

    monkeypatch.setattr("core.memory.event_log.cleanup_event_log", _cleanup)
    await scheduler_loop._check_log_maintenance()

    assert calls == [f"retention-owner:{TEST_CHAR_ID}", f"retention-owner:{TEST_PEER_CHAR_ID}"]
