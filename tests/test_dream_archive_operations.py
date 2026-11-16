"""Read-only Dream archive and operations API contracts."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_archive_list_is_paginated_and_excludes_current_dream(sandbox):
    from admin.routers.dream import dream_archive_list

    archive_dir = sandbox.dreams_archive_dir(char_id="dreamer")
    _write_jsonl(archive_dir / "dream_old.jsonl", [
        {"role": "user", "content": "hello", "ts": 100.0, "context_snapshot": "secret"},
        {"role": "assistant", "content": "reply", "ts": 101.0, "prompt": "secret"},
    ])
    _write_jsonl(sandbox.dreams_tmp_dir(char_id="dreamer") / "current_dream_owner.jsonl", [
        {"role": "assistant", "content": "active", "ts": 200.0},
    ])

    result = asyncio.run(dream_archive_list(offset=0, limit=20, char_id="dreamer"))

    assert result["total"] == 1
    assert result["items"][0]["dream_id"] == "old"
    assert result["items"][0]["valid_assistant_turns"] == 1
    assert "context_snapshot" not in result["items"][0]
    assert "prompt" not in result["items"][0]


def test_archive_detail_returns_only_replay_fields_and_validates_id(sandbox):
    from admin.routers.dream import dream_archive_detail

    _write_jsonl(sandbox.dreams_archive_dir(char_id="dreamer") / "dream_replay_1.jsonl", [
        {"role": "user", "content": "hello", "ts": 100.0, "hidden_state": "secret"},
        {"role": "assistant", "content": "reply", "ts": 101.0, "never_retrieve": True},
        {"role": "tool", "content": "ignore", "ts": 102.0},
    ])

    result = asyncio.run(dream_archive_detail("replay_1", char_id="dreamer"))
    assert result["messages"] == [
        {"role": "user", "content": "hello", "ts": 100.0},
        {
            "role": "assistant",
            "content": "reply",
            "ts": 101.0,
            "segments": [{"type": "say", "text": "reply"}],
            "segmented_content": "reply",
        },
    ]
    assert "hidden_state" not in result
    assert "never_retrieve" not in result

    with pytest.raises(HTTPException) as error:
        asyncio.run(dream_archive_detail("../outside", char_id="dreamer"))
    assert error.value.status_code == 422


def test_archive_detail_projects_all_narrative_segment_types_without_writing_archive(sandbox, monkeypatch):
    from admin.routers.dream import dream_archive_detail

    archive_path = sandbox.dreams_archive_dir(char_id="dreamer") / "dream_segments_1.jsonl"
    _write_jsonl(archive_path, [
        {"role": "assistant", "content": "<say>你好</say><do>抬头</do><env>夜色</env><feel>安心</feel>", "ts": 101.0},
        {"role": "user", "content": "你还在吗？", "ts": 102.0},
    ])
    before = archive_path.read_bytes()

    result = asyncio.run(dream_archive_detail("segments_1", char_id="dreamer"))

    assert result["messages"][0]["segments"] == [
        {"type": "say", "text": "你好"},
        {"type": "do", "text": "抬头"},
        {"type": "env", "text": "夜色"},
        {"type": "feel", "text": "安心"},
    ]
    assert result["messages"][1] == {"role": "user", "content": "你还在吗？", "ts": 102.0}
    assert archive_path.read_bytes() == before


def test_archive_detail_parser_failure_is_raw_content_fallback(sandbox, monkeypatch):
    from admin.routers.dream import dream_archive_detail

    _write_jsonl(sandbox.dreams_archive_dir(char_id="dreamer") / "dream_fallback_1.jsonl", [
        {"role": "assistant", "content": "legacy reply", "ts": 101.0},
    ])

    def _boom(_content):
        raise RuntimeError("parser failure")

    monkeypatch.setattr("core.narrative_parser.parse_narrative_segments", _boom)
    result = asyncio.run(dream_archive_detail("fallback_1", char_id="dreamer"))

    assert result["messages"] == [{
        "role": "assistant",
        "content": "legacy reply",
        "ts": 101.0,
        "segmented_content": "legacy reply",
        "segment_parse_fallback": True,
    }]


def test_operations_omit_postcard_and_dream_text(sandbox, monkeypatch):
    from admin.routers.dream import dream_operations_get
    from core.dream.dream_state import write_state
    from core.dream.exit_observability import record
    from core.dream.scenario_progress_audit import record as record_scenario_progress, record_reconciler

    _write_jsonl(sandbox.dreams_archive_dir(char_id="dreamer") / "dream_ops_1.jsonl", [
        {"role": "assistant", "content": "dream prose", "ts": 100.0},
    ])
    summary_path = sandbox.dreams_summaries_dir(char_id="dreamer") / "dream_ops_1.summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps({"dream_id": "ops_1", "summary": "safe", "created_at": 101.0}), encoding="utf-8")
    write_state("owner", {"status": "REALITY_AFTERGLOW", "last_dream_id": "ops_1", "last_greeted_dream_id": None})
    record("owner", "ops_1", char_id="dreamer", lifecycle="blocked", reason_code="dnd")
    record_scenario_progress(
        "ops_1",
        char_id="dreamer",
        current_stage_id="opening",
        control_status="valid",
        control_version=2,
        matched_exit_ids=["E1", "raw text"],
        disposition="advanced",
        from_stage_id="opening",
        to_stage_id="next",
    )
    record_reconciler(
        "ops_1",
        char_id="dreamer",
        trigger="control_missing",
        status="completed",
        decision="stay",
        from_stage_id="opening",
        expected_state_version=2,
        state_version=2,
    )
    schedule = sandbox.dreams_postcards_dir(char_id="dreamer") / "schedule.json"
    schedule.parent.mkdir(parents=True, exist_ok=True)
    schedule.write_text(json.dumps([{"dream_id": "ops_1", "letter_text": "postcard prose", "sent": False, "attempts": 1, "delivery_status": "smtp_failed", "last_error": "smtp_failed"}], ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr('admin.routers.dream_common._owner_uid', lambda: "owner")
    monkeypatch.setattr('admin.routers.dream_common._active_dream_char_id', lambda: "dreamer")
    result = asyncio.run(dream_operations_get())

    encoded = json.dumps(result, ensure_ascii=False)
    assert "dream prose" not in encoded
    assert "postcard prose" not in encoded
    assert result["exit_lifecycle"][0]["reason_code"] == "dnd"
    assert result["postcards"][0]["delivery_status"] == "smtp_failed"
    assert result["consistency"]["last_dream_id_present"] is True
    assert result["scenario_progress"]["last"]["dream_id"] == "ops_1"
    assert result["scenario_progress"]["last"]["matched_exit_ids"] == ["E1"]
    assert result["scenario_progress"]["last"]["from_stage_id"] == "opening"
    assert result["scenario_progress"]["last"]["to_stage_id"] == "next"
    assert result["scenario_reconciliation"]["trigger_count"] == 1
    assert result["scenario_reconciliation"]["latest"]["reconciler_decision"] == "stay"
    assert "raw text" not in encoded
