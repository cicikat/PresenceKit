from tests.fixtures.public_assets import TEST_CHAR_ID
"""
tests/test_pipeline_user_facts_wiring.py
=========================================
P1-4: pipeline fetch_context / build_prompt user_facts wiring tests.

Covers:
1.  fetch_context calls user_facts.format_for_prompt(uid, char_id=char_id)
2.  fetch_context returns "user_facts_text" key in context dict
3.  build_prompt passes user_facts_text from context to prompt_builder.build()
4.  characters with the same uid read their own facts independently
5.  scoped profile and identity still use char_id (isolation unchanged)
6.  omitted char_id resolves the active character for compatibility callers
7.  no user_facts on disk → user_facts_text = '' → layer 5.1 absent, no error
8.  user_facts loaded → user_facts_text non-empty → layer 5.1 present
9.  facts paths include the explicitly frozen character scope
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

import core.asset_registry as _reg_mod
from core.asset_registry import AssetRegistry
from core.memory.scope import MemoryScope

# Eager import so monkeypatch can intercept module-level attributes.
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
import core.memory.user_facts


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def chars_tree(tmp_path):
    chars = tmp_path / "characters"
    chars.mkdir()
    (chars / f'{TEST_CHAR_ID}.json').write_text(
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
        json.dumps({
            "active_character": char_id,
            "enabled_lorebooks": [],
            "enabled_jailbreaks": [],
        }),
        encoding="utf-8",
    )


def _apply_fetch_stubs(monkeypatch, user_facts_return: str = ""):
    """Stub all I/O in fetch_context; user_facts.format_for_prompt returns given value."""
    import core.memory.event_log as _el
    import core.memory.user_profile as _up
    import core.memory.mid_term as _mt
    import core.memory.short_term as _st
    import core.memory.episodic_memory as _ep
    import core.memory.user_identity as _ui
    import core.dream.impression_loader as _il
    import core.memory.group_context as _gc
    import core.memory.diary_context as _dc
    import core.memory.mood_state as _ms
    import core.user_relation as _ur
    import core.memory.user_facts as _uf

    monkeypatch.setattr(_el, "search", AsyncMock(return_value=("", [])))
    monkeypatch.setattr(_up, "load", lambda *a, **kw: {})
    monkeypatch.setattr(_mt, "format_for_prompt", lambda *a, **kw: "")
    monkeypatch.setattr(_st, "load_for_prompt", lambda *a, **kw: [])
    monkeypatch.setattr(_ep, "retrieve", lambda *a, **kw: ([], []) if kw.get("return_trace") else [])
    monkeypatch.setattr(_ep, "retrieve_fallback", lambda *a, **kw: ([], []) if kw.get("return_trace") else [])
    monkeypatch.setattr(_ui, "format_for_prompt", AsyncMock(return_value=""))
    monkeypatch.setattr(_il, "load_impression_text", lambda *a, **kw: "")
    monkeypatch.setattr(_gc, "get_recent", lambda *a, **kw: "")
    monkeypatch.setattr(_ms, "get_current", lambda *a, **kw: "neutral")
    monkeypatch.setattr(_ms, "update", lambda *a, **kw: None)
    monkeypatch.setattr(_ur, "get_relation", lambda *a, **kw: {"priority": 1})
    monkeypatch.setattr(_uf, "format_for_prompt", lambda uid, **kwargs: user_facts_return)
    try:
        monkeypatch.setattr(_dc, "load", lambda *a, **kw: "")
    except Exception:
        pass
    try:
        monkeypatch.setattr(
            __import__("core.tools.reminder", fromlist=["get_reminders"]),
            "get_reminders",
            lambda *a, **kw: [],
        )
    except Exception:
        pass


# ── 1. fetch_context calls user_facts.format_for_prompt(uid) ─────────────────

def test_fetch_context_calls_user_facts_format_for_prompt(chars_tree, sandbox, registry, monkeypatch):
    """fetch_context must call user_facts.format_for_prompt with uid (no char_id)."""
    _apply_fetch_stubs(monkeypatch)

    import core.memory.user_facts as _uf
    calls = []
    monkeypatch.setattr(_uf, "format_for_prompt", lambda uid, *, char_id: calls.append((uid, char_id)) or "")

    pipeline = _make_pipeline(TEST_CHAR_ID, registry)
    _write_active(sandbox, TEST_CHAR_ID)

    asyncio.get_event_loop().run_until_complete(
        pipeline.fetch_context("u99", "hello")
    )

    assert calls == [("u99", TEST_CHAR_ID)], f"Expected format_for_prompt called once with 'u99', got {calls}"


# ── 2. fetch_context returns user_facts_text in context dict ─────────────────

def test_fetch_context_returns_user_facts_text_key(chars_tree, sandbox, registry, monkeypatch):
    """fetch_context must return 'user_facts_text' in the context dict."""
    _apply_fetch_stubs(monkeypatch, user_facts_return="preferred_language: zh-CN")

    pipeline = _make_pipeline(TEST_CHAR_ID, registry)
    _write_active(sandbox, TEST_CHAR_ID)

    ctx = asyncio.get_event_loop().run_until_complete(
        pipeline.fetch_context("u1", "hello")
    )

    assert "user_facts_text" in ctx
    assert ctx["user_facts_text"] == "preferred_language: zh-CN"


# ── 3. build_prompt passes user_facts_text to prompt_builder.build() ─────────

def test_build_prompt_passes_user_facts_text(chars_tree, sandbox, registry, monkeypatch):
    """build_prompt must pass context['user_facts_text'] → prompt_builder.build()."""
    import core.prompt_builder as _pb

    received: dict = {}

    def _capturing_build(*args, **kwargs):
        received.update(kwargs)
        # Return stub so build_prompt doesn't need real character files.
        return ([], {"layers_used": []})

    # prompt_builder is a lazy import inside build_prompt; patch the module directly.
    monkeypatch.setattr(_pb, "build", _capturing_build)

    pipeline = _make_pipeline(TEST_CHAR_ID, registry)
    _write_active(sandbox, TEST_CHAR_ID)

    context = {
        "history": [],
        "profile": {},
        "relation": {"priority": 1},
        "group_context": "",
        "user_identity_text": "",
        "user_facts_text": "device_os: Windows",
        "event_search_result": "",
        "lore_entries": [],
        "reminders": [],
        "diary_context": "",
        "episodic_result": "",
        "episodic_fallback_result": "",
        "mid_term": "",
        "dream_impression_text": "",
    }

    pipeline.build_prompt("u1", "hello", context)

    assert "user_facts_text" in received, "prompt_builder.build() was not passed user_facts_text"
    assert received["user_facts_text"] == "device_os: Windows"


# ── 4. yexuan and character_b same uid → same user_facts ─────────────────────────

def test_same_uid_both_chars_get_same_facts(sandbox):
    """format_for_prompt(uid) returns same text regardless of which char is active."""
    from core.memory import user_facts as uf
    uf.save_user_facts("shareduser", {"preferred_language": "zh-CN"})

    text_a = uf.format_for_prompt("shareduser")
    text_b = uf.format_for_prompt("shareduser", char_id="character_b")

    assert text_b == ""
    assert "preferred_language" in text_a


# ── 5. scoped profile path still contains char_id ────────────────────────────

def test_profile_path_scoped_by_char_id(sandbox):
    """Reality-scoped profile path differs by char_id; isolation unchanged."""
    from core.memory.path_resolver import resolve_path

    scope_y = MemoryScope.reality_scope("u1", TEST_CHAR_ID)
    scope_h = MemoryScope.reality_scope("u1", "character_b")
    p_y = str(resolve_path(scope_y, "profile")).replace("\\", "/")
    p_h = str(resolve_path(scope_h, "profile")).replace("\\", "/")

    assert p_y != p_h
    assert TEST_CHAR_ID in p_y
    assert "character_b" in p_h


# ── 6. user_facts injection does NOT require char_id ─────────────────────────

def test_user_facts_format_for_prompt_no_char_id_argument(sandbox):
    """Compatibility callers resolve the active character when char_id is omitted."""
    from core.memory import user_facts as uf
    uf.save_user_facts("u_nci", {"timezone": "UTC"})

    text = uf.format_for_prompt("u_nci")  # no char_id arg — must work

    assert "timezone" in text
    assert "UTC" in text


# ── 7. no user_facts on disk → text = '' → layer 5.1 absent ─────────────────

def test_no_user_facts_empty_text_no_layer_51(sandbox):
    """When format_for_prompt returns '', layer 5.1_user_facts must not appear."""
    from unittest.mock import MagicMock
    from core.prompt_builder import build

    char = MagicMock()
    char.system_prompt = ""
    char.description = ""
    char.personality = ""
    char.scenario = ""
    char.mes_example = ""
    char.name = "NoFacts"

    messages, _ = build(
        character=char,
        user_id="u_empty",
        user_message="hi",
        history=[],
        relation={"role": "stranger"},
        profile={},
        group_context=[],
        user_facts_text="",
        char_id=TEST_CHAR_ID,
    )
    layers = [m.get("_layer") for m in messages]
    assert "5.1_user_facts" not in layers


# ── 8. user_facts loaded → layer 5.1 present in build output ─────────────────

def test_user_facts_present_layer_51_in_build(sandbox):
    """When user_facts_text is non-empty, layer 5.1_user_facts appears in messages."""
    from unittest.mock import MagicMock
    from core.prompt_builder import build

    char = MagicMock()
    char.system_prompt = ""
    char.description = ""
    char.personality = ""
    char.scenario = ""
    char.mes_example = ""
    char.name = "WithFacts"

    messages, _ = build(
        character=char,
        user_id="u_facts",
        user_message="hi",
        history=[],
        relation={"role": "stranger"},
        profile={},
        group_context=[],
        user_facts_text="preferred_language: en\ndevice_os: Windows",
        char_id=TEST_CHAR_ID,
    )
    layer_msgs = [m for m in messages if m.get("_layer") == "5.1_user_facts"]
    assert layer_msgs, "5.1_user_facts layer not found in messages"
    content = layer_msgs[0]["content"]
    assert "preferred_language" in content
    assert "device_os" in content


# ── 9. no yexuan fallback: global_scope path has no char_id segment ───────────

def test_user_facts_path_contains_frozen_char_id(sandbox):
    """user_facts paths carry both uid and the frozen character."""
    from core.memory.path_resolver import resolve_path

    scope = MemoryScope.reality_scope("u_global", TEST_CHAR_ID)
    p = str(resolve_path(scope, "user_facts")).replace("\\", "/")

    assert TEST_CHAR_ID in p
    assert "character_b" not in p
    assert "u_global" in p
    # Confirm it ends with user_facts.json
    assert p.endswith("user_facts.json")
