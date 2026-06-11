"""
tests/test_pipeline_write_scope.py

P0-T02: capture_turn / fixation 写路径透传 char_id 验收测试

Covers:
1.  capture_turn() passes char_id to short_term.append
2.  capture_turn() passes char_id to event_log.append
3.  pipeline.post_process() passes active char_id to capture_turn
4.  Active character switch: post_process uses the new char_id after switch
5.  Invalid active_character: post_process raises, short_term/event_log never called
6.  Content-level isolation: yexuan and hongcha writes land in different buckets
7.  T-01 regression: fetch_context read paths still use active char_id
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.asset_registry as _reg_mod
from core.asset_registry import AssetRegistry


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def chars_tree(tmp_path):
    """Minimal characters/ tree with yexuan + hongcha."""
    chars = tmp_path / "characters"
    chars.mkdir()

    (chars / "yexuan.json").write_text(
        json.dumps({"name": "叶瑄", "description": "test", "world_book": []}),
        encoding="utf-8",
    )
    (chars / "hongcha.json").write_text(
        json.dumps({"name": "红茶", "description": "hongcha test", "world_book": []}),
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
    lore.match.return_value = []
    return Pipeline(char, lore_engine=lore, active_character_id=char_id)


def _write_active(sandbox, char_id: str):
    p = sandbox.active_prompt_assets()
    p.write_text(
        json.dumps({"active_character": char_id, "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )


# ── 1. capture_turn passes char_id to short_term.append ──────────────────────

def test_capture_turn_passes_char_id_to_short_term(sandbox):
    """capture_turn(char_id='hongcha') must forward char_id to short_term.append."""
    import core.memory.short_term as _st
    import core.memory.event_log as _el
    from core.memory.fixation_pipeline import capture_turn
    from core.write_envelope import WriteEnvelope, SourceType

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    st_char_ids: list[str] = []

    original_st_append = _st.append

    def _spy_st(user_id, role, content, turn_id=None, *, char_id="yexuan"):
        st_char_ids.append(char_id)
        return True

    with (
        patch.object(_st, "append", side_effect=_spy_st),
        patch.object(_el, "append", return_value=True),
    ):
        capture_turn("u1", "你好", "在的", char_id="hongcha", envelope=env)

    assert st_char_ids, "short_term.append must be called"
    assert all(c == "hongcha" for c in st_char_ids), (
        f"short_term.append must receive char_id='hongcha', got {st_char_ids}"
    )


# ── 2. capture_turn passes char_id to event_log.append ───────────────────────

def test_capture_turn_passes_char_id_to_event_log(sandbox):
    """capture_turn(char_id='hongcha') must forward char_id to event_log.append."""
    import core.memory.short_term as _st
    import core.memory.event_log as _el
    from core.memory.fixation_pipeline import capture_turn
    from core.write_envelope import WriteEnvelope, SourceType

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    el_char_ids: list[str] = []

    def _spy_el(user_id, role, content, emotion="neutral", intensity=0, turn_id=None, trigger_name="", *, char_id="yexuan"):
        el_char_ids.append(char_id)
        return True

    with (
        patch.object(_st, "append", return_value=True),
        patch.object(_el, "append", side_effect=_spy_el),
    ):
        capture_turn("u1", "你好", "在的", char_id="hongcha", envelope=env)

    assert el_char_ids, "event_log.append must be called"
    assert all(c == "hongcha" for c in el_char_ids), (
        f"event_log.append must receive char_id='hongcha', got {el_char_ids}"
    )


# ── 3. pipeline.post_process passes active char_id to capture_turn ────────────

@pytest.mark.asyncio
async def test_post_process_passes_active_char_id_to_capture_turn(
    chars_tree, monkeypatch, sandbox, registry
):
    """post_process() must pass self._active_character_id as char_id to capture_turn."""
    pipeline = _make_pipeline("hongcha", registry)
    _write_active(sandbox, "hongcha")

    captured_char_ids: list[str] = []

    import core.memory.fixation_pipeline as _fp
    from core.write_envelope import WriteEnvelope, SourceType

    original_ct = _fp.capture_turn

    def _spy_ct(uid, user_msg, reply, emotion="neutral", turn_id=None, trigger_name="", envelope=None, *, char_id="yexuan", audit_extras=None):
        captured_char_ids.append(char_id)
        return turn_id or f"{uid}_spy"

    monkeypatch.setattr(_fp, "capture_turn", _spy_ct)

    import core.memory.locks as _locks
    import core.memory.short_term as _st

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    with (
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
        patch("core.memory.short_term.load", return_value=[]),
        patch("core.post_process.slow_queue.enqueue", return_value=None),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
    ):
        await pipeline.post_process(
            user_id="u1",
            content="你好",
            reply="在的",
            envelope=env,
        )

    assert captured_char_ids, "capture_turn must be called"
    assert captured_char_ids[0] == "hongcha", (
        f"post_process must pass char_id='hongcha', got {captured_char_ids[0]!r}"
    )


# ── 4. Active character switch: post_process uses new char_id ─────────────────

@pytest.mark.asyncio
async def test_post_process_uses_new_char_id_after_switch(
    chars_tree, monkeypatch, sandbox, registry
):
    """After switching active_character, the next post_process call uses the new char_id."""
    import core.memory.fixation_pipeline as _fp
    from core.write_envelope import WriteEnvelope, SourceType

    pipeline = _make_pipeline("yexuan", registry)
    _write_active(sandbox, "yexuan")

    captured_char_ids: list[str] = []

    def _spy_ct(uid, user_msg, reply, emotion="neutral", turn_id=None, trigger_name="", envelope=None, *, char_id="yexuan", audit_extras=None):
        captured_char_ids.append(char_id)
        return turn_id or f"{uid}_spy"

    monkeypatch.setattr(_fp, "capture_turn", _spy_ct)

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    common_patches = dict(
        detect_emotion=AsyncMock(return_value="neutral"),
    )

    with (
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
        patch("core.memory.short_term.load", return_value=[]),
        patch("core.post_process.slow_queue.enqueue", return_value=None),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
    ):
        # First turn: yexuan
        await pipeline.post_process("u1", "你好", "在的", envelope=env)
        assert captured_char_ids[-1] == "yexuan", (
            f"First turn must use yexuan, got {captured_char_ids[-1]!r}"
        )

        # Switch to hongcha
        _write_active(sandbox, "hongcha")

        # Second turn: must now use hongcha
        await pipeline.post_process("u1", "今天怎样", "挺好的", envelope=env)
        assert captured_char_ids[-1] == "hongcha", (
            f"After switch, post_process must use hongcha, got {captured_char_ids[-1]!r}"
        )


# ── 5. Invalid active_character: post_process raises, no writes ───────────────

@pytest.mark.asyncio
async def test_post_process_invalid_active_does_not_write(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    When active_character is unknown, post_process raises before any write.
    short_term.append and event_log.append must never be called.
    """
    import core.memory.short_term as _st
    import core.memory.event_log as _el
    from core.write_envelope import WriteEnvelope, SourceType

    pipeline = _make_pipeline("yexuan", registry)

    # Pipeline currently has yexuan; active_prompt_assets changes to unknown
    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "missing_id", "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    st_called: list = []
    el_called: list = []

    def _fail_st(*args, **kwargs):
        st_called.append(args)
        pytest.fail("short_term.append must NOT be called when active_character is invalid")

    def _fail_el(*args, **kwargs):
        el_called.append(args)
        pytest.fail("event_log.append must NOT be called when active_character is invalid")

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    with (
        patch.object(_st, "append", side_effect=_fail_st),
        patch.object(_el, "append", side_effect=_fail_el),
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
    ):
        with pytest.raises((ValueError, RuntimeError)):
            await pipeline.post_process("u1", "你好", "在的", envelope=env)

    assert st_called == [], "short_term.append must not be called on invalid active_character"
    assert el_called == [], "event_log.append must not be called on invalid active_character"


