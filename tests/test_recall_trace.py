"""
tests/test_recall_trace.py

Contract tests for:
  - recall_trace.write_trace() path + content
  - episodic_memory.retrieve(return_trace=True)
  - episodic_memory.retrieve_fallback(return_trace=True)
  - event_log.search(return_trace=True)
  - lore_engine.LoreEngine.match(return_trace=True)
"""

import asyncio
import json
import time

import pytest

UID = "recall_trace_test_user"
CHAR_ID = "yexuan"


# ─── helpers ──────────────────────────────────────────────────────────────────

def _episode(ep_id: str, *, keyword: str = "西瓜", strength: float = 0.7) -> dict:
    return {
        "id": ep_id,
        "timestamp": time.time(),
        "raw_facts": [f"用户提到了{keyword}"],
        "topic_keywords": [keyword, "测试"],
        "emotion_peak": "gentle",
        "emotion_texture": "温柔",
        "emotion_arc": "平稳",
        "user_state": "relaxed",
        "narrative_summary": f"用户正在讨论{keyword}，气氛轻松",
        "strength": strength,
        "status": "open",
        "resolved_at": None,
        "resolved_by": None,
        "temporal_ref": "none",
        "event_time": None,
        "expires_at": None,
        "retrieval_count": 0,
        "last_retrieved": None,
        "summary": "",
        "yexuan_feeling": "",
        "tags": [keyword],
    }


# ─── write_trace ──────────────────────────────────────────────────────────────

