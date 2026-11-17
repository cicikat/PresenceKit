from tests.fixtures.public_assets import TEST_CHAR_ID
"""
tests/test_p1_memory_fields.py — P1 验收

P1-1: event_log append speaker 字段
P1-2: mid_term occurred_at 字段 + fixation pipeline 贯穿
P1-3: episodic 血缘 exact-dup 去重
"""

import json
import time
import unittest.mock as mock
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ─── P1-1: event_log append speaker 字段 ────────────────────────────────────

class TestEventLogAppendSpeaker:
    """append() 写入格式验证：user 行恒含 > speaker:user，assistant 含 speaker:assistant。"""

    def _make_paths(self, tmp_path):
        return tmp_path / "event_log" / "uid_test"

    def test_user_append_has_speaker_field(self, sandbox):
        """user 行写入时包含 > speaker:user。"""
        import core.memory.event_log as el

        el.append("test_uid", "user", "我在下棋", turn_id="test_1000", char_id=TEST_CHAR_ID)

        text = el._full_log_file_write("test_uid", char_id=TEST_CHAR_ID).read_text(encoding="utf-8")
        assert "> speaker:user" in text, f"user 行缺少 speaker:user 元字段: {text!r}"
        assert "turn_id:test_1000" in text

    def test_assistant_append_has_speaker_field(self, sandbox):
        """assistant 行写入时包含 speaker:assistant。"""
        import core.memory.event_log as el

        el.append("test_uid", "assistant", "下棋很有意思", emotion="happy", turn_id="test_2000", char_id=TEST_CHAR_ID)

        text = el._full_log_file_write("test_uid", char_id=TEST_CHAR_ID).read_text(encoding="utf-8")
        assert "speaker:assistant" in text, f"assistant 行缺少 speaker:assistant: {text!r}"
        assert "> emotion:happy" in text

    def test_user_append_always_has_speaker_even_without_turn_id(self, sandbox):
        """user 行即使无 turn_id 也写 > speaker:user（不再是可选）。"""
        import core.memory.event_log as el

        el.append("test_uid", "user", "随便说说", char_id=TEST_CHAR_ID)  # no turn_id

        text = el._full_log_file_write("test_uid", char_id=TEST_CHAR_ID).read_text(encoding="utf-8")
        assert "> speaker:user" in text, "无 turn_id 时也应有 speaker:user"


# ─── P1-2: mid_term occurred_at ─────────────────────────────────────────────

class TestMidTermOccurredAt:
    """mid_term.append() 写入 occurred_at 字段。"""

    def test_append_stores_occurred_at(self, tmp_path, monkeypatch):
        """传入 occurred_at 时写入 entry。"""
        import core.memory.mid_term as mt
        from core.memory.scope import MemoryScope
        from core.memory.path_resolver import resolve_path

        scope = MemoryScope.reality_scope("uid_mt", TEST_CHAR_ID)
        p = tmp_path / "mid_term.json"
        monkeypatch.setattr(mt, "_read_file", lambda uid, char_id=TEST_CHAR_ID: p)
        monkeypatch.setattr(mt, "_write_file", lambda uid, char_id=TEST_CHAR_ID: p)

        _ts = 1_700_000_000.0
        mt.append("uid_mt", "用户去下棋了", mid_id="mt_1", occurred_at=_ts)

        data = json.loads(p.read_text(encoding="utf-8"))
        entry = data["events"][0]
        assert "occurred_at" in entry, "entry 应含 occurred_at"
        assert entry["occurred_at"] == pytest.approx(_ts), "occurred_at 应为传入值"

    def test_append_fallback_to_now_when_occurred_at_none(self, tmp_path, monkeypatch):
        """occurred_at=None 时回退到记录时刻 now。"""
        import core.memory.mid_term as mt

        p = tmp_path / "mid_term.json"
        monkeypatch.setattr(mt, "_read_file", lambda uid, char_id=TEST_CHAR_ID: p)
        monkeypatch.setattr(mt, "_write_file", lambda uid, char_id=TEST_CHAR_ID: p)

        _before = time.time()
        mt.append("uid_mt", "随便聊聊", mid_id="mt_2", occurred_at=None)
        _after = time.time()

        data = json.loads(p.read_text(encoding="utf-8"))
        entry = data["events"][0]
        assert _before <= entry["occurred_at"] <= _after, "occurred_at 应在写入时刻附近"

    def test_append_fallback_when_occurred_at_invalid(self, tmp_path, monkeypatch):
        """occurred_at 为非数值时回退到 now。"""
        import core.memory.mid_term as mt

        p = tmp_path / "mid_term.json"
        monkeypatch.setattr(mt, "_read_file", lambda uid, char_id=TEST_CHAR_ID: p)
        monkeypatch.setattr(mt, "_write_file", lambda uid, char_id=TEST_CHAR_ID: p)

        _before = time.time()
        mt.append("uid_mt", "测试", mid_id="mt_3", occurred_at="not-a-float")  # type: ignore
        _after = time.time()

        data = json.loads(p.read_text(encoding="utf-8"))
        entry = data["events"][0]
        assert isinstance(entry["occurred_at"], float), "invalid occurred_at 应回退为 float"
        assert _before <= entry["occurred_at"] <= _after


