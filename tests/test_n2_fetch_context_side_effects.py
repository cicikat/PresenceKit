from tests.fixtures.public_assets import TEST_CHAR_ID
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

    monkeypatch.setattr(_el, "search", AsyncMock(return_value=("", [])))
    monkeypatch.setattr(_up, "load", lambda *a, **kw: {})
    monkeypatch.setattr(_mt, "format_for_prompt", lambda *a, **kw: "")
    monkeypatch.setattr(_st, "load_for_prompt", lambda *a, **kw: [])
    monkeypatch.setattr(_ep, "retrieve", lambda *a, **kw: ([], []) if kw.get("return_trace") else [])
    monkeypatch.setattr(_ep, "retrieve_fallback", lambda *a, **kw: ([], []) if kw.get("return_trace") else [])
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

    pipeline = _make_pipeline(TEST_CHAR_ID, registry)
    _write_active(sandbox, TEST_CHAR_ID)
    _apply_base_stubs(monkeypatch)

    mood_update_calls = []

    def _spy_update(new_emotion, new_intensity=None, source="detect", *, char_id=TEST_CHAR_ID):
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

    pipeline = _make_pipeline(TEST_CHAR_ID, registry)
    _write_active(sandbox, TEST_CHAR_ID)
    _apply_base_stubs(monkeypatch)

    retrieve_kwargs = []

    def _spy_retrieve(*args, **kwargs):
        retrieve_kwargs.append(kwargs)
        return ([], []) if kwargs.get("return_trace") else []

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
            char_id=TEST_CHAR_ID,
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
        patch("core.memory.mood_state.update"),
    ):
        result = _ep.retrieve(
            user_id="u_test",
            topic="测试 hello",
            top_k=3,
            char_id=TEST_CHAR_ID,
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
        patch.object(_ms, "update",
                     side_effect=lambda *a, **kw: nudge_calls.append(a)),
    ):
        _ep.retrieve(
            user_id="u_test",
            topic="情绪",
            top_k=3,
            char_id=TEST_CHAR_ID,
            allow_strengthen=False,
        )

    assert nudge_calls == [], (
        f"allow_strengthen=False 时不应调用 nudge_from_memory，实际 {len(nudge_calls)} 次"
    )


# ══════════════════════════════════════════════════════════════════════════════
# 3. thinking mood 入口是 mark_tool_thinking_mood
# ══════════════════════════════════════════════════════════════════════════════







# ══════════════════════════════════════════════════════════════════════════════
# 4. sleepy 语义仍存在，由 post_process 内 maybe_mark_sleepy_from_time 触发
# ══════════════════════════════════════════════════════════════════════════════









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




# ══════════════════════════════════════════════════════════════════════════════
# 5. mood_helpers 模块接口完整性
# ══════════════════════════════════════════════════════════════════════════════
