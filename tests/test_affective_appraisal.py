import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import time

import pytest

from core import affective_appraisal as appraisal
from core.memory import mood_state as ms
from core.mood_text import get_affect_text
from tests.test_affective_engine import proposal


@pytest.fixture(autouse=True)
def enable_engine(monkeypatch):
    monkeypatch.setattr(ms, "enabled", lambda: True)


@pytest.mark.asyncio
async def test_semantic_evaluation_uses_frozen_character_route_and_validates(sandbox, monkeypatch):
    calls = []
    monkeypatch.setattr("core.character_loader.load", lambda char: calls.append(char) or SimpleNamespace(
        system_prompt="Authored character", description="Background", personality="Reserved"))
    async def closed(request, messages, **kwargs):
        assert kwargs == {"call_category": "detect_emotion", "max_tokens": 500, "char_id": "other"}
        assert request.task_id == "affective_appraisal"
        assert "Reserved" in request.state["persona"]
        assert request.state["user_event"] == "An actual clarification"
        return json.dumps(proposal(anger=.3, concern=.6))
    monkeypatch.setattr("core.decision_contract.closed", closed)
    raw, status = await appraisal.evaluate("An actual clarification", char_id="other")
    assert raw["anger"] == .3 and status == "ready" and calls == ["other"]
    assert not sandbox.mood_state(char_id="other").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["neutral", "{}", '{"anger":NaN}'])
async def test_invalid_model_result_creates_no_state(sandbox, monkeypatch, raw):
    monkeypatch.setattr("core.character_loader.load", lambda char: SimpleNamespace(system_prompt="", description="", personality=""))
    monkeypatch.setattr("core.decision_contract.closed", AsyncMock(return_value=raw))
    result, status = await appraisal.evaluate("event", char_id="companion")
    assert result is None and status == "rejected"
    assert not sandbox.mood_state(char_id="companion").exists()


