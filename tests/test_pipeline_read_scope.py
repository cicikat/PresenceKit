"""
tests/test_pipeline_read_scope.py

P0-T01: pipeline fetch_context() 读路径透传 char_id 验收测试

Covers:
1.  event_log.search        receives char_id=active_character_id
2.  user_profile.load       receives char_id=active_character_id
3.  mid_term.format_for_prompt  receives char_id=active_character_id
4.  short_term.load_for_prompt  receives char_id=active_character_id
5.  episodic_memory.retrieve    receives char_id=active_character_id
6.  user_identity.format_for_prompt  receives char_id=active_character_id
7.  impression_loader.load_impression_text  receives char_id=active_character_id
8.  Character switch: yexuan → character_b, fetch_context uses new id
9.  Invalid active_character: fetch_context raises, no reader called
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.asset_registry as _reg_mod
from core.asset_registry import AssetRegistry

# Import all reader modules at module level so their module-level init code
# (e.g. user_profile._CHAR = _char_name()) runs NOW while cwd == project root,
# not lazily inside tests after monkeypatch.chdir() has changed the directory.
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
    """Minimal characters/ tree with yexuan + character_b."""
    chars = tmp_path / "characters"
    chars.mkdir()

    (chars / "yexuan.json").write_text(
        json.dumps({"name": "Companion", "description": "test", "world_book": []}),
        encoding="utf-8",
    )
    (chars / "character_b.json").write_text(
        json.dumps({"name": "DemoUser", "description": "character_b test", "world_book": []}),
        encoding="utf-8",
    )

    jb = chars / "reality" / "jailbreaks"
    jb.mkdir(parents=True)
    (jb / "base.json").write_text(json.dumps({"entries": []}), encoding="utf-8")

    return tmp_path


@pytest.fixture
def registry(chars_tree, monkeypatch):
    monkeypatch.chdir(chars_tree)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)
    return reg


def _make_pipeline(char_id: str, registry):
    from core.character_loader import load as _load
    from core.pipeline import Pipeline
    char = _load(char_id)
    lore = MagicMock()
    lore.match.return_value = ([], [])
    return Pipeline(char, lore_engine=lore, active_character_id=char_id)


def _write_active(sandbox, char_id: str):
    p = sandbox.active_prompt_assets()
    p.write_text(
        json.dumps({"active_character": char_id, "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )


def _run_fetch(pipeline, user_id="u1", content="hello"):
    return asyncio.run(pipeline.fetch_context(user_id=user_id, content=content))


# ── Shared stub layer ─────────────────────────────────────────────────────────

def _apply_base_stubs(monkeypatch):
    """
    Monkeypatch all supporting functions that would need real files/LLM.
    Returns nothing; callers layer their own spy on top.
    """
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

    # diary_context & reminders
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


# ── Helper: capture char_id kwarg ────────────────────────────────────────────

def _capture_char_id_sync(monkeypatch, module, func_name):
    """Monkeypatch module.func_name (sync) to capture char_id kwarg; return capture list."""
    captured: list[str] = []

    def _spy(*args, **kwargs):
        captured.append(kwargs.get("char_id", "__NOT_PASSED__"))
        return [] if "retrieve" in func_name else ({} if "load" in func_name else "")

    monkeypatch.setattr(module, func_name, _spy)
    return captured


def _capture_char_id_async(monkeypatch, module, func_name):
    """Monkeypatch module.func_name (async) to capture char_id kwarg; return capture list."""
    captured: list[str] = []

    async def _spy(*args, **kwargs):
        captured.append(kwargs.get("char_id", "__NOT_PASSED__"))
        return ""

    monkeypatch.setattr(module, func_name, _spy)
    return captured


# ── 1. event_log.search receives char_id="character_b" ───────────────────────────

def test_fetch_context_passes_char_id_to_event_log_search(
    chars_tree, monkeypatch, sandbox, registry
):
    import core.memory.event_log as _el

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")
    _apply_base_stubs(monkeypatch)

    captured: list[str] = []

    async def _spy_search(user_id, query, llm_client=None, *, char_id="yexuan", return_trace=False, query_vec=None, since_ts=None, until_ts=None):
        captured.append(char_id)
        return ("", []) if return_trace else ""

    monkeypatch.setattr(_el, "search", _spy_search)

    _run_fetch(pipeline)

    assert len(captured) == 1, "event_log.search should be called once"
    assert captured[0] == "character_b", (
        f"event_log.search must receive char_id='character_b', got {captured[0]!r}"
    )


# ── 2. user_profile.load receives char_id="character_b" ──────────────────────────

def test_fetch_context_passes_char_id_to_user_profile_load(
    chars_tree, monkeypatch, sandbox, registry
):
    import core.memory.user_profile as _up

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")
    _apply_base_stubs(monkeypatch)

    captured: list[str] = []

    def _spy_load(user_id, *, char_id="yexuan"):
        captured.append(char_id)
        return {}

    monkeypatch.setattr(_up, "load", _spy_load)

    _run_fetch(pipeline)

    assert len(captured) >= 1, "user_profile.load should be called"
    assert captured[0] == "character_b", (
        f"user_profile.load must receive char_id='character_b', got {captured[0]!r}"
    )


# ── 3. mid_term.format_for_prompt receives char_id="character_b" ─────────────────

def test_fetch_context_passes_char_id_to_mid_term_format(
    chars_tree, monkeypatch, sandbox, registry
):
    import core.memory.mid_term as _mt

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")
    _apply_base_stubs(monkeypatch)

    captured: list[str] = []

    def _spy_format(uid, *, char_id="yexuan"):
        captured.append(char_id)
        return ""

    monkeypatch.setattr(_mt, "format_for_prompt", _spy_format)

    _run_fetch(pipeline)

    assert len(captured) >= 1, "mid_term.format_for_prompt should be called"
    assert captured[0] == "character_b", (
        f"mid_term.format_for_prompt must receive char_id='character_b', got {captured[0]!r}"
    )


# ── 4. short_term.load_for_prompt receives char_id="character_b" ─────────────────

def test_fetch_context_passes_char_id_to_short_term_load_for_prompt(
    chars_tree, monkeypatch, sandbox, registry
):
    import core.memory.short_term as _st

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")
    _apply_base_stubs(monkeypatch)

    captured: list[str] = []

    def _spy_load(user_id, *, budget_rounds=None, near_k=5, char_id="yexuan"):
        captured.append(char_id)
        return []

    monkeypatch.setattr(_st, "load_for_prompt", _spy_load)

    _run_fetch(pipeline)

    assert len(captured) >= 1, "short_term.load_for_prompt should be called"
    assert captured[0] == "character_b", (
        f"short_term.load_for_prompt must receive char_id='character_b', got {captured[0]!r}"
    )


# ── 5. episodic_memory.retrieve receives char_id="character_b" ───────────────────

def test_fetch_context_passes_char_id_to_episodic_retrieve(
    chars_tree, monkeypatch, sandbox, registry
):
    import core.memory.episodic_memory as _ep

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")
    _apply_base_stubs(monkeypatch)

    captured: list[str] = []

    def _spy_retrieve(user_id, topic="", top_k=3, *, char_id="yexuan", char_name="", allow_strengthen=True, return_trace=False, query_vec=None, sem_hits=None, since_ts=None, until_ts=None):
        captured.append(char_id)
        return ([], []) if return_trace else []

    monkeypatch.setattr(_ep, "retrieve", _spy_retrieve)

    _run_fetch(pipeline)

    assert len(captured) >= 1, "episodic_memory.retrieve should be called"
    assert captured[0] == "character_b", (
        f"episodic_memory.retrieve must receive char_id='character_b', got {captured[0]!r}"
    )


# ── 6. user_identity.format_for_prompt receives char_id="character_b" ────────────

def test_fetch_context_passes_char_id_to_user_identity_format(
    chars_tree, monkeypatch, sandbox, registry
):
    import core.memory.user_identity as _ui

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")
    _apply_base_stubs(monkeypatch)

    captured: list[str] = []

    async def _spy_format(user_id, min_confidence=0.5, *, char_id="yexuan"):
        captured.append(char_id)
        return ""

    monkeypatch.setattr(_ui, "format_for_prompt", _spy_format)

    _run_fetch(pipeline)

    assert len(captured) >= 1, "user_identity.format_for_prompt should be called"
    assert captured[0] == "character_b", (
        f"user_identity.format_for_prompt must receive char_id='character_b', got {captured[0]!r}"
    )


# ── 7. impression_loader.load_impression_text receives char_id="character_b" ──────

def test_fetch_context_passes_char_id_to_impression_loader(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    load_impression_text(uid, char_id=...) must receive active char_id.
    Note: impression_store._impressions_file path wiring is T-05 territory;
    this test only asserts that the T-01 caller passes char_id down correctly.
    """
    import core.dream.impression_loader as _il

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")
    _apply_base_stubs(monkeypatch)

    captured: list[str] = []

    def _spy_load_imp(uid, *, char_id="yexuan", **_kwargs):
        captured.append(char_id)
        return ""

    monkeypatch.setattr(_il, "load_impression_text", _spy_load_imp)

    _run_fetch(pipeline)

    assert len(captured) >= 1, "load_impression_text should be called"
    assert captured[0] == "character_b", (
        f"load_impression_text must receive char_id='character_b', got {captured[0]!r}"
    )


