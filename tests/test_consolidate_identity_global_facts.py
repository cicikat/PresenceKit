"""
tests/test_consolidate_identity_global_facts.py — Brief 89 §1: identity 分流

覆盖：
  1. user_facts.apply_global_facts_patch：接受/拒绝/截断/同值跳过/provenance
     （consolidate_to_identity 与 event_log_salvage 共享的落盘入口）
  2. consolidate_to_identity：可选 global_facts 段解析 + 落盘 + 主产物零回归
"""
from __future__ import annotations
from tests.fixtures.public_assets import TEST_CHAR_ID

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

UID_PREFIX = "gf_identity"


# ═══════════════════════════════════════════════════════════════════════════
# 1. user_facts.apply_global_facts_patch
# ═══════════════════════════════════════════════════════════════════════════

def test_apply_patch_writes_allowed_keys_and_provenance(sandbox):
    from core.memory import user_facts as uf
    from core.memory import provenance_log

    uid = f"{UID_PREFIX}_basic"
    uf.apply_global_facts_patch(
        uid, TEST_CHAR_ID,
        [{"key": "timezone", "value": "Asia/Shanghai"}],
        trigger_signal="test_trigger",
    )

    assert uf.load_user_facts(uid)["timezone"] == "Asia/Shanghai"
    records = provenance_log.query(uid, TEST_CHAR_ID, artifact="user_facts")
    assert len(records) == 1
    assert records[0]["field"] == "timezone"
    assert records[0]["after_gist"] == "Asia/Shanghai"


def test_apply_patch_rejects_denied_key_and_warns(sandbox, caplog):
    from core.memory import user_facts as uf

    uid = f"{UID_PREFIX}_denied"
    with caplog.at_level("WARNING", logger="core.memory.user_facts"):
        uf.apply_global_facts_patch(uid, TEST_CHAR_ID, [{"key": "nickname", "value": "宝宝"}])

    assert "nickname" not in uf.load_user_facts(uid)
    assert any("rejected keys" in r.message for r in caplog.records)


def test_apply_patch_denied_key_no_provenance(sandbox):
    from core.memory import user_facts as uf
    from core.memory import provenance_log

    uid = f"{UID_PREFIX}_denied_no_prov"
    uf.apply_global_facts_patch(uid, TEST_CHAR_ID, [{"key": "impression", "value": "warm"}])

    records = provenance_log.query(uid, TEST_CHAR_ID, artifact="user_facts")
    assert records == []


def test_apply_patch_truncates_over_three(sandbox):
    from core.memory import user_facts as uf

    uid = f"{UID_PREFIX}_cap"
    items = [
        {"key": "timezone", "value": "A"},
        {"key": "device_os", "value": "B"},
        {"key": "preferred_language", "value": "C"},
        {"key": "known_projects", "value": ["D"]},
    ]
    uf.apply_global_facts_patch(uid, TEST_CHAR_ID, items)

    facts = uf.load_user_facts(uid)
    assert "known_projects" not in facts, "第 4 条应被截断，不落盘"
    assert facts.get("timezone") == "A"
    assert facts.get("device_os") == "B"
    assert facts.get("preferred_language") == "C"


def test_apply_patch_same_value_skips_write_and_provenance(sandbox):
    from core.memory import user_facts as uf
    from core.memory import provenance_log

    uid = f"{UID_PREFIX}_samevalue"
    uf.save_user_facts(uid, {"timezone": "Asia/Shanghai"})
    before = provenance_log.query(uid, TEST_CHAR_ID, artifact="user_facts")

    uf.apply_global_facts_patch(uid, TEST_CHAR_ID, [{"key": "timezone", "value": "Asia/Shanghai"}])

    records = provenance_log.query(uid, TEST_CHAR_ID, artifact="user_facts")
    assert records == before, "同值重写不应新增 provenance"


def test_apply_patch_empty_list_is_noop(sandbox):
    from core.memory import user_facts as uf

    uid = f"{UID_PREFIX}_empty"
    uf.apply_global_facts_patch(uid, TEST_CHAR_ID, [])
    assert uf.load_user_facts(uid) == {}