@pytest.mark.asyncio
async def test_timeout_and_cancellation(sandbox, monkeypatch):
    monkeypatch.setattr("core.character_loader.load", lambda char: SimpleNamespace(system_prompt="", description="", personality=""))
    gate = asyncio.Event()
    monkeypatch.setattr("core.decision_contract.closed", AsyncMock(side_effect=lambda *a, **k: gate.wait()))
    async def blocked(*args, **kwargs):
        await gate.wait()
    monkeypatch.setattr("core.decision_contract.closed", blocked)
    monkeypatch.setattr(appraisal, "WALL_SECONDS", .01)
    assert await appraisal.evaluate("event", char_id="companion") == (None, "timeout")
    task = asyncio.create_task(appraisal.evaluate("event", char_id="companion"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_late_inference_and_disabled_inflight_cannot_override_new_turn(sandbox, monkeypatch):
    now = time.time()
    ms.update("happy", char_id="companion", source_turn_id="a", source_at=now-2)
    ms.update("gentle", char_id="companion", source_turn_id="b", source_at=now-1)
    path = sandbox.mood_state(char_id="companion")
    before = path.read_bytes()
    ms.record_appraisal(proposal(anger=.9), char_id="companion", turn_id="a", occurred_at=now-2, evidence="old event", status="ready")
    assert before == path.read_bytes()
    monkeypatch.setattr(ms, "enabled", lambda: False)
    ms.record_appraisal(proposal(anger=.9), char_id="companion", turn_id="b", occurred_at=now-1, evidence="new event", status="ready")
    assert before == path.read_bytes()


def test_commit_inference_and_readable_prompt_without_engine_parameters(sandbox):
    now = time.time()
    ms.update("happy", char_id="companion", source_turn_id="a", source_at=now-1)
    state = ms.record_appraisal(proposal(anger=.9, concern=.7, strategy="suppress"), char_id="companion",
                                turn_id="a", occurred_at=now-1, evidence="Real user event", status="ready")
    text = get_affect_text(state)
    assert "不悦" in text and "关切" in text and "微笑" in text
    assert "0.9" not in text and "source_turn_id" not in text
    assert state["affect_events"][0]["evidence_excerpt"] == "Real user event"
    version = state["version"]
    assert ms.record_appraisal(proposal(joy=1), char_id="companion", turn_id="a", occurred_at=now-1,
                               evidence="duplicate", status="ready")["version"] == version


def test_native_closed_request_and_expression_are_independent():
    request, _ = appraisal.request_for("event", "persona", [])
    assert len(request.questions) < 32
    assert all(q.wire() for q in request.questions.values())
    from core.expression_signal import explicit_expression
    assert explicit_expression("<do>微笑</do><feel>有点生气</feel>") == "happy"
    assert explicit_expression("<feel>生气</feel><do>微笑</do>") == "happy"


def test_source_withdrawal_clears_derived_affect_and_revert_restores(sandbox):
    from core.memory.invalidation import invalidate_by_events, revert_invalidation
    from core.memory.scope import MemoryScope
    now = time.time()
    ms.update("happy", char_id="companion", source_turn_id="t1", source_at=now-1,
              appraisal=proposal(anger=.8))
    scope = MemoryScope.reality_scope("owner", "companion")
    result = invalidate_by_events(scope, ["t1:user"], reason="withdrawn", actor="owner")
    assert result["affective_events"] == 1
    assert not ms.load(char_id="companion")["affect"]["has_signal"]
    assert revert_invalidation(scope, result["invalidation_id"])["ok"]
    assert ms.load(char_id="companion")["affect"]["has_signal"]


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["trigger", "dream", "web", "coplay", "import", "debug", "group", "failed_capture"])
async def test_pipeline_rejects_non_reality_appraisal_inputs(sandbox, monkeypatch, blocked):
    from core.pipeline import Pipeline
    from core.write_envelope import stamp_user_chat, stamp_ingest, stamp_debug
    from core.memory.scope import MemoryScope
    monkeypatch.setattr("core.post_process.slow_queue.enqueue", lambda *a, **k: None)
    monkeypatch.setattr("core.pipeline._detect_dream_echo", lambda *a, **k: blocked == "dream")
    evaluator = AsyncMock(return_value=(proposal(anger=.9), "ready"))
    monkeypatch.setattr(appraisal, "evaluate", evaluator)
    env = stamp_ingest() if blocked == "import" else stamp_debug() if blocked == "debug" else stamp_user_chat()
    critical = {"char_id": "companion", "scope_payload": MemoryScope.reality_scope("owner", "companion").to_payload(),
                "turn_id": "t1", "should_update_profile": False, "profile_recent": [],
                "source_at": time.time()-1, "critical_written": blocked != "failed_capture",
                "expression_signal": "happy", "appraisal_input": "real event"}
    await Pipeline(SimpleNamespace(name="Companion"), None).post_process_slow(
        "owner", "event", "reply", critical, envelope=env,
        trigger_name="timer" if blocked == "trigger" else "", is_group=blocked == "group",
        web_echo=blocked == "web", coplay_echo=blocked == "coplay")
    evaluator.assert_not_called()


@pytest.mark.asyncio
async def test_expression_commits_before_network_and_network_holds_no_uid_lock(sandbox, monkeypatch):
    from core.pipeline import Pipeline
    from core.write_envelope import stamp_user_chat
    from core.memory.scope import MemoryScope
    from core.memory import locks
    gate, started = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr("core.post_process.slow_queue.enqueue", lambda *a, **k: None)
    monkeypatch.setattr("core.pipeline._detect_dream_echo", lambda *a, **k: False)
    async def evaluate(*args, **kwargs):
        started.set()
        await gate.wait()
        return proposal(anger=.8, concern=.6, strategy="suppress"), "ready"
    monkeypatch.setattr(appraisal, "evaluate", evaluate)
    critical = {"char_id": "companion", "scope_payload": MemoryScope.reality_scope("owner", "companion").to_payload(),
                "turn_id": "t1", "should_update_profile": False, "profile_recent": [],
                "source_at": time.time()-1, "critical_written": True,
                "expression_signal": "happy", "appraisal_input": "real event"}
    task = asyncio.create_task(Pipeline(SimpleNamespace(name="Companion"), None).post_process_slow(
        "owner", "event", "reply", critical, envelope=stamp_user_chat()))
    await asyncio.wait_for(started.wait(), 2)
    assert ms.get_current(char_id="companion") == "happy"
    async with asyncio.timeout(1):
        async with locks.uid_lock("owner"):
            pass
    gate.set()
    await task
    assert ms.load(char_id="companion")["affect"]["emotions"]["anger"] > .7
