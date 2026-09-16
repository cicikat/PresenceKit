import asyncio
import json
from pathlib import Path

import pytest

from core.data_paths import DEFAULT_CHAR_ID
from core.dream.rpg_engine import RpgKernelError, apply_proposal, rebuild_snapshot
from core.dream.rpg_corrections import branch, clarify, retcon
from core.dream import rpg_store
from core.dream.rpg_store import read_dice, read_events


_UID = "rpg_engine_user"


def _session():
    from core.dream.dream_pipeline import enter_dream
    result = asyncio.run(enter_dream(_UID, char_id=DEFAULT_CHAR_ID, dream_mode="rpg", script_id="prison_demo"))
    assert result["ok"]
    return result["dream_id"]


def _roll(request_id="request_roll"):
    return {"request_id": request_id, "decision": "roll", "check_type": "observe", "reason_code": "test", "scene_id": "arrival", "roll_spec": {"dice_count": 1, "dice_sides": 6, "modifier": 0, "dc": 4}, "outcome_branches": {outcome: {"projections": {"public": [{"fact_id": "door", "value": outcome}], "player": [], "character": [], "kp_private": []}, "scene_updates": []} for outcome in ("critical_failure", "failure", "success_with_cost", "success", "critical_success")}, "character_should_respond": False}


def test_roll_is_audited_once_and_request_is_idempotent(sandbox):
    dream_id = _session()
    first = apply_proposal(_UID, dream_id, _roll(), expected_revision=0, char_id=DEFAULT_CHAR_ID)
    second = apply_proposal(_UID, dream_id, _roll(), expected_revision=0, char_id=DEFAULT_CHAR_ID)
    assert first["outcome"] in {"critical_failure", "failure", "success_with_cost", "success", "critical_success"}
    assert second["idempotent"] is True
    assert len(read_dice(_UID, dream_id, char_id=DEFAULT_CHAR_ID)) == 1
    assert len(read_events(_UID, dream_id, char_id=DEFAULT_CHAR_ID)) == 1


def test_auto_and_reject_write_resolution_without_fake_dice(sandbox):
    dream_id = _session()
    automatic = {"request_id": "auto", "decision": "automatic_success", "check_type": "move", "reason_code": "obvious", "outcome_branches": {"success": {"projections": {"public": [], "player": [], "character": [], "kp_private": []}}}}
    result = apply_proposal(_UID, dream_id, automatic, expected_revision=0, char_id=DEFAULT_CHAR_ID)
    assert result["outcome"] == "success"
    reject = {"request_id": "reject", "decision": "reject", "check_type": "move", "reason_code": "impossible"}
    result = apply_proposal(_UID, dream_id, reject, expected_revision=1, char_id=DEFAULT_CHAR_ID)
    assert result["outcome"] == "rejected"
    assert read_dice(_UID, dream_id, char_id=DEFAULT_CHAR_ID) == []


def test_invalid_proposal_and_revision_fail_closed(sandbox):
    dream_id = _session()
    invalid = _roll()
    invalid["unexpected"] = True
    with pytest.raises(RpgKernelError, match="RPG_INVALID_PROPOSAL"):
        apply_proposal(_UID, dream_id, invalid, expected_revision=0, char_id=DEFAULT_CHAR_ID)
    with pytest.raises(RpgKernelError, match="RPG_REVISION_CONFLICT"):
        apply_proposal(_UID, dream_id, _roll(), expected_revision=4, char_id=DEFAULT_CHAR_ID)
    core, health = rpg_store.load(_UID, dream_id, char_id=DEFAULT_CHAR_ID)
    assert health == "ok" and core is not None
    assert rpg_store.observability(core, health=health, recovery_source="test")["invalid_proposal_count"] == 1


def test_pending_dice_receipt_reuses_the_existing_roll_after_event_failure(sandbox, monkeypatch):
    dream_id = _session()
    original_append = rpg_store.append_event
    monkeypatch.setattr(rpg_store, "append_event", lambda *args, **kwargs: False)
    with pytest.raises(RpgKernelError, match="RPG_EVENT_WRITE_FAILED"):
        apply_proposal(_UID, dream_id, _roll("pending_roll"), expected_revision=0, char_id=DEFAULT_CHAR_ID)
    first_audit = read_dice(_UID, dream_id, char_id=DEFAULT_CHAR_ID)
    assert len(first_audit) == 1
    monkeypatch.setattr(rpg_store, "append_event", original_append)
    recovered = apply_proposal(_UID, dream_id, _roll("pending_roll"), expected_revision=0, char_id=DEFAULT_CHAR_ID)
    assert recovered["idempotent"] is False
    assert len(read_dice(_UID, dream_id, char_id=DEFAULT_CHAR_ID)) == 1
    assert read_dice(_UID, dream_id, char_id=DEFAULT_CHAR_ID)[0] == first_audit[0]