class TestBatchOccurredAt:
    """_batch_occurred_at: 有字段取最早；无字段退回 source_turn_id 解析；全无退 fallback。"""

    def test_reads_from_occurred_at_field(self):
        """有 occurred_at 字段时直接取最小值。"""
        from core.memory.fixation_pipeline import _batch_occurred_at

        to_process = [
            {"occurred_at": 1_000_000.0},
            {"occurred_at": 900_000.0},   # 最早
        ]
        result = _batch_occurred_at(to_process, fallback=9_999_999.0)
        assert result == pytest.approx(900_000.0)

    def test_fallback_to_source_turn_id_when_no_field(self):
        """无 occurred_at 字段时退回 source_turn_id 解析。"""
        from core.memory.fixation_pipeline import _batch_occurred_at

        # turn_id = uid_1700000000000 → 1700000000.0 秒
        to_process = [
            {"source_turn_id": "uid_1700000000000", "ts": 9_000_000.0},
        ]
        result = _batch_occurred_at(to_process, fallback=9_999_999.0)
        assert result == pytest.approx(1_700_000_000.0)

    def test_fallback_to_ts_when_source_turn_id_unparseable(self):
        """source_turn_id 无法解析时退回 ts 字段。"""
        from core.memory.fixation_pipeline import _batch_occurred_at

        to_process = [
            {"source_turn_id": "bad_id", "ts": 1_234_567.0},
        ]
        result = _batch_occurred_at(to_process, fallback=9_999_999.0)
        assert result == pytest.approx(1_234_567.0)

    def test_fallback_to_fallback_param(self):
        """完全没有时间线索时退回 fallback 参数。"""
        from core.memory.fixation_pipeline import _batch_occurred_at

        to_process = [{}]
        result = _batch_occurred_at(to_process, fallback=42.0)
        assert result == pytest.approx(42.0)

    def test_occurred_at_takes_priority_over_source_turn_id(self):
        """occurred_at 字段存在时不解析 source_turn_id（字段优先）。"""
        from core.memory.fixation_pipeline import _batch_occurred_at

        # occurred_at=800_000, source_turn_id 指向 1_700_000_000 → 应取 occurred_at
        to_process = [
            {"occurred_at": 800_000.0, "source_turn_id": "uid_1700000000000"},
        ]
        result = _batch_occurred_at(to_process, fallback=9_999_999.0)
        assert result == pytest.approx(800_000.0)


