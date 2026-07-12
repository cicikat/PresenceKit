"""Brief 50: append-only, fail-open Stage arbiter decision traces."""
from __future__ import annotations

import json

import pytest


def _settings(**overrides):
    from core.stage.models import StageSettings

    values = {
        "min_responders": 1,
        "max_responders": 1,
        "max_ai_chain_depth": 2,
        "respond_threshold": 0.5,
        "talkativeness": {"yexuan": 1.0, "yexuanJ-5412": 1.0},
    }
    values.update(overrides)
    return StageSettings(**values)


@pytest.mark.asyncio
async def test_trace_records_phase_a_and_each_phase_b_selection(sandbox):
    from core.stage.runner import run_owner_turn
    from core.stage.store import create_stage

    stage = create_stage(
        "trace-round", "owner", ["yexuan", "yexuanJ-5412"], settings=_settings(),
    )

    async def generate(_stage, speaker_id, _transcript, _turn_id, _triggered_by):
        return f"{speaker_id} 的不同回复"

    result = await run_owner_turn(stage.group_id, "今天怎么样", generate_reply=generate, turn_id="round-1")

    records = [
        json.loads(line)
        for line in sandbox.stage_arbiter_trace(group_id=stage.group_id).read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 3
    assert [record["phase"] for record in records] == ["A", "B", "B"]
    assert [record["chain_depth"] for record in records] == [0, 0, 1]
    assert [record["selected"][0] for record in records] == [entry.speaker_id for entry in result.replies]
    assert all(record["round_id"] == "round-1" for record in records)
    assert records[0]["latest_speaker"] == "owner"
    assert records[0]["latest_excerpt"] == "今天怎么样"
    assert all(record["candidates"] for record in records)


@pytest.mark.asyncio
async def test_trace_write_failure_does_not_block_stage_turn(sandbox, monkeypatch):
    from core.stage.runner import run_owner_turn
    from core.stage.store import create_stage

    stage = create_stage("trace-fail", "owner", ["yexuan"], settings=_settings(max_ai_chain_depth=0))
    monkeypatch.setattr("core.stage.runner.safe_append_jsonl", lambda *_args, **_kwargs: False)

    result = await run_owner_turn(
        stage.group_id,
        "hello",
        generate_reply=lambda *_args: "reply",
        turn_id="round-fail",
    )

    assert [entry.content for entry in result.replies] == ["reply"]


def test_arbiter_trace_rolls_and_keeps_three_archives(sandbox):
    from core.safe_write import rotate_jsonl_if_needed

    path = sandbox.stage_arbiter_trace(group_id="trace-rotate")
    path.parent.mkdir(parents=True, exist_ok=True)
    for index in range(5):
        path.write_bytes(b"x" * 32)
        assert rotate_jsonl_if_needed(path, max_bytes=1, keep_n=3)
    archives = sorted(path.parent.glob("arbiter_trace.jsonl.*.gz"))
    assert [item.name for item in archives] == [
        "arbiter_trace.jsonl.1.gz",
        "arbiter_trace.jsonl.2.gz",
        "arbiter_trace.jsonl.3.gz",
    ]
