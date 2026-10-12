"""
tests/test_fetch_context_lowinfo_gate.py — P0.5-2 验收

断言覆盖：
- 低信息 content → event_search 未调用（mock 断言 0 次）
- 低信息 content → episodic_fallback 为空
- 低信息 content → diary_context 为空
- 低信息 content → context["suppress_emotional_recall"] == True
- 正常 content → event_search 被调用；episodic_fallback 有机会被调用；
  suppress_emotional_recall == False
"""

from __future__ import annotations
from tests.fixtures.public_assets import TEST_CHAR_ID

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import core.memory.event_log
import core.memory.user_profile
import core.memory.mid_term
import core.memory.short_term
import core.memory.episodic_memory
import core.memory.user_identity
import core.dream.impression_loader
import core.memory.group_context
import core.memory.diary_context
import core.tools.reminder
import core.memory.mood_state
import core.user_relation


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def chars_tree(tmp_path):
    chars = tmp_path / "characters"
    chars.mkdir()
    (chars / f'{TEST_CHAR_ID}.json').write_text(
        json.dumps({"name": "Companion", "description": "test", "world_book": []}),
        encoding="utf-8",
    )
    jb = chars / "reality" / "jailbreaks"
    jb.mkdir(parents=True)
    (jb / "base.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def registry(chars_tree, monkeypatch):
    import core.asset_registry as _reg_mod
    from core.asset_registry import AssetRegistry
    monkeypatch.chdir(chars_tree)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)
    return reg


def _make_pipeline(char_id, registry):
    from core.character_loader import load as _load
    from core.pipeline import Pipeline
    char = _load(char_id)
    lore = MagicMock()
    lore.match.return_value = ([], [])
    return Pipeline(char, lore_engine=lore, active_character_id=char_id)


def _write_active(sandbox, char_id):
    p = sandbox.active_prompt_assets()
    p.write_text(
        json.dumps({"active_character": char_id,
                    "enabled_lorebooks": [],
                    "enabled_jailbreaks": []}),
        encoding="utf-8",
    )


def _apply_base_stubs(monkeypatch, *, event_log_mock=None, fallback_mock=None, diary_load_mock=None):
    import core.memory.event_log as _el
    import core.memory.user_profile as _up
    import core.memory.mid_term as _mt
    import core.memory.short_term as _st
    import core.memory.episodic_memory as _ep
    import core.memory.user_identity as _ui
    import core.dream.impression_loader as _il
    import core.memory.group_context as _gc
    import core.memory.diary_context as _dc
    import core.tools.reminder as _rem
    import core.memory.mood_state as _ms
    import core.user_relation as _ur

    _el_mock = event_log_mock or AsyncMock(return_value=("", []))
    monkeypatch.setattr(_el, "search", _el_mock)
    monkeypatch.setattr(_up, "load", lambda *a, **kw: {})
    monkeypatch.setattr(_mt, "format_for_prompt", lambda *a, **kw: "")
    monkeypatch.setattr(_st, "load_for_prompt", lambda *a, **kw: [])
    monkeypatch.setattr(_ep, "retrieve", lambda *a, **kw: ([], []) if kw.get("return_trace") else [])

    _fb_mock = fallback_mock or (lambda *a, **kw: ([], []) if kw.get("return_trace") else [])
    monkeypatch.setattr(_ep, "retrieve_fallback", _fb_mock)

    monkeypatch.setattr(_ui, "format_for_prompt", AsyncMock(return_value=""))
    monkeypatch.setattr(_il, "load_impression_text", lambda *a, **kw: "")
    monkeypatch.setattr(_gc, "get_recent", lambda *a, **kw: "")

    _dc_mock = diary_load_mock or (lambda *a, **kw: "")
    monkeypatch.setattr(_dc, "load", _dc_mock)
    monkeypatch.setattr(_dc, "load_meta", lambda *a, **kw: {})

    monkeypatch.setattr(_rem, "get_reminders", lambda *a, **kw: [])
    monkeypatch.setattr(_ms, "get_current", lambda *a, **kw: "neutral")
    monkeypatch.setattr(_ms, "get_intensity", lambda *a, **kw: 0.5)
    monkeypatch.setattr(_ms, "update", lambda *a, **kw: None)
    monkeypatch.setattr(_ur, "get_relation", lambda *a, **kw: {"priority": 1})

    return _el_mock


def _run_fetch(pipeline, user_id="u1", content="hello"):
    return asyncio.run(pipeline.fetch_context(user_id=user_id, content=content))