# ── 6. Content-level isolation: yexuan and hongcha write to different buckets ──

def test_capture_turn_content_isolation_by_char_id(sandbox):
    """
    capture_turn with char_id='yexuan' and char_id='hongcha' write to separate buckets.
    short_term.load_for_prompt(uid, char_id=...) shows different content per bucket.
    Does NOT drain slow_queue — T-02 only tests immediate short_term writes.
    """
    from core.memory.fixation_pipeline import capture_turn
    from core.memory.short_term import load_for_prompt
    from core.write_envelope import WriteEnvelope, SourceType

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    uid = "t02_isolation_uid"

    # Write a turn for yexuan
    capture_turn(uid, "草莓大福-T02用户", "草莓大福-T02回复", char_id="yexuan", envelope=env)

    # Write a turn for hongcha
    capture_turn(uid, "XYZ动画-T02用户", "XYZ动画-T02回复", char_id="hongcha", envelope=env)

    yexuan_hist = load_for_prompt(uid, char_id="yexuan")
    hongcha_hist = load_for_prompt(uid, char_id="hongcha")

    yexuan_texts = " ".join(m.get("content", "") for m in yexuan_hist)
    hongcha_texts = " ".join(m.get("content", "") for m in hongcha_hist)

    assert "草莓大福-T02" in yexuan_texts, "yexuan bucket must contain 草莓大福-T02 turn"
    assert "XYZ动画-T02" not in yexuan_texts, "yexuan bucket must NOT contain hongcha's turn"

    assert "XYZ动画-T02" in hongcha_texts, "hongcha bucket must contain XYZ动画-T02 turn"
    assert "草莓大福-T02" not in hongcha_texts, "hongcha bucket must NOT contain yexuan's turn"


# ── 7. T-01 regression: fetch_context read path still uses active char_id ─────

# Import all reader modules at module level so their module-level init runs before
# monkeypatch.chdir() changes the directory in tests.
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


def _apply_fetch_stubs(monkeypatch):
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


def test_fetch_context_t01_regression_char_id_still_passes(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    T-01 regression: fetch_context() still passes active char_id to short_term.load_for_prompt.
    This test ensures T-02 changes did not break the T-01 read-path wiring.
    """
    import core.memory.short_term as _st

    pipeline = _make_pipeline("hongcha", registry)
    _write_active(sandbox, "hongcha")
    _apply_fetch_stubs(monkeypatch)

    captured: list[str] = []

    def _spy_load(user_id, *, budget_rounds=None, near_k=5, char_id="yexuan"):
        captured.append(char_id)
        return []

    monkeypatch.setattr(_st, "load_for_prompt", _spy_load)

    asyncio.run(pipeline.fetch_context(user_id="u1", content="hello"))

    assert captured, "short_term.load_for_prompt must be called"
    assert captured[0] == "hongcha", (
        f"T-01 regression: short_term.load_for_prompt must receive char_id='hongcha', got {captured[0]!r}"
    )