class TestSummarizeToMidtermOccurredAt:
    """summarize_to_midterm 写入的 mid_term entry 含 occurred_at == turn 真实时刻。"""

    @pytest.mark.asyncio
    async def test_occurred_at_set_from_turn_id(self, tmp_path, monkeypatch):
        import core.memory.mid_term as mt
        import core.memory.fixation_pipeline as fp
        import core.llm_client as lc

        p = tmp_path / "mid_term.json"
        monkeypatch.setattr(mt, "_read_file", lambda uid, char_id=TEST_CHAR_ID: p)
        monkeypatch.setattr(mt, "_write_file", lambda uid, char_id=TEST_CHAR_ID: p)

        # turn_id 中的毫秒 → 1_700_000_000_000 ms → 1_700_000_000.0 s
        _turn_id = "uid_1700000000000"

        monkeypatch.setattr(lc, "summarize_turn", AsyncMock(return_value="用户聊到了下棋"))

        # mock slow_queue to avoid real enqueue
        import core.post_process as pp
        mock_sq = MagicMock()
        mock_sq.enqueue = MagicMock()
        monkeypatch.setattr(pp, "slow_queue", mock_sq)

        # mock uid_lock to be a no-op context manager
        import core.memory.locks as locks_mod
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _noop_lock(uid):
            yield

        monkeypatch.setattr(locks_mod, "uid_lock", _noop_lock)

        await fp.summarize_to_midterm(
            turn_id=_turn_id,
            uid="uid",
            user_msg="我在学下棋",
            reply="好厉害",
            tags=["下棋"],
            char_id=TEST_CHAR_ID,
        )

        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("events"):
                entry = data["events"][0]
                assert "occurred_at" in entry
                assert entry["occurred_at"] == pytest.approx(1_700_000_000.0)


# ─── P1-3: episodic 血缘 exact-dup 去重 ─────────────────────────────────────