def test_midterm_receives_the_same_selected_history(chars_tree, monkeypatch, sandbox, registry):
    pipeline = _make_pipeline(TEST_CHAR_ID, registry)
    _write_active(sandbox, TEST_CHAR_ID)
    _apply_base_stubs(monkeypatch)
    history = [{"role": "user", "content": "selected original", "_turn_id": "selected-turn"}]
    monkeypatch.setattr(core.memory.short_term, "load_for_prompt", lambda *a, **kw: history)
    received = []
    def project(*args, **kwargs):
        received.append(kwargs["history"])
        return "uncovered summary"
    monkeypatch.setattr(core.memory.mid_term, "format_for_prompt", project)
    context = _run_fetch(pipeline, content="嗯嗯")
    assert received == [history]
    assert received[0] is history
    assert context["mid_term"] == "uncovered summary"


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestLowInfoGateBlocks:
    @pytest.mark.parametrize("low_info_text", ["嗯嗯", "咪", "哈哈哈", "好的", ""])
    def test_event_search_not_called_for_low_info(
        self, low_info_text, chars_tree, monkeypatch, sandbox, registry
    ):
        el_mock = AsyncMock(return_value=("", []))
        pipeline = _make_pipeline(TEST_CHAR_ID, registry)
        _write_active(sandbox, TEST_CHAR_ID)
        _apply_base_stubs(monkeypatch, event_log_mock=el_mock)

        ctx = _run_fetch(pipeline, content=low_info_text)

        el_mock.assert_not_called()
        assert ctx["event_search_result"] == ""

    @pytest.mark.parametrize("low_info_text", ["嗯嗯", "咪", "喵喵喵"])
    def test_episodic_fallback_empty_for_low_info(
        self, low_info_text, chars_tree, monkeypatch, sandbox, registry
    ):
        fb_calls = []

        def _fb_spy(*a, **kw):
            fb_calls.append(True)
            return ([], []) if kw.get("return_trace") else []

        pipeline = _make_pipeline(TEST_CHAR_ID, registry)
        _write_active(sandbox, TEST_CHAR_ID)
        _apply_base_stubs(monkeypatch, fallback_mock=_fb_spy)

        ctx = _run_fetch(pipeline, content=low_info_text)

        assert fb_calls == [], "retrieve_fallback 不应在低信息轮被调用"
        assert ctx["episodic_fallback_result"] == ""

    @pytest.mark.parametrize("low_info_text", ["嗯嗯", "好的", "哦"])
    def test_diary_context_empty_for_low_info(
        self, low_info_text, chars_tree, monkeypatch, sandbox, registry
    ):
        pipeline = _make_pipeline(TEST_CHAR_ID, registry)
        _write_active(sandbox, TEST_CHAR_ID)
        _apply_base_stubs(monkeypatch, diary_load_mock=lambda *a, **kw: "最近的日记内容")

        ctx = _run_fetch(pipeline, content=low_info_text)

        assert ctx["diary_context"] == ""

    @pytest.mark.parametrize("low_info_text", ["嗯嗯", "咪", "好"])
    def test_suppress_emotional_recall_true_for_low_info(
        self, low_info_text, chars_tree, monkeypatch, sandbox, registry
    ):
        pipeline = _make_pipeline(TEST_CHAR_ID, registry)
        _write_active(sandbox, TEST_CHAR_ID)
        _apply_base_stubs(monkeypatch)

        ctx = _run_fetch(pipeline, content=low_info_text)

        assert ctx.get("suppress_emotional_recall") is True


class TestNormalContentPasses:
    @pytest.mark.parametrize("normal_text", ["今天好累啊", "我想你", "好的我去睡了"])
    def test_event_search_called_for_normal_content(
        self, normal_text, chars_tree, monkeypatch, sandbox, registry
    ):
        el_mock = AsyncMock(return_value=("", []))
        pipeline = _make_pipeline(TEST_CHAR_ID, registry)
        _write_active(sandbox, TEST_CHAR_ID)
        _apply_base_stubs(monkeypatch, event_log_mock=el_mock)

        _run_fetch(pipeline, content=normal_text)

        el_mock.assert_called_once()

    @pytest.mark.parametrize("normal_text", ["今天好累啊", "我想你"])
    def test_suppress_emotional_recall_false_for_normal_content(
        self, normal_text, chars_tree, monkeypatch, sandbox, registry
    ):
        pipeline = _make_pipeline(TEST_CHAR_ID, registry)
        _write_active(sandbox, TEST_CHAR_ID)
        _apply_base_stubs(monkeypatch)

        ctx = _run_fetch(pipeline, content=normal_text)

        assert ctx.get("suppress_emotional_recall") is False
