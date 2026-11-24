from tests.fixtures.public_assets import TEST_CHAR_ID
"""
tests/test_impression_char_scope.py — P0-T05: impression char_id scope isolation

Covers:
1.  impression_store writer routes to correct char_id bucket
2.  impression_store reader isolates by char_id
3.  pipeline.context.fetch passes active char_id to impression_loader
4.  Character switch: fetch_context uses new char_id for impression
5.  Invalid active_character: fetch_context raises, impression reader not called
6.  distill_impression writer uses explicit char_id param
7.  Legacy default compat: no-arg calls default to yexuan

Note: tests 3/4/5 also have full coverage in test_pipeline_read_scope.py tests 7/8/9.
These versions are impression-focused regression guards.

P0-T05.5 fixed: dream_pipeline._generate_summary_bg now reads char_id from dream_state
and passes it to distill_impression.  Archive paths are also char_id-scoped.
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.asset_registry as _reg_mod
from core.asset_registry import AssetRegistry

# Import at module level so module-init runs while cwd == project root
import core.dream.impression_loader   # noqa: F401
import core.memory.event_log          # noqa: F401
import core.memory.user_profile       # noqa: F401
import core.memory.mid_term           # noqa: F401
import core.memory.short_term         # noqa: F401
import core.memory.episodic_memory    # noqa: F401
import core.memory.user_identity      # noqa: F401
import core.memory.group_context      # noqa: F401
import core.memory.diary_context      # noqa: F401
import core.tools.reminder            # noqa: F401
import core.memory.mood_state         # noqa: F401
import core.user_relation             # noqa: F401


_UID = "imp_scope_u1"


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


def _write_active(sandbox, char_id: str) -> None:
    p = sandbox.active_prompt_assets()
    p.write_text(
        json.dumps({"active_character": char_id,
                    "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )


def _run_fetch(pipeline, user_id=_UID, content="hello"):
    return asyncio.run(pipeline.context.fetch(user_id=user_id, content=content))


def _apply_base_stubs(monkeypatch) -> None:
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


def _make_entry(text: str) -> dict:
    now = time.time()
    return {
        "dream_id": f"dream_scope_{int(now)}",
        "ts": now,
        "last_decay_ts": now,
        "impression_text": text,
        "weight": 0.3,
        "emotional_tags": [],
        "exit_type": "soft",
        "decay_after": now + 30 * 86400,
        "marked": True,
    }


# ── 1. Writer routes to correct char_id bucket ───────────────────────────────

def test_impression_store_write_goes_to_char_id_bucket(sandbox):
    """append_impression with char_id='character_b' writes only to the character_b bucket."""
    from core.dream.impression_store import append_impression, load_impressions
    from core.sandbox import safe_user_id

    append_impression(_UID, _make_entry("我好像在梦里有种DemoUser的感觉"), char_id="character_b")

    character_b_entries = load_impressions(_UID, char_id="character_b")
    yexuan_entries = load_impressions(_UID, char_id=TEST_CHAR_ID)

    assert len(character_b_entries) == 1
    assert "DemoUser" in character_b_entries[0]["impression_text"]
    assert yexuan_entries == [], f"Must not write to {TEST_CHAR_ID} bucket when char_id='character_b'"

    # Also verify physical file locations
    safe_uid = safe_user_id(_UID)
    assert (sandbox.dreams_impressions_dir(char_id="character_b") / f"{safe_uid}.json").exists()
    assert not (sandbox.dreams_impressions_dir(char_id=TEST_CHAR_ID) / f"{safe_uid}.json").exists()


# ── 2. Reader isolates by char_id ─────────────────────────────────────────────

def test_impression_store_read_isolated_by_char_id(sandbox):
    """load_impressions / get_active_impressions / load_impression_text isolate by char_id."""
    from core.dream.impression_store import (
        append_impression, load_impressions, get_active_impressions,
    )
    from core.dream.impression_loader import load_impression_text

    uid = "imp_scope_u2"

    append_impression(uid, _make_entry("Companion内容"), char_id=TEST_CHAR_ID)
    append_impression(uid, _make_entry("DemoUser内容"), char_id="character_b")

    # load_impressions
    y = load_impressions(uid, char_id=TEST_CHAR_ID)
    h = load_impressions(uid, char_id="character_b")
    assert any("Companion" in e["impression_text"] for e in y), f'{TEST_CHAR_ID} bucket missing {TEST_CHAR_ID} entry'
    assert all("DemoUser" not in e["impression_text"] for e in y), f'{TEST_CHAR_ID} bucket leaked character_b entry'
    assert any("DemoUser" in e["impression_text"] for e in h), "character_b bucket missing character_b entry"
    assert all("Companion" not in e["impression_text"] for e in h), f'character_b bucket leaked {TEST_CHAR_ID} entry'

    # get_active_impressions
    ay = get_active_impressions(uid, char_id=TEST_CHAR_ID)
    ah = get_active_impressions(uid, char_id="character_b")
    assert any("Companion" in e["impression_text"] for e in ay)
    assert all("DemoUser" not in e["impression_text"] for e in ay)
    assert any("DemoUser" in e["impression_text"] for e in ah)
    assert all("Companion" not in e["impression_text"] for e in ah)

    # load_impression_text
    text_y = load_impression_text(uid, char_id=TEST_CHAR_ID)
    text_h = load_impression_text(uid, char_id="character_b")
    assert "Companion" in text_y and "DemoUser" not in text_y, (
        f"yexuan text should contain 'Companion' only, got: {text_y!r}"
    )
    assert "DemoUser" in text_h and "Companion" not in text_h, (
        f"character_b text should contain 'DemoUser' only, got: {text_h!r}"
    )


# ── 3. fetch_context passes active char_id to impression_loader ───────────────

def test_fetch_context_passes_char_id_to_impression_loader(
    chars_tree, monkeypatch, sandbox, registry
):
    """Pipeline with active=character_b must call load_impression_text(char_id='character_b')."""
    import core.dream.impression_loader as _il

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")
    _apply_base_stubs(monkeypatch)

    captured: list[str] = []

    def _spy(uid, *, char_id=TEST_CHAR_ID, **_kwargs):
        captured.append(char_id)
        return ""

    monkeypatch.setattr(_il, "load_impression_text", _spy)
    _run_fetch(pipeline)

    assert len(captured) >= 1, "load_impression_text should be called"
    assert captured[0] == "character_b", (
        f"load_impression_text must receive char_id='character_b', got {captured[0]!r}"
    )


# ── 4. Character switch: impression reader gets new char_id ───────────────────

def test_fetch_context_impression_char_id_updates_after_switch(
    chars_tree, monkeypatch, sandbox, registry
):
    """After switching active_character yexuan→character_b, impression reader gets character_b."""
    import core.dream.impression_loader as _il

    pipeline = _make_pipeline(TEST_CHAR_ID, registry)
    _write_active(sandbox, TEST_CHAR_ID)
    _apply_base_stubs(monkeypatch)

    captured: list[str] = []

    def _spy(uid, *, char_id=TEST_CHAR_ID, **_kwargs):
        captured.append(char_id)
        return ""

    monkeypatch.setattr(_il, "load_impression_text", _spy)

    _run_fetch(pipeline)
    assert captured[-1] == TEST_CHAR_ID, (
        f"First call: expected char_id=TEST_CHAR_ID, got {captured[-1]!r}"
    )

    _write_active(sandbox, "character_b")
    _run_fetch(pipeline)
    assert captured[-1] == "character_b", (
        f"After switch: expected char_id='character_b', got {captured[-1]!r}"
    )


# ── 5. Invalid active: fetch_context raises, impression reader not called ──────

def test_fetch_context_invalid_active_does_not_call_impression_loader(
    chars_tree, monkeypatch, sandbox, registry
):
    """When active_character is unknown, fetch_context raises before calling impression reader."""
    import core.dream.impression_loader as _il

    pipeline = _make_pipeline(TEST_CHAR_ID, registry)
    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "missing_id",
                    "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    reader_called: list[bool] = []

    def _spy(uid, *, char_id=TEST_CHAR_ID, **_kwargs):
        reader_called.append(True)
        return ""

    monkeypatch.setattr(_il, "load_impression_text", _spy)

    with pytest.raises((ValueError, RuntimeError)):
        _run_fetch(pipeline)

    assert reader_called == [], (
        "load_impression_text must NOT be called when active_character is invalid"
    )


# ── 6. distill_impression writer uses explicit char_id ───────────────────────

def test_distill_impression_writes_to_explicit_char_id_bucket(sandbox):
    """
    distill_impression(uid, dream_id, exit_type, char_id='character_b') writes only to
    the character_b impression bucket.

    P0-T05.5 fixed: dream_pipeline._generate_summary_bg now reads char_id from
    dream_state and passes it explicitly to distill_impression.  Archive path is
    also char_id-scoped so the write and read use the same bucket.
    """
    from core.dream.impression_store import load_impressions
    from core.dream.distill_impression import distill_impression
    from core.sandbox import get_paths

    uid = "distill_scope_u1"
    dream_id = f"dream_{uid}_scope"

    # Write archive to the character_b-scoped path (char_id must match the distill call)
    archive_dir = get_paths().dreams_archive_dir(char_id="character_b")
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / f"dream_{dream_id}.jsonl").write_text(
        json.dumps({"role": "user", "content": "测试梦境内容"}) + "\n",
        encoding="utf-8",
    )

    mock_result = {
        "impression_text": "我好像在梦里有种DemoUser般的感觉",
        "emotional_tags": ["温热"],
        "weight": 0.3,
    }

    with patch(
        "core.dream.distill_impression._llm_distill",
        new=AsyncMock(return_value=mock_result),
    ):
        asyncio.run(distill_impression(uid, dream_id, "soft", char_id="character_b"))

    character_b_entries = load_impressions(uid, char_id="character_b")
    yexuan_entries = load_impressions(uid, char_id=TEST_CHAR_ID)

    assert len(character_b_entries) == 1, (
        f"Expected 1 entry in character_b bucket, got {len(character_b_entries)}"
    )
    assert "DemoUser" in character_b_entries[0]["impression_text"]
    assert yexuan_entries == [], (
        f"distill_impression with char_id='character_b' must NOT write to {TEST_CHAR_ID} bucket"
    )


# ── 7. Legacy default compat: no-arg calls default to yexuan ─────────────────

def test_legacy_no_char_id_defaults_to_yexuan(sandbox):
    """
    append_impression / load_impressions without explicit char_id default to yexuan.
    This is legacy compatibility — production paths must pass char_id explicitly.
    """
    from core.dream.impression_store import append_impression, load_impressions

    uid = "legacy_compat_u1"

    append_impression(uid, _make_entry("Companion默认内容"))

    assert len(load_impressions(uid, char_id=TEST_CHAR_ID)) == 1, (
        f'Default (no char_id) must write to {TEST_CHAR_ID} bucket'
    )
    assert load_impressions(uid, char_id="character_b") == [], (
        "Default write must not touch character_b bucket"
    )