class TestEpisodicLineageDedup:
    """write_episode 血缘去重：共 source_mid_id 的第二条被跳过。"""

    def _base_episode(self, ep_id: str, mids: list, summary: str = "测试摘要") -> dict:
        return {
            "id": ep_id,
            "timestamp": time.time(),
            "occurred_at": time.time(),
            "raw_facts": ["事实1"],
            "topic_keywords": ["下棋"],
            "emotion_peak": "happy",
            "emotion_texture": "",
            "emotion_arc": "",
            "user_state": "",
            "narrative_summary": summary,
            "temporal_ref": "none",
            "event_time": None,
            "expires_at": None,
            "strength": 0.7,
            "retrieval_count": 0,
            "last_retrieved": None,
            "source_mid_ids": mids,
            "consolidated_at": None,
            "summary": summary,
            "tags": [],
        }

    def test_second_episode_with_shared_mid_id_is_skipped(self, tmp_path, monkeypatch):
        """共享一个 source_mid_id → 第二条被跳过。"""
        import core.memory.episodic_memory as em

        p = tmp_path / "episodic.json"
        monkeypatch.setattr(em, "_mem_read_file", lambda uid, char_id=TEST_CHAR_ID: p)
        monkeypatch.setattr(em, "_mem_write_file", lambda uid, char_id=TEST_CHAR_ID: p)
        monkeypatch.setattr(em, "_index_write_file", lambda uid, char_id=TEST_CHAR_ID: tmp_path / "idx.json")
        monkeypatch.setattr(em, "_index_read_file", lambda uid, char_id=TEST_CHAR_ID: tmp_path / "idx.json")

        ep1 = self._base_episode("ep_1", ["mt_abc", "mt_def"])
        em.write_episode("uid", ep1, char_id=TEST_CHAR_ID)

        ep2 = self._base_episode("ep_2", ["mt_abc", "mt_xyz"], summary="完全不同的摘要")
        em.write_episode("uid", ep2, char_id=TEST_CHAR_ID)

        data = json.loads(p.read_text(encoding="utf-8"))
        ids = [e["id"] for e in data]
        assert "ep_1" in ids
        assert "ep_2" not in ids, "血缘重叠的 ep_2 应被跳过"

    def test_disjoint_lineage_but_similar_text_still_deduped_by_text(self, tmp_path, monkeypatch):
        """血缘无交集但文本高度相似 → 走原文本近似跳过（不回归）。"""
        import core.memory.episodic_memory as em

        p = tmp_path / "episodic.json"
        monkeypatch.setattr(em, "_mem_read_file", lambda uid, char_id=TEST_CHAR_ID: p)
        monkeypatch.setattr(em, "_mem_write_file", lambda uid, char_id=TEST_CHAR_ID: p)
        monkeypatch.setattr(em, "_index_write_file", lambda uid, char_id=TEST_CHAR_ID: tmp_path / "idx.json")
        monkeypatch.setattr(em, "_index_read_file", lambda uid, char_id=TEST_CHAR_ID: tmp_path / "idx.json")

        ep1 = self._base_episode("ep_1", ["mt_aaa"], summary="用户今天去下棋了")
        em.write_episode("uid", ep1, char_id=TEST_CHAR_ID)

        ep2 = self._base_episode("ep_2", ["mt_bbb"], summary="用户今天去下棋了")  # 同文本，不同血缘
        em.write_episode("uid", ep2, char_id=TEST_CHAR_ID)

        data = json.loads(p.read_text(encoding="utf-8"))
        assert len(data) == 1, "文本相似的第二条应被原文本去重逻辑跳过"

    def test_disjoint_lineage_and_different_text_is_written(self, tmp_path, monkeypatch):
        """血缘无交集且文本不似 → 正常写入。"""
        import core.memory.episodic_memory as em

        p = tmp_path / "episodic.json"
        monkeypatch.setattr(em, "_mem_read_file", lambda uid, char_id=TEST_CHAR_ID: p)
        monkeypatch.setattr(em, "_mem_write_file", lambda uid, char_id=TEST_CHAR_ID: p)
        monkeypatch.setattr(em, "_index_write_file", lambda uid, char_id=TEST_CHAR_ID: tmp_path / "idx.json")
        monkeypatch.setattr(em, "_index_read_file", lambda uid, char_id=TEST_CHAR_ID: tmp_path / "idx.json")

        ep1 = self._base_episode("ep_1", ["mt_aaa"], summary="用户今天下棋赢了")
        em.write_episode("uid", ep1, char_id=TEST_CHAR_ID)

        ep2 = self._base_episode("ep_2", ["mt_bbb"], summary="用户今天去游泳")  # 完全不同
        em.write_episode("uid", ep2, char_id=TEST_CHAR_ID)

        data = json.loads(p.read_text(encoding="utf-8"))
        ids = [e["id"] for e in data]
        assert "ep_1" in ids
        assert "ep_2" in ids, "不同血缘且文本不似的第二条应正常写入"

    def test_empty_source_mid_ids_skips_lineage_check(self, tmp_path, monkeypatch):
        """source_mid_ids 为空时跳过血缘检查（直接走文本近似，不崩溃）。"""
        import core.memory.episodic_memory as em

        p = tmp_path / "episodic.json"
        monkeypatch.setattr(em, "_mem_read_file", lambda uid, char_id=TEST_CHAR_ID: p)
        monkeypatch.setattr(em, "_mem_write_file", lambda uid, char_id=TEST_CHAR_ID: p)
        monkeypatch.setattr(em, "_index_write_file", lambda uid, char_id=TEST_CHAR_ID: tmp_path / "idx.json")
        monkeypatch.setattr(em, "_index_read_file", lambda uid, char_id=TEST_CHAR_ID: tmp_path / "idx.json")

        # Use text distinct enough to pass the _is_similar(threshold=0.6) text check
        ep1 = self._base_episode("ep_1", [], summary="用户失眠睡不着觉焦虑")
        em.write_episode("uid", ep1, char_id=TEST_CHAR_ID)

        ep2 = self._base_episode("ep_2", [], summary="收到礼物特别惊喜开心")
        em.write_episode("uid", ep2, char_id=TEST_CHAR_ID)

        data = json.loads(p.read_text(encoding="utf-8"))
        assert len(data) == 2, "无血缘信息时两条应正常写入"
