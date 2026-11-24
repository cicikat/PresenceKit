"""Baseline contract for the pre-event-id memory chain.

This test deliberately exercises only existing writers/readers.  It is the
regression anchor for later Memory Event briefs while their feature flag stays
off; it does not define or persist an event identifier.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "memory_event_baseline.json"


def _baseline() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _trace_records(uid: str, char_id: str) -> list[dict]:
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope

    trace_dir = resolve_path(MemoryScope.reality_scope(uid, char_id), "recall_trace")
    return [json.loads(line) for path in trace_dir.glob("*.jsonl") for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_memory_event_baseline_fixture_has_all_boundary_cases():
    baseline = _baseline()
    scenarios = {item["id"]: item for item in baseline["scenarios"]}

    assert baseline["fixture_version"] == "memory-event-baseline-v1"
    assert {"owner_chat", "scheduler_assistant_only", "desktop_owner_chat", "mobile_owner_chat", "media_message", "dream_isolated", "stage_isolated", "web_echo_isolated", "coplay_isolated", "same_uid_second_character"} <= scenarios.keys()
    assert scenarios["media_message"]["media"] == ["image", "file"]
    assert scenarios["same_uid_second_character"]["character_id"] == TEST_PEER_CHAR_ID
    assert all(not scenarios[name]["memory_eligible"] for name in ("dream_isolated", "stage_isolated", "web_echo_isolated", "coplay_isolated"))


@pytest.mark.parametrize("temporal_projection_fails", [False, True])
def test_legacy_memory_chain_golden_shape_and_character_isolation(sandbox, monkeypatch, temporal_projection_fails):
    from core.memory import event_log, short_term
    from core.memory.fixation_pipeline import capture_turn
    from core.memory.scope import MemoryScope
    from core.pipeline import Pipeline
    from core.character_loader import load as load_character
    from core.lore_engine import LoreEngine
    from core.write_envelope import stamp_user_chat
    import core.memory.embedding as embedding

    baseline = _baseline()
    uid = baseline["owner_uid"]
    canonical_turn = baseline["canonical_turn"]
    turn_id = canonical_turn["turn_id"]
    user_text = "baseline owner message about a placeholder project"
    reply_text = "baseline assistant reply about that placeholder project"
    peer_text = "peer character private placeholder memory"

    # Keep fetch_context deterministic and fully offline.
    async def _offline_embed(_texts):
        raise RuntimeError("memory-event baseline runs without embeddings")

    monkeypatch.setattr(embedding, "embed", _offline_embed)
    if temporal_projection_fails:
        def unavailable_projection(*args):
            raise OSError("fixture projection read failed")

        monkeypatch.setattr("core.memory.temporal_facts.project", unavailable_projection)

    assert capture_turn(uid, user_text, reply_text, turn_id=turn_id, char_id=TEST_CHAR_ID, envelope=stamp_user_chat()) == turn_id
    capture_turn(uid, user_text, peer_text, turn_id=f"{uid}_1700000000001", char_id=TEST_PEER_CHAR_ID, envelope=stamp_user_chat())

    history = short_term.load_for_prompt(uid, char_id=TEST_CHAR_ID)
    assert [entry["role"] for entry in history] == ["user", "assistant"]
    assert all(set(baseline["golden"]["history_entry_required_keys"]) <= entry.keys() for entry in history)
    assert {entry["_turn_id"] for entry in history} == {turn_id}
    assert peer_text not in "\n".join(entry["content"] for entry in history)

    event_result, event_trace = asyncio.run(
        event_log.search(uid, "placeholder project", char_id=TEST_CHAR_ID, return_trace=True)
    )
    assert user_text in event_result
    assert reply_text in event_result
    assert isinstance(event_trace, list)

    pipeline = Pipeline(load_character(TEST_CHAR_ID), LoreEngine(), active_character_id=TEST_CHAR_ID)
    context = asyncio.run(
        pipeline.context.fetch(
            user_id=uid,
            content="placeholder project",
            frozen_scope=MemoryScope.reality_scope(uid, TEST_CHAR_ID),
        )
    )
    assert set(baseline["golden"]["fetch_context_required_keys"]) <= context.keys()
    assert reply_text in "\n".join(item["content"] for item in context["history"])
    assert peer_text not in json.dumps(context, ensure_ascii=True, default=str)

    records = _trace_records(uid, TEST_CHAR_ID)
    assert records
    assert set(baseline["golden"]["recall_trace_required_keys"]) <= records[-1].keys()
    assert records[-1]["uid"] == uid
    assert records[-1]["char_id"] == TEST_CHAR_ID

    # Existing transport contract: incoming identifiers are not memory keys;
    # desktop/mobile assistant frames project the canonical post-process turn id.
    assert canonical_turn["transport_msg_id"] != turn_id
    assert canonical_turn["assistant_msg_id"] == turn_id


def test_scheduler_assistant_only_turn_preserves_legacy_short_term_shape(sandbox):
    from core.memory import event_log, short_term
    from core.memory.fixation_pipeline import capture_turn
    from core.write_envelope import stamp_sensor

    uid = _baseline()["owner_uid"]
    turn_id = f"{uid}_1700000000002"
    assistant_text = "baseline scheduler-only assistant message"
    capture_turn(uid, "", assistant_text, turn_id=turn_id, trigger_name="sensor_aware", char_id=TEST_CHAR_ID, envelope=stamp_sensor())

    history = short_term.load_for_prompt(uid, char_id=TEST_CHAR_ID)
    assert [(entry["role"], entry["content"]) for entry in history] == [("assistant", assistant_text)]
    event_result = asyncio.run(event_log.search(uid, "scheduler-only", char_id=TEST_CHAR_ID))
    assert assistant_text in event_result
