"""
tests/test_n2_fetch_context_side_effects.py

N2-A 审计测试：fetch_context 读路径副作用封口验收

验收标准：
1. Pipeline.fetch_context 本身不再直接写 mood（sleepy 已迁出）
2. Pipeline.fetch_context 调 episodic retrieve 时 allow_strengthen=False，不写回 strength
3. 工具命中仍会进入 thinking，但入口是 mark_tool_thinking_mood helper，
   main.py 不再直接调用 mood_state.update
4. 深夜 sleepy 语义仍存在，现在由 post_process 内的 maybe_mark_sleepy_from_time 触发
5. maybe_mark_sleepy_from_time 和 mark_tool_thinking_mood 是 mood_helpers 模块的唯一出口
"""

import asyncio
import inspect
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.memory.event_log          # noqa: F401
import core.memory.user_profile       # noqa: F401
import core.memory.mid_term           # noqa: F401
import core.memory.short_term         # noqa: F401
import core.memory.episodic_memory    # noqa: F401
import core.memory.user_identity      # noqa: F401
import core.dream.impression_loader   # noqa: F401
import core.memory.group_context      # noqa: F401
import core.memory.diary_context      # noqa: F401
import core.tools.reminder            # noqa: F401
import core.memory.mood_state         # noqa: F401
import core.user_relation             # noqa: F401


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def chars_tree(tmp_path):
    chars = tmp_path / "characters"
    chars.mkdir()
    (chars / "yexuan.json").write_text(
        json.dumps({"name": "叶瑄", "description": "test", "world_book": []}),
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
    lore.match.return_value = []
    return Pipeline(char, lore_engine=lore, active_character_id=char_id)


def _write_active(sandbox, char_id):
    p = sandbox.active_prompt_assets()
    p.write_text(
        json.dumps({"active_character": char_id,
                    "enabled_lorebooks": [],
                    "enabled_jailbreaks": []}),
        encoding="utf-8",
    )


def _apply_base_stubs(monkeypatch):
    import core.memory.event_log as _el
    import core.memory.user_profile as _up
    import core.memory.mid_term as _mt
    import core.memory.short_term as _st
    import core.memory.episodic_memory as _ep
    import core.memory.user_identity as _ui
    import core.dream.impression_loader as _il
    import core.memory.group_context as _gc
    import core.memory.diary_context as _dc

    monkeypatch.setattr(_el, "search", AsyncMock(return_value=""))
    monkeypatch.setattr(_up, "load", lambda *a, **kw: {})
    monkeypatch.setattr(_mt, "format_for_prompt", lambda *a, **kw: "")
    monkeypatch.setattr(_st, "load_for_prompt", lambda *a, **kw: [])
    monkeypatch.setattr(_ep, "retrieve", lambda *a, **kw: [])
    monkeypatch.setattr(_ep, "retrieve_fallback", lambda *a, **kw: [])
    monkeypatch.setattr(_ui, "format_for_prompt", AsyncMock(return_value=""))
    monkeypatch.setattr(_il, "load_impression_text", lambda *a, **kw: "")
    monkeypatch.setattr(_gc, "get_recent", lambda *a, **kw: "")
    try:
        monkeypatch.setattr(_dc, "load", lambda *a, **kw: "")
    except Exception:
        pass
    import core.tools.reminder as _rem
    try:
        monkeypatch.setattr(_rem, "get_reminders", lambda *a, **kw: [])
    except Exception:
        pass
    import core.memory.mood_state as _ms
    monkeypatch.setattr(_ms, "get_current", lambda *a, **kw: "neutral")
    monkeypatch.setattr(_ms, "update", lambda *a, **kw: None)
    import core.user_relation as _ur
    monkeypatch.setattr(_ur, "get_relation", lambda *a, **kw: {"priority": 1})


def _run_fetch(pipeline, user_id="u1", content="hello"):
    return asyncio.run(pipeline.fetch_context(user_id=user_id, content=content))


# ══════════════════════════════════════════════════════════════════════════════
# 1. fetch_context 不直接写 mood_state
# ══════════════════════════════════════════════════════════════════════════════

def test_fetch_context_does_not_call_mood_state_update_directly(
    chars_tree, monkeypatch, sandbox, registry
):
    """N2-A T1: fetch_context 全程不调用 mood_state.update。"""
    import core.memory.mood_state as _ms

    pipeline = _make_pipeline("yexuan", registry)
    _write_active(sandbox, "yexuan")
    _apply_base_stubs(monkeypatch)

    mood_update_calls = []

    def _spy_update(new_emotion, new_intensity=None, source="detect", *, char_id="yexuan"):
        mood_update_calls.append((new_emotion, source))
        return {}

    monkeypatch.setattr(_ms, "update", _spy_update)
    _run_fetch(pipeline)

    assert mood_update_calls == [], (
        f"fetch_context 不应直接写 mood_state.update，实际调用: {mood_update_calls}"
    )


def test_fetch_context_source_code_has_no_mood_update_call():
    """N2-A T1 (静态): fetch_context 可执行代码行中无 mood_state.update 调用。"""
    from core.pipeline import Pipeline
    src = inspect.getsource(Pipeline.fetch_context)
    code_lines = [l for l in src.splitlines() if not l.strip().startswith("#")]
    code_body = "\n".join(code_lines)
    assert "_mood_update" not in code_body, (
        "fetch_context 代码中残留 _mood_update 调用"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 2. fetch_context 调 retrieve 时 allow_strengthen=False
# ══════════════════════════════════════════════════════════════════════════════

def test_fetch_context_calls_retrieve_with_allow_strengthen_false(
    chars_tree, monkeypatch, sandbox, registry
):
    """N2-A T2: fetch_context 调 episodic.retrieve 必须传 allow_strengthen=False。"""
    import core.memory.episodic_memory as _ep

    pipeline = _make_pipeline("yexuan", registry)
    _write_active(sandbox, "yexuan")
    _apply_base_stubs(monkeypatch)

    retrieve_kwargs = []

    def _spy_retrieve(*args, **kwargs):
        retrieve_kwargs.append(kwargs)
        return []

    monkeypatch.setattr(_ep, "retrieve", _spy_retrieve)
    _run_fetch(pipeline)

    assert len(retrieve_kwargs) >= 1, "episodic.retrieve 应被调用至少一次"
    for kw in retrieve_kwargs:
        assert kw.get("allow_strengthen") is False, (
            f"fetch_context 调 retrieve 时必须传 allow_strengthen=False，实际: {kw}"
        )


def test_retrieve_with_allow_strengthen_false_does_not_save():
    """N2-A T2a: retrieve(allow_strengthen=False) 命中记忆后不写回文件。"""
    import core.memory.episodic_memory as _ep

    memories = [
        {
            "id": "ep_1",
            "timestamp": 1000.0,
            "raw_facts": ["用户说了测试"],
            "topic_keywords": ["测试", "hello"],
            "emotion_peak": "happy",
            "emotion_texture": "",
            "emotion_arc": "",
            "user_state": "",
            "narrative_summary": "测试记忆",
            "strength": 0.7,
            "retrieval_count": 0,
            "last_retrieved": None,
            "summary": "",
            "tags": [],
        }
    ]

    save_calls = []
    with (
        patch.object(_ep, "_load_memories", return_value=memories),
        patch.object(_ep, "_load_index", return_value={}),
        patch.object(_ep, "_save_memories",
                     side_effect=lambda *a, **kw: save_calls.append(a)),
    ):
        result = _ep.retrieve(
            user_id="u_test",
            topic="测试 hello",
            top_k=3,
            char_id="yexuan",
            allow_strengthen=False,
        )

    assert len(result) >= 1, "应召回至少一条记忆"
    assert save_calls == [], (
        f"allow_strengthen=False 时不应调用 _save_memories，但调用了 {len(save_calls)} 次"
    )


def test_retrieve_with_allow_strengthen_true_does_save():
    """N2-A T2b (向后兼容): retrieve(allow_strengthen=True) 命中时仍写回 strength。"""
    import core.memory.episodic_memory as _ep

    memories = [
        {
            "id": "ep_1",
            "timestamp": 1000.0,
            "raw_facts": ["用户说了测试"],
            "topic_keywords": ["测试", "hello"],
            "emotion_peak": "neutral",
            "emotion_texture": "",
            "emotion_arc": "",
            "user_state": "",
            "narrative_summary": "测试记忆",
            "strength": 0.5,
            "retrieval_count": 0,
            "last_retrieved": None,
            "summary": "",
            "tags": [],
        }
    ]

    save_calls = []
    with (
        patch.object(_ep, "_load_memories", return_value=memories),
        patch.object(_ep, "_load_index", return_value={}),
        patch.object(_ep, "_save_memories",
                     side_effect=lambda *a, **kw: save_calls.append(a)),
        patch("core.memory.mood_state.nudge_from_memory"),
    ):
        result = _ep.retrieve(
            user_id="u_test",
            topic="测试 hello",
            top_k=3,
            char_id="yexuan",
            allow_strengthen=True,
        )

    assert len(result) >= 1, "应召回至少一条记忆"
    assert len(save_calls) >= 1, (
        "allow_strengthen=True 时命中后应调用 _save_memories（向后兼容）"
    )


def test_retrieve_with_allow_strengthen_false_does_not_call_nudge():
    """N2-A T2c: allow_strengthen=False 时 nudge_from_memory 也不被调用。"""
    import core.memory.episodic_memory as _ep
    from core.memory import mood_state as _ms

    memories = [
        {
            "id": "ep_1",
            "timestamp": 1000.0,
            "raw_facts": ["强情绪记忆"],
            "topic_keywords": ["情绪"],
            "emotion_peak": "sad",
            "emotion_texture": "很难过",
            "emotion_arc": "",
            "user_state": "",
            "narrative_summary": "强情绪",
            "strength": 0.9,
            "retrieval_count": 0,
            "last_retrieved": None,
            "summary": "",
            "tags": [],
        }
    ]

    nudge_calls = []
    with (
        patch.object(_ep, "_load_memories", return_value=memories),
        patch.object(_ep, "_load_index", return_value={}),
        patch.object(_ep, "_save_memories"),
        patch.object(_ms, "nudge_from_memory",
                     side_effect=lambda *a, **kw: nudge_calls.append(a)),
    ):
        _ep.retrieve(
            user_id="u_test",
            topic="情绪",
            top_k=3,
            char_id="yexuan",
            allow_strengthen=False,
        )

    assert nudge_calls == [], (
        f"allow_strengthen=False 时不应调用 nudge_from_memory，实际 {len(nudge_calls)} 次"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 3. thinking mood 入口是 mark_tool_thinking_mood
# ══════════════════════════════════════════════════════════════════════════════

def test_main_py_does_not_import_mood_state_update_directly():
    """N2-A T3 (静态): main.py 不再裸调用 mood_state.update / _update_mood_probe。"""
    import main as _main
    src = inspect.getsource(_main.handle_message)
    assert "_update_mood_probe" not in src, (
        "main.py handle_message 中仍有 _update_mood_probe，未完成迁出"
    )
    assert "mark_tool_thinking_mood" in src or "mark_thinking" in src, (
        "main.py handle_message 中未找到 mark_tool_thinking_mood 调用"
    )


def test_mark_tool_thinking_mood_calls_mood_update():
    """N2-A T3a: mark_tool_thinking_mood 调 mood_state.update('thinking', ...)。"""
    from core import mood_helpers
    import core.memory.mood_state as _ms

    calls = []
    with patch.object(_ms, "update",
                      side_effect=lambda *a, **kw: calls.append((a, kw))):
        mood_helpers.mark_tool_thinking_mood(uid="u1", char_id="yexuan")

    assert len(calls) == 1
    assert calls[0][0][0] == "thinking", f"情绪应为 thinking，实际 {calls[0][0][0]!r}"


def test_mark_tool_thinking_mood_skips_when_envelope_blocks():
    """N2-A T3b: envelope.can_affect_mood=False 时不写 mood。"""
    from core import mood_helpers
    import core.memory.mood_state as _ms
    from core.write_envelope import WriteEnvelope

    env = WriteEnvelope(can_affect_mood=False)
    calls = []
    with patch.object(_ms, "update",
                      side_effect=lambda *a, **kw: calls.append((a, kw))):
        mood_helpers.mark_tool_thinking_mood(uid="u1", char_id="yexuan", envelope=env)

    assert calls == []


# ══════════════════════════════════════════════════════════════════════════════
# 4. sleepy 语义仍存在，由 post_process 内 maybe_mark_sleepy_from_time 触发
# ══════════════════════════════════════════════════════════════════════════════

def test_maybe_mark_sleepy_writes_when_nighttime():
    """N2-A T4a: 深夜 neutral 时写入 sleepy。"""
    from core import mood_helpers
    import core.memory.mood_state as _ms

    calls = []
    with (
        patch("core.mood_helpers.datetime") as _dt,
        patch.object(_ms, "get_current", return_value="neutral"),
        patch.object(_ms, "update",
                     side_effect=lambda *a, **kw: calls.append((a, kw))),
    ):
        _dt.now.return_value.hour = 23
        mood_helpers.maybe_mark_sleepy_from_time(uid="u1", char_id="yexuan")

    assert len(calls) == 1 and calls[0][0][0] == "sleepy"


def test_maybe_mark_sleepy_skips_when_daytime():
    """N2-A T4b: 白天不写 sleepy。"""
    from core import mood_helpers
    import core.memory.mood_state as _ms

    calls = []
    with (
        patch("core.mood_helpers.datetime") as _dt,
        patch.object(_ms, "update",
                     side_effect=lambda *a, **kw: calls.append(a)),
    ):
        _dt.now.return_value.hour = 14
        mood_helpers.maybe_mark_sleepy_from_time(uid="u1", char_id="yexuan")

    assert calls == []


def test_maybe_mark_sleepy_skips_when_yandere():
    """N2-A T4c: 深夜但 mood=yandere 时不覆盖。"""
    from core import mood_helpers
    import core.memory.mood_state as _ms

    calls = []
    with (
        patch("core.mood_helpers.datetime") as _dt,
        patch.object(_ms, "get_current", return_value="yandere"),
        patch.object(_ms, "update",
                     side_effect=lambda *a, **kw: calls.append(a)),
    ):
        _dt.now.return_value.hour = 2
        mood_helpers.maybe_mark_sleepy_from_time(uid="u1", char_id="yexuan")

    assert calls == []


def test_maybe_mark_sleepy_skips_when_envelope_blocks():
    """N2-A T4d: envelope.can_affect_mood=False 时不写。"""
    from core import mood_helpers
    import core.memory.mood_state as _ms
    from core.write_envelope import WriteEnvelope

    env = WriteEnvelope(can_affect_mood=False)
    calls = []
    with (
        patch("core.mood_helpers.datetime") as _dt,
        patch.object(_ms, "get_current", return_value="neutral"),
        patch.object(_ms, "update",
                     side_effect=lambda *a, **kw: calls.append(a)),
    ):
        _dt.now.return_value.hour = 23
        mood_helpers.maybe_mark_sleepy_from_time(uid="u1", char_id="yexuan",
                                                  envelope=env)

    assert calls == []


def test_fetch_context_source_has_no_sleepy_write():
    """
    N2-A T4e (静态): fetch_context 可执行代码行中无 sleepy 写入或 maybe_mark_sleepy 调用。
    注释里引用函数名是允许的；检查的是可执行行。
    """
    from core.pipeline import Pipeline
    src = inspect.getsource(Pipeline.fetch_context)
    code_lines = [l for l in src.splitlines() if not l.strip().startswith("#")]
    code_body = "\n".join(code_lines)

    assert '"sleepy"' not in code_body, (
        "fetch_context 代码行中仍含有 sleepy mood 写入"
    )
    assert "maybe_mark_sleepy" not in code_body, (
        "fetch_context 可执行代码中不应调用 maybe_mark_sleepy（已迁到 post_process）"
    )


def test_post_process_source_calls_maybe_mark_sleepy():
    """N2-A T4f (静态): post_process 中包含 maybe_mark_sleepy_from_time 调用。"""
    from core.pipeline import Pipeline
    src = inspect.getsource(Pipeline.post_process)
    assert "maybe_mark_sleepy" in src


# ══════════════════════════════════════════════════════════════════════════════
# 5. mood_helpers 模块接口完整性
# ══════════════════════════════════════════════════════════════════════════════

def test_mood_helpers_module_has_expected_functions():
    """N2-A T5: mood_helpers 导出两个显式 helper。"""
    from core import mood_helpers
    assert hasattr(mood_helpers, "maybe_mark_sleepy_from_time")
    assert hasattr(mood_helpers, "mark_tool_thinking_mood")
    assert callable(mood_helpers.maybe_mark_sleepy_from_time)
    assert callable(mood_helpers.mark_tool_thinking_mood)