def test_apply_patch_malformed_items_do_not_raise(sandbox):
    from core.memory import user_facts as uf

    uid = f"{UID_PREFIX}_malformed"
    # Not a list at all
    uf.apply_global_facts_patch(uid, TEST_CHAR_ID, "not-a-list")  # type: ignore[arg-type]
    # List with junk entries mixed with one valid entry (kept within the ≤3 cap)
    uf.apply_global_facts_patch(uid, TEST_CHAR_ID, [
        "junk", {"key": 5, "value": "x"}, {"key": "device_os", "value": "Linux"},
    ])
    assert uf.load_user_facts(uid).get("device_os") == "Linux"


# ═══════════════════════════════════════════════════════════════════════════
# 2. consolidate_to_identity global_facts 分流
# ═══════════════════════════════════════════════════════════════════════════

def _identity_response(global_facts=None):
    dims = {
        "trust_pattern": {"text": "她信任建立较快", "confidence": 0.7, "evidence_count": 5, "counter_evidence_count": 0},
    }
    if global_facts is not None:
        dims["global_facts"] = global_facts
    return json.dumps(dims, ensure_ascii=False)


def _seed_episode(uid, char_id):
    from core.memory import episodic_memory as em
    em._save_memories(uid, [{
        "id": "ep1",
        "narrative_summary": "聊到了最近的工作",
        "emotion_peak": "neutral",
        "strength": 0.8,
        "consolidated_at": None,
        "timestamp": 0.0,
    }], char_id=char_id)


def test_consolidate_applies_global_facts(sandbox):
    from core.memory.fixation_pipeline import consolidate_to_identity
    from core.memory import user_facts as uf
    from core.memory import provenance_log

    uid = f"{UID_PREFIX}_identity_ok"
    char_id = TEST_CHAR_ID
    _seed_episode(uid, char_id)

    llm = MagicMock()
    llm.chat = AsyncMock(return_value=_identity_response(
        global_facts=[{"key": "device_os", "value": "Windows"}]
    ))

    result = asyncio.run(consolidate_to_identity(uid, llm, char_id=char_id))

    assert result is True
    assert uf.load_user_facts(uid).get("device_os") == "Windows"
    records = provenance_log.query(uid, char_id, artifact="user_facts")
    assert any(r["field"] == "device_os" for r in records)


def test_consolidate_denied_global_facts_key_rejected(sandbox):
    from core.memory.fixation_pipeline import consolidate_to_identity
    from core.memory import user_facts as uf

    uid = f"{UID_PREFIX}_identity_denied"
    char_id = TEST_CHAR_ID
    _seed_episode(uid, char_id)

    llm = MagicMock()
    llm.chat = AsyncMock(return_value=_identity_response(
        global_facts=[{"key": "nickname", "value": "宝宝"}]
    ))

    result = asyncio.run(consolidate_to_identity(uid, llm, char_id=char_id))

    assert result is True
    assert "nickname" not in uf.load_user_facts(uid)


def test_consolidate_missing_global_facts_is_noop(sandbox):
    from core.memory.fixation_pipeline import consolidate_to_identity
    from core.memory import user_facts as uf

    uid = f"{UID_PREFIX}_identity_missing"
    char_id = TEST_CHAR_ID
    _seed_episode(uid, char_id)

    llm = MagicMock()
    llm.chat = AsyncMock(return_value=_identity_response(global_facts=None))

    result = asyncio.run(consolidate_to_identity(uid, llm, char_id=char_id))

    assert result is True
    assert uf.load_user_facts(uid) == {}


def test_consolidate_malformed_global_facts_does_not_break_identity(sandbox):
    """global_facts 是字符串（格式错误）时不得影响 identity 主产物落盘。"""
    from core.memory.fixation_pipeline import consolidate_to_identity
    from core.memory import user_identity as ui

    uid = f"{UID_PREFIX}_identity_badgf"
    char_id = TEST_CHAR_ID
    _seed_episode(uid, char_id)

    dims = {
        "trust_pattern": {"text": "她信任建立较快", "confidence": 0.7, "evidence_count": 5, "counter_evidence_count": 0},
        "global_facts": "not-a-list-oops",
    }
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=json.dumps(dims, ensure_ascii=False))

    result = asyncio.run(consolidate_to_identity(uid, llm, char_id=char_id))

    assert result is True
    saved = asyncio.run(ui.load(uid, char_id=char_id))
    assert saved.get("trust_pattern", {}).get("text") == "她信任建立较快"