class TestWriteTrace:
    def test_creates_jsonl_file(self, sandbox, tmp_path):
        from core.recall_trace import write_trace
        trace = {"ts": "2026-06-19T12:00:00", "uid": UID, "char_id": CHAR_ID, "query": "test"}
        write_trace(UID, CHAR_ID, trace)

        from core.memory.scope import MemoryScope
        from core.memory.path_resolver import resolve_path
        from datetime import datetime
        scope = MemoryScope.reality_scope(UID, CHAR_ID)
        trace_dir = resolve_path(scope, "recall_trace")
        date_str = datetime.now().strftime("%Y-%m-%d")
        trace_file = trace_dir / f"{date_str}.jsonl"
        assert trace_file.exists()

    def test_appends_valid_json_line(self, sandbox):
        from core.recall_trace import write_trace
        from datetime import datetime
        trace = {"ts": "2026-06-19T12:00:00", "uid": UID, "char_id": CHAR_ID, "query": "hello"}
        write_trace(UID, CHAR_ID, trace)

        from core.memory.scope import MemoryScope
        from core.memory.path_resolver import resolve_path
        scope = MemoryScope.reality_scope(UID, CHAR_ID)
        trace_dir = resolve_path(scope, "recall_trace")
        date_str = datetime.now().strftime("%Y-%m-%d")
        lines = (trace_dir / f"{date_str}.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["uid"] == UID
        assert parsed["query"] == "hello"

    def test_multiple_writes_append(self, sandbox):
        from core.recall_trace import write_trace
        from datetime import datetime
        for i in range(3):
            write_trace(UID, CHAR_ID, {"idx": i})

        from core.memory.scope import MemoryScope
        from core.memory.path_resolver import resolve_path
        scope = MemoryScope.reality_scope(UID, CHAR_ID)
        trace_dir = resolve_path(scope, "recall_trace")
        date_str = datetime.now().strftime("%Y-%m-%d")
        lines = (trace_dir / f"{date_str}.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        assert json.loads(lines[2])["idx"] == 2

    def test_swallows_exception_on_bad_uid(self, sandbox):
        from core.recall_trace import write_trace
        # Invalid uid that would trigger safe_user_id ValueError — must not raise
        write_trace("../escape", CHAR_ID, {"x": 1})  # no raise


# ─── episodic_memory.retrieve(return_trace=True) ──────────────────────────────

class TestEpisodicRetrieveTrace:
    def test_returns_tuple_when_return_trace_true(self, sandbox, monkeypatch):
        monkeypatch.setattr(
            "core.memory.mood_state.get_current", lambda **kw: "neutral"
        )
        monkeypatch.setattr(
            "core.memory.mood_state.get_intensity", lambda **kw: 0.0
        )
        from core.memory import episodic_memory
        episodic_memory.write_episode(UID, _episode("ep_t1"), char_id=CHAR_ID)

        result = episodic_memory.retrieve(
            UID, topic="", top_k=3, char_id=CHAR_ID,
            allow_strengthen=False, return_trace=True,
        )
        assert isinstance(result, tuple)
        mems, trace = result
        assert isinstance(mems, list)
        assert isinstance(trace, list)

    def test_trace_items_have_required_fields(self, sandbox, monkeypatch):
        monkeypatch.setattr("core.memory.mood_state.get_current", lambda **kw: "neutral")
        monkeypatch.setattr("core.memory.mood_state.get_intensity", lambda **kw: 0.0)
        from core.memory import episodic_memory
        episodic_memory.write_episode(UID, _episode("ep_t2", strength=0.8), char_id=CHAR_ID)

        _, trace = episodic_memory.retrieve(
            UID, topic="西瓜", top_k=3, char_id=CHAR_ID,
            allow_strengthen=False, return_trace=True,
        )
        assert len(trace) >= 1
        item = trace[0]
        for field in ("id", "score", "hop", "kw", "summary", "strength", "emotion_peak", "selected"):
            assert field in item, f"missing field: {field}"

    def test_selected_flag_marks_result_members(self, sandbox, monkeypatch):
        monkeypatch.setattr("core.memory.mood_state.get_current", lambda **kw: "neutral")
        monkeypatch.setattr("core.memory.mood_state.get_intensity", lambda **kw: 0.0)
        from core.memory import episodic_memory
        for i in range(5):
            episodic_memory.write_episode(
                UID, _episode(f"ep_sel{i}", strength=0.5 + i * 0.05), char_id=CHAR_ID
            )

        mems, trace = episodic_memory.retrieve(
            UID, topic="", top_k=2, char_id=CHAR_ID,
            allow_strengthen=False, return_trace=True,
        )
        selected_in_trace = {t["id"] for t in trace if t["selected"]}
        result_ids = {m["id"] for m in mems}
        assert selected_in_trace == result_ids

    def test_returns_list_when_return_trace_false(self, sandbox, monkeypatch):
        monkeypatch.setattr("core.memory.mood_state.get_current", lambda **kw: "neutral")
        monkeypatch.setattr("core.memory.mood_state.get_intensity", lambda **kw: 0.0)
        from core.memory import episodic_memory
        result = episodic_memory.retrieve(
            UID, topic="", top_k=3, char_id=CHAR_ID,
            allow_strengthen=False, return_trace=False,
        )
        assert isinstance(result, list)

    def test_empty_memories_trace_is_empty_list(self, sandbox, monkeypatch):
        monkeypatch.setattr("core.memory.mood_state.get_current", lambda **kw: "neutral")
        monkeypatch.setattr("core.memory.mood_state.get_intensity", lambda **kw: 0.0)
        from core.memory import episodic_memory
        mems, trace = episodic_memory.retrieve(
            "no_memories_uid", topic="something", top_k=3, char_id=CHAR_ID,
            allow_strengthen=False, return_trace=True,
        )
        assert mems == []
        assert trace == []


# ─── episodic_memory.retrieve_fallback(return_trace=True) ─────────────────────

class TestEpisodicFallbackTrace:
    def test_returns_tuple_when_return_trace_true(self, sandbox, monkeypatch):
        monkeypatch.setattr("core.memory.mood_state.get_current", lambda **kw: "neutral")
        monkeypatch.setattr("core.memory.mood_state.get_intensity", lambda **kw: 0.0)
        from core.memory import episodic_memory
        ep = _episode("ep_fb1", strength=0.9)
        ep["timestamp"] = time.time() - 86400  # 1 day ago, within 7-day window
        episodic_memory.write_episode(UID, ep, char_id=CHAR_ID)

        result = episodic_memory.retrieve_fallback(
            UID, recent_history=[], top_k=1, char_id=CHAR_ID, return_trace=True
        )
        assert isinstance(result, tuple)
        mems, trace = result
        assert isinstance(mems, list)
        assert isinstance(trace, list)

    def test_fallback_trace_items_have_hop_field(self, sandbox, monkeypatch):
        monkeypatch.setattr("core.memory.mood_state.get_current", lambda **kw: "neutral")
        monkeypatch.setattr("core.memory.mood_state.get_intensity", lambda **kw: 0.0)
        from core.memory import episodic_memory
        ep = _episode("ep_fb2", strength=0.9)
        ep["timestamp"] = time.time() - 3600
        episodic_memory.write_episode(UID, ep, char_id=CHAR_ID)

        _, trace = episodic_memory.retrieve_fallback(
            UID, recent_history=[], top_k=1, char_id=CHAR_ID, return_trace=True
        )
        if trace:
            assert trace[0]["hop"] == "fallback"
            for field in ("id", "score", "summary", "strength"):
                assert field in trace[0]

    def test_returns_list_when_return_trace_false(self, sandbox, monkeypatch):
        monkeypatch.setattr("core.memory.mood_state.get_current", lambda **kw: "neutral")
        monkeypatch.setattr("core.memory.mood_state.get_intensity", lambda **kw: 0.0)
        from core.memory import episodic_memory
        result = episodic_memory.retrieve_fallback(
            UID, recent_history=[], top_k=1, char_id=CHAR_ID, return_trace=False
        )
        assert isinstance(result, list)


# ─── event_log.search(return_trace=True) ─────────────────────────────────────

class TestEventLogSearchTrace:
    def test_returns_tuple_when_return_trace_true(self, sandbox):
        from core.memory import event_log
        # Write a log entry so search has something to find
        event_log.append(UID, "user", "我今天很累很累", char_id=CHAR_ID)
        event_log.append(UID, "assistant", "好好休息", char_id=CHAR_ID)

        result = asyncio.get_event_loop().run_until_complete(
            event_log.search(UID, "很累", return_trace=True, char_id=CHAR_ID)
        )
        assert isinstance(result, tuple)
        text, trace = result
        assert isinstance(text, str)
        assert isinstance(trace, list)

    def test_trace_items_have_score_and_snippet(self, sandbox):
        from core.memory import event_log
        event_log.append(UID, "user", "我今天非常非常开心", char_id=CHAR_ID)
        event_log.append(UID, "assistant", "真好", char_id=CHAR_ID)

        _, trace = asyncio.get_event_loop().run_until_complete(
            event_log.search(UID, "开心", return_trace=True, char_id=CHAR_ID)
        )
        if trace:
            item = trace[0]
            assert "score" in item
            assert "snippet" in item

    def test_returns_str_when_return_trace_false(self, sandbox):
        from core.memory import event_log
        result = asyncio.get_event_loop().run_until_complete(
            event_log.search(UID, "query", return_trace=False, char_id=CHAR_ID)
        )
        assert isinstance(result, str)

    def test_empty_log_returns_empty_trace(self, sandbox):
        from core.memory import event_log
        text, trace = asyncio.get_event_loop().run_until_complete(
            event_log.search("no_log_uid", "query", return_trace=True, char_id=CHAR_ID)
        )
        assert text == ""
        assert trace == []


# ─── lore_engine.match(return_trace=True) ────────────────────────────────────

class TestLoreEngineMatchTrace:
    def _make_engine(self) -> "LoreEngine":
        from core.lore_engine import LoreEngine
        engine = LoreEngine()
        engine.load_entries([
            {"keywords": ["圣塞西尔", "学院"], "content": "圣塞西尔学院的世界观描述", "enabled": True},
            {"keywords": ["琴宁岛"], "content": "琴宁岛位于南海", "enabled": True},
        ])
        return engine

    def test_returns_tuple_when_return_trace_true(self):
        engine = self._make_engine()
        result = engine.match("我在圣塞西尔学院上课", return_trace=True)
        assert isinstance(result, tuple)
        contents, trace = result
        assert isinstance(contents, list)
        assert isinstance(trace, list)

    def test_trace_contains_hit_keyword(self):
        engine = self._make_engine()
        contents, trace = engine.match("圣塞西尔很美", return_trace=True)
        assert len(contents) >= 1
        assert len(trace) >= 1
        item = trace[0]
        assert "kw" in item
        assert "content_preview" in item
        assert "insertion_order" in item
        assert item["kw"] == "圣塞西尔"

    def test_no_hit_returns_empty_trace(self):
        engine = self._make_engine()
        contents, trace = engine.match("今天天气很好", return_trace=True)
        assert contents == []
        assert trace == []

    def test_returns_list_when_return_trace_false(self):
        engine = self._make_engine()
        result = engine.match("圣塞西尔", return_trace=False)
        assert isinstance(result, list)
        assert isinstance(result[0], str)

    def test_multiple_entries_all_appear_in_trace(self):
        engine = self._make_engine()
        contents, trace = engine.match("圣塞西尔学院在琴宁岛附近", return_trace=True)
        assert len(contents) == 2
        assert len(trace) == 2
        kws = {t["kw"] for t in trace}
        assert "圣塞西尔" in kws or "学院" in kws
        assert "琴宁岛" in kws