def test_event_before_core_write_recovers_without_a_second_roll(sandbox, monkeypatch):
    dream_id = _session()
    original_save = rpg_store.save
    calls = 0

    def fail_first_save(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return False, "write_failed"
        return original_save(*args, **kwargs)

    monkeypatch.setattr(rpg_store, "save", fail_first_save)
    with pytest.raises(RpgKernelError, match="RPG_COMMIT_UNCERTAIN"):
        apply_proposal(_UID, dream_id, _roll("core_crash"), expected_revision=0, char_id=DEFAULT_CHAR_ID)
    monkeypatch.setattr(rpg_store, "save", original_save)
    recovered = apply_proposal(_UID, dream_id, _roll("core_crash"), expected_revision=0, char_id=DEFAULT_CHAR_ID)
    core, health = rpg_store.load(_UID, dream_id, char_id=DEFAULT_CHAR_ID)
    assert recovered["idempotent"] is True
    assert health == "ok" and core is not None and core.status == "active" and core.scene_revision == 1
    assert len(read_dice(_UID, dream_id, char_id=DEFAULT_CHAR_ID)) == 1
    assert len(read_events(_UID, dream_id, char_id=DEFAULT_CHAR_ID)) == 1


def test_corrupt_ledger_fails_closed_and_never_becomes_an_empty_log(sandbox):
    dream_id = _session()
    rpg_store.events_path(_UID, dream_id, char_id=DEFAULT_CHAR_ID).write_text("{broken\n", encoding="utf-8")
    with pytest.raises(RpgKernelError, match="RPG_LEDGER_INVALID"):
        apply_proposal(_UID, dream_id, _roll("corrupt"), expected_revision=0, char_id=DEFAULT_CHAR_ID)
    core, health = rpg_store.load(_UID, dream_id, char_id=DEFAULT_CHAR_ID)
    assert health == "ok" and core is not None and core.status == "uncertain"


def test_cross_session_ledger_row_fails_closed(sandbox):
    dream_id = _session()
    rpg_store.append_event(_UID, dream_id, {"event_id": "evt_bad", "dream_id": "other_dream", "request_id": "bad", "request_digest": "x", "branch_id": "root", "seq": 1, "revision": 1, "core_after": {"scene_revision": 1}}, char_id=DEFAULT_CHAR_ID)
    with pytest.raises(RpgKernelError, match="RPG_LEDGER_INVALID"):
        apply_proposal(_UID, dream_id, _roll("cross_session"), expected_revision=0, char_id=DEFAULT_CHAR_ID)


def test_knowledge_downgrade_is_rejected_before_an_automatic_resolution(sandbox):
    dream_id = _session()
    known = {"request_id": "known", "decision": "automatic_success", "check_type": "observe", "reason_code": "test", "outcome_branches": {"success": {"projections": {"public": [], "player": [], "character": [{"fact_id": "suspect", "value": "seen", "knowledge": "known"}], "kp_private": []}}}}
    apply_proposal(_UID, dream_id, known, expected_revision=0, char_id=DEFAULT_CHAR_ID)
    downgrade = {"request_id": "downgrade", "decision": "automatic_success", "check_type": "observe", "reason_code": "test", "outcome_branches": {"success": {"projections": {"public": [], "player": [], "character": [{"fact_id": "suspect", "value": "seen", "knowledge": "unknown"}], "kp_private": []}}}}
    with pytest.raises(RpgKernelError, match="RPG_KNOWLEDGE_CONFLICT"):
        apply_proposal(_UID, dream_id, downgrade, expected_revision=1, char_id=DEFAULT_CHAR_ID)


def test_observability_is_content_free_and_kernel_has_no_reality_or_llm_dependencies(sandbox):
    dream_id = _session()
    proposal = _roll("redacted")
    proposal["outcome_branches"]["success"]["projections"]["kp_private"] = [{"fact_id": "secret", "value": "never-expose-this", "knowledge": "known"}]
    apply_proposal(_UID, dream_id, proposal, expected_revision=0, char_id=DEFAULT_CHAR_ID)
    core, health = rpg_store.load(_UID, dream_id, char_id=DEFAULT_CHAR_ID)
    payload = json.dumps(rpg_store.observability(core, health=health, recovery_source="test"))
    parsed = json.loads(payload)

    def _tokens(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                yield str(key)
                yield from _tokens(value)
        elif isinstance(obj, list):
            for item in obj:
                yield from _tokens(item)
        elif obj is not None:
            yield str(obj)

    tokens = set(_tokens(parsed))
    for secret in ("never-expose-this", "seed", "faces", "dc", "modifier"):
        assert secret not in tokens
    assert str(rpg_store.session_dir(_UID, dream_id, char_id=DEFAULT_CHAR_ID)) not in payload
    source = Path(__file__).parents[1].joinpath("core", "dream", "rpg_engine.py").read_text(encoding="utf-8")
    for forbidden in ("llm_client", "tool_dispatcher", "event_log", "afterglow", "stimulus"):
        assert forbidden not in source


def test_snapshot_rebuild_and_append_only_corrections(sandbox):
    dream_id = _session()
    result = apply_proposal(_UID, dream_id, _roll(), expected_revision=0, char_id=DEFAULT_CHAR_ID)
    before = list(read_events(_UID, dream_id, char_id=DEFAULT_CHAR_ID))
    snapshot = rebuild_snapshot(_UID, dream_id, char_id=DEFAULT_CHAR_ID)
    assert snapshot["shared_facts"]["door"]["value"] == result["outcome"]
    clar = clarify(_UID, dream_id, result["round_id"], "clarify", request_id="clarify_1", expected_revision=1, char_id=DEFAULT_CHAR_ID)
    repeat_clar = clarify(_UID, dream_id, result["round_id"], "clarify", request_id="clarify_1", expected_revision=1, char_id=DEFAULT_CHAR_ID)
    branched = branch(_UID, dream_id, result["round_id"], "alternate", request_id="branch_1", expected_revision=2, char_id=DEFAULT_CHAR_ID)
    assert clar["revision"] == 2 and repeat_clar["idempotent"] is True and branched["branch_id"].startswith("branch_")
    assert len(read_events(_UID, dream_id, char_id=DEFAULT_CHAR_ID)) == len(before) + 2
    assert len(read_dice(_UID, dream_id, char_id=DEFAULT_CHAR_ID)) == 1
    with pytest.raises(RpgKernelError, match="RPG_REVISION_CONFLICT"):
        retcon(_UID, dream_id, result["round_id"], "late", request_id="retcon_1", expected_revision=2, char_id=DEFAULT_CHAR_ID)
