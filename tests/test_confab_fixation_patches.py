from tests.fixtures.public_assets import TEST_CHAR_ID
"""
tests/test_confab_fixation_patches.py — 虚构记忆固化回路补丁验收

覆盖三个修复：
  Patch B: 触发轮 mid_term 条目不晋升为 episodic
           (fixation_pipeline.reflect_to_episodic 过滤 is_trigger_turn=True)
  Patch A: 核心记忆去重合并——与现有 is_core 相似时不新建 episode
           (fixation_pipeline._find_core_duplicate + reflect_to_episodic 分支)
  Patch C: is_core 完全排除于 retrieve_fallback (见 test_episodic_fallback_cooldown.py)
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.memory.episodic_memory as em
from core.memory.episodic_memory import write_episode, _load_memories
from core.memory.fixation_pipeline import (
    _find_core_duplicate,
    reflect_to_episodic,
    summarize_to_midterm,
)


# ─── shared fixtures ─────────────────────────────────────────────────────────

_UID = "confab_fix_uid"
_CHAR = TEST_CHAR_ID
_NOW = 1_800_000_000.0

_DEFAULT_EPISODE_JSON = json.dumps({
    "raw_facts": ["用户提到了生日", "用户独自"],
    "topic_keywords": ["生日", "哭泣"],
    "emotion_peak": "sad",
    "emotion_texture": "沉沉的",
    "emotion_arc": "从悲伤到平静",
    "user_state": "sad_alone",
    "narrative_summary": "用户生日独自哭泣",
    "is_closure": False,
    "closure_keywords": [],
    "temporal_ref": "none",
    "event_time_hint": "",
    "strength": 0.85,
})


@pytest.fixture
def fake_llm():
    llm = MagicMock()
    llm.summarize_turn = AsyncMock(return_value="用户提到生日独自哭泣")
    llm.chat = AsyncMock(return_value=_DEFAULT_EPISODE_JSON)
    return llm


@pytest.fixture(autouse=True)
def patch_llm_client(fake_llm):
    with patch("core.llm_client", fake_llm, create=True):
        yield fake_llm


@pytest.fixture(autouse=True)
def reset_llm_validator_counter():
    from core import llm_output_validator
    llm_output_validator.reset = MagicMock()
    llm_output_validator.record_failure = MagicMock()
    yield


def _ep(ep_id, summary, keywords=None, is_core=False, strength=0.8, **kw):
    return {
        "id": ep_id,
        "timestamp": _NOW - 86400,
        "occurred_at": _NOW - 86400,
        "narrative_summary": summary,
        "summary": summary,
        "topic_keywords": keywords or [],
        "tags": keywords or [],
        "raw_facts": ["用户提到了" + summary],
        "emotion_peak": "sad",
        "emotion_texture": "",
        "emotion_arc": "",
        "user_state": "",
        "temporal_ref": "none",
        "event_time": None,
        "expires_at": None,
        "strength": strength,
        "status": "open",
        "is_core": is_core,
        "retrieval_count": 0,
        "last_retrieved": None,
        "resolved_at": None,
        "resolved_by": None,
        "source_mid_ids": [],
        "consolidated_at": None,
        **kw,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Patch B: trigger 轮不铸造 episodic
# ═══════════════════════════════════════════════════════════════════════════════

class TestTriggerTurnGuard:
    """reflect_to_episodic 跳过 is_trigger_turn=True 的 mid_term 条目。"""

    def test_trigger_turn_mid_term_skipped(self, sandbox):
        """is_trigger_turn=True 的 mid_term 条目应被 reflect 跳过，不产生新 episodic。"""
        from core.memory import mid_term as _mt

        # 写入一条 trigger 轮的 mid_term
        mid_id = "mt_trigger_001"
        _mt.append(
            _UID, "sensor_aware 触发时叶瑄独白内容",
            tags=["生日"],
            mid_id=mid_id,
            source_turn_id=f"{_UID}_1800000000000",
            char_id=_CHAR,
            is_trigger_turn=True,
        )

        result = asyncio.run(
            reflect_to_episodic(_UID, [mid_id], trigger="eager", char_id=_CHAR)
        )

        assert result is None, "trigger 轮不应产生新 episodic"
        mems = _load_memories(_UID, char_id=_CHAR)
        assert len(mems) == 0, "episodic 不应有任何新条目"

    def test_normal_turn_still_promoted(self, sandbox):
        """非 trigger 轮的 mid_term 条目正常晋升。"""
        from core.memory import mid_term as _mt

        mid_id = "mt_normal_001"
        _mt.append(
            _UID, "用户说了生日快乐",
            tags=["生日"],
            mid_id=mid_id,
            source_turn_id=f"{_UID}_1800000001000",
            char_id=_CHAR,
            is_trigger_turn=False,  # 正常对话轮
        )

        result = asyncio.run(
            reflect_to_episodic(_UID, [mid_id], trigger="eager", char_id=_CHAR)
        )

        assert result is not None, "正常对话轮应晋升为 episodic"
        mems = _load_memories(_UID, char_id=_CHAR)
        assert len(mems) == 1

    def test_trigger_turn_field_stored_in_midterm(self, sandbox):
        """is_trigger_turn 字段应被正确持久化到 mid_term。"""
        from core.memory import mid_term as _mt

        _mt.append(
            _UID, "触发轮摘要",
            tags=[],
            mid_id="mt_store_check",
            source_turn_id=f"{_UID}_1800000002000",
            char_id=_CHAR,
            is_trigger_turn=True,
        )

        events = _mt.load(_UID, char_id=_CHAR)
        assert any(e.get("mid_id") == "mt_store_check" and e.get("is_trigger_turn") is True
                   for e in events), "is_trigger_turn 应持久化到 mid_term"

    def test_mixed_batch_only_normal_promoted(self, sandbox):
        """同一批次包含触发轮和正常轮时，只有正常轮晋升。"""
        from core.memory import mid_term as _mt

        trigger_mid = "mt_mix_trigger"
        normal_mid = "mt_mix_normal"

        _mt.append(_UID, "触发轮",
                   tags=[], mid_id=trigger_mid,
                   source_turn_id=f"{_UID}_1800000003000",
                   char_id=_CHAR, is_trigger_turn=True)
        _mt.append(_UID, "正常轮对话",
                   tags=["日常"], mid_id=normal_mid,
                   source_turn_id=f"{_UID}_1800000004000",
                   char_id=_CHAR, is_trigger_turn=False)

        result = asyncio.run(
            reflect_to_episodic(_UID, [trigger_mid, normal_mid], trigger="eager", char_id=_CHAR)
        )

        # 正常轮晋升成功；触发轮被过滤掉
        assert result is not None
        mems = _load_memories(_UID, char_id=_CHAR)
        assert len(mems) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Patch A: 核心记忆去重合并
# ═══════════════════════════════════════════════════════════════════════════════

class TestCoreDedupHelper:
    """_find_core_duplicate 单元测试。"""

    def test_returns_none_for_empty_list(self):
        assert _find_core_duplicate(["生日"], "生日哭泣", []) is None

    def test_no_match_non_core(self):
        eps = [_ep("ep1", "生日独自哭泣", keywords=["生日", "哭泣"], is_core=False)]
        assert _find_core_duplicate(["生日", "哭泣"], "生日独自哭泣", eps) is None

    def test_keyword_intersection_2_matches(self):
        eps = [_ep("ep1", "生日独自哭泣", keywords=["生日", "哭泣", "窗边"], is_core=True)]
        result = _find_core_duplicate(["生日", "哭泣", "月光"], "另一段生日记忆", eps)
        assert result is not None and result["id"] == "ep1"

    def test_keyword_intersection_1_no_match_below_jaccard(self):
        eps = [_ep("ep1", "某核心记忆", keywords=["生日", "其他1", "其他2"], is_core=True)]
        # intersection=1, union=5, jaccard=0.2 — 不满足 ≥0.5
        result = _find_core_duplicate(["生日", "无关1", "无关2"], "完全不同摘要", eps)
        assert result is None

    def test_summary_similarity_matches(self):
        eps = [_ep("ep1", "用户生日独自哭泣", keywords=["无关词"], is_core=True)]
        result = _find_core_duplicate(["其他"], "用户生日独自哭泣", eps)
        assert result is not None and result["id"] == "ep1"

    def test_resolved_core_not_matched(self):
        ep = _ep("ep1", "生日独自哭泣", keywords=["生日", "哭泣"], is_core=True)
        ep["status"] = "resolved"
        assert _find_core_duplicate(["生日", "哭泣"], "生日独自哭泣", [ep]) is None


class TestCoreDedupInReflect:
    """reflect_to_episodic 遇到 is_core 相似体时合并而非新建。"""

    def test_similar_core_merges_no_new_episode(self, sandbox):
        """已有 is_core 相似 episode → reflect 返回现有 id，不新建 episode。"""
        from core.memory import mid_term as _mt

        # 预写一条 is_core episode（模拟原始真实记忆）
        existing = _ep("ep_original_core", "用户生日独自哭泣",
                       keywords=["生日", "哭泣"], is_core=True, strength=1.0)
        write_episode(_UID, existing, char_id=_CHAR)

        # 新来一条 mid_term，描述同一件事（来自正常对话轮，不是触发轮）
        mid_id = "mt_dedup_001"
        _mt.append(_UID, "用户说了生日哭泣的事",
                   tags=["生日", "哭泣"], mid_id=mid_id,
                   source_turn_id=f"{_UID}_1800010000000",
                   char_id=_CHAR, is_trigger_turn=False)

        result = asyncio.run(
            reflect_to_episodic(_UID, [mid_id], trigger="eager", char_id=_CHAR)
        )

        # 返回的是现有 episode 的 id（合并路径）
        assert result == "ep_original_core"
        # episodic 总数仍为 1，没有新建
        mems = _load_memories(_UID, char_id=_CHAR)
        assert len(mems) == 1
        # retrieval_count 已自增
        core_ep = mems[0]
        assert core_ep.get("retrieval_count", 0) >= 1

    def test_non_similar_creates_new_episode(self, sandbox):
        """与现有 is_core 不相似时正常新建 episode。"""
        from core.memory import mid_term as _mt

        # 预写 is_core episode（完全不同话题）
        existing = _ep("ep_unrelatd_core", "用户谈论学校考试",
                       keywords=["考试", "学校"], is_core=True, strength=0.9)
        write_episode(_UID, existing, char_id=_CHAR)

        mid_id = "mt_no_dedup_001"
        _mt.append(_UID, "用户说生日快乐",
                   tags=["生日", "哭泣"], mid_id=mid_id,
                   source_turn_id=f"{_UID}_1800020000000",
                   char_id=_CHAR, is_trigger_turn=False)

        result = asyncio.run(
            reflect_to_episodic(_UID, [mid_id], trigger="eager", char_id=_CHAR)
        )

        assert result is not None
        mems = _load_memories(_UID, char_id=_CHAR)
        assert len(mems) == 2, "不相似的 is_core 不应触发合并"


# ═══════════════════════════════════════════════════════════════════════════════
# Patch B: summarize_to_midterm trigger_name → is_trigger_turn 传播
# ═══════════════════════════════════════════════════════════════════════════════

class TestSummarizeToMidtermTriggerPropagation:
    """trigger_name 非空时，summarize_to_midterm 应将 is_trigger_turn=True 写入 mid_term。"""

    def test_trigger_name_sets_is_trigger_turn(self, sandbox):
        from core.memory import mid_term as _mt

        turn_id = f"{_UID}_1800030000000"
        asyncio.run(
            summarize_to_midterm(
                turn_id=turn_id,
                uid=_UID,
                user_msg="[触发: sensor_aware]",
                reply="叶瑄主动关心",
                tags=["关心"],
                emotion="neutral",
                char_id=_CHAR,
                trigger_name="sensor_aware",
            )
        )

        events = _mt.load(_UID, char_id=_CHAR)
        assert len(events) == 1
        assert events[0].get("is_trigger_turn") is True

    def test_no_trigger_name_not_marked(self, sandbox):
        from core.memory import mid_term as _mt

        turn_id = f"{_UID}_1800040000000"
        asyncio.run(
            summarize_to_midterm(
                turn_id=turn_id,
                uid=_UID,
                user_msg="用户：你好",
                reply="叶瑄的回复",
                tags=["日常"],
                emotion="happy",
                char_id=_CHAR,
                trigger_name="",  # 正常对话轮
            )
        )

        events = _mt.load(_UID, char_id=_CHAR)
        assert len(events) == 1
        assert not events[0].get("is_trigger_turn"), "正常对话轮不应标 is_trigger_turn"