# ── 8. Character switch: yexuan → character_b ────────────────────────────────────

def test_fetch_context_uses_new_char_id_after_switch(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    First run with yexuan, then update active_prompt_assets to character_b.
    fetch_context must pass the new char_id after the switch.
    """
    import core.memory.short_term as _st

    pipeline = _make_pipeline("yexuan", registry)
    _write_active(sandbox, "yexuan")
    _apply_base_stubs(monkeypatch)

    captured: list[str] = []

    def _spy_load(user_id, *, budget_rounds=None, near_k=5, char_id="yexuan"):
        captured.append(char_id)
        return []

    monkeypatch.setattr(_st, "load_for_prompt", _spy_load)

    # First call: yexuan
    _run_fetch(pipeline)
    assert captured[-1] == "yexuan", f"First call must use yexuan, got {captured[-1]!r}"

    # Switch active to character_b
    _write_active(sandbox, "character_b")

    # Second call: must now use character_b
    _run_fetch(pipeline)
    assert captured[-1] == "character_b", (
        f"After switch, fetch_context must use character_b, got {captured[-1]!r}"
    )


# ── 9. Invalid active_character: raises, no reader called ────────────────────

def test_fetch_context_invalid_active_does_not_call_readers(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    When active_character is unknown, fetch_context must raise and never
    invoke any memory reader.
    """
    import core.memory.short_term as _st

    pipeline = _make_pipeline("yexuan", registry)

    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "missing_id", "enabled_lorebooks": [],
                    "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    reader_called = []

    def _spy_load(*args, **kwargs):
        reader_called.append(True)
        return []

    monkeypatch.setattr(_st, "load_for_prompt", _spy_load)

    with pytest.raises((ValueError, RuntimeError)):
        _run_fetch(pipeline)

    assert reader_called == [], (
        "short_term.load_for_prompt must NOT be called when active_character is invalid"
    )


# ── 10. CC 任务 19 · C: recall_policy="none" skips episodic/event_search/web_recall ──

def test_recall_policy_none_skips_episodic_event_search_and_web_recall(
    chars_tree, monkeypatch, sandbox, registry
):
    """主动触发 recall_policy="none" 时，episodic/event_search/web_recall 三个检索层
    必须完全不被调用（RC6：主动触发不该被宽泛锚点词捞出无关旧记忆）。
    identity/profile/mid_term 等状态层不受影响，正常调用。
    """
    import core.memory.episodic_memory as _ep
    import core.memory.event_log as _el
    import core.memory.user_identity as _ui

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")
    _apply_base_stubs(monkeypatch)

    retrieve_calls: list = []
    fallback_calls: list = []
    search_calls: list = []
    identity_calls: list = []

    def _spy_retrieve(*args, **kwargs):
        retrieve_calls.append(1)
        return ([], []) if kwargs.get("return_trace") else []

    def _spy_fallback(*args, **kwargs):
        fallback_calls.append(1)
        return ([], []) if kwargs.get("return_trace") else []

    async def _spy_search(*args, **kwargs):
        search_calls.append(1)
        return ("", []) if kwargs.get("return_trace") else ""

    async def _spy_identity(*args, **kwargs):
        identity_calls.append(1)
        return ""

    monkeypatch.setattr(_ep, "retrieve", _spy_retrieve)
    monkeypatch.setattr(_ep, "retrieve_fallback", _spy_fallback)
    monkeypatch.setattr(_el, "search", _spy_search)
    monkeypatch.setattr(_ui, "format_for_prompt", _spy_identity)

    context = asyncio.run(
        pipeline.fetch_context(user_id="u1", content="今天", recall_policy="none")
    )

    assert retrieve_calls == [], "recall_policy=none 时 episodic_memory.retrieve 不应被调用"
    assert fallback_calls == [], "recall_policy=none 时 episodic_memory.retrieve_fallback 不应被调用"
    assert search_calls == [], "recall_policy=none 时 event_log.search 不应被调用"
    assert context["episodic_result"] == ""
    assert context["event_search_result"] == ""
    assert context["web_recall_result"] == ""
    # 状态层不受影响：user_identity 正常调用
    assert identity_calls == [1], "recall_policy=none 不应影响 user_identity 状态层"


def test_recall_policy_seed_default_still_calls_episodic_retrieve(
    chars_tree, monkeypatch, sandbox, registry
):
    """默认 recall_policy="seed" 时行为不变：episodic 主检索照常调用（兼容现状）。"""
    import core.memory.episodic_memory as _ep

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")
    _apply_base_stubs(monkeypatch)

    retrieve_calls: list = []

    def _spy_retrieve(*args, **kwargs):
        retrieve_calls.append(1)
        return ([], []) if kwargs.get("return_trace") else []

    monkeypatch.setattr(_ep, "retrieve", _spy_retrieve)

    asyncio.run(pipeline.fetch_context(user_id="u1", content="今天心情不错"))

    assert retrieve_calls == [1], "默认 recall_policy 不应跳过 episodic_memory.retrieve"
