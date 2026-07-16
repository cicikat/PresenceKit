"""
tests/test_pipeline_memory_scope_integration.py

P1-3B: pipeline 主流程 scope-first 集成验收测试

Covers:
1.  _current_reality_scope() uid == str(user_id)
2.  _current_reality_scope() character_id == active_char_id
3.  _current_reality_scope() domain == "reality"
4.  _current_reality_scope() raises on invalid active_character (no fallback)
5.  fetch_context store calls receive scope.character_id (not bare _active_character_id)
6.  fetch_context scope.uid is forwarded to stores
7.  fetch_context char switch: scope.character_id follows new active char
8.  post_process enqueue payload["scope"] == scope.to_payload()
9.  post_process payload["scope"] consistent: scope.uid == payload["uid"], scope.character_id == payload["char_id"]
10. post_process capture_turn_retry payload["scope"] == scope_payload
11. post_process user_profile_update payload["scope"] == scope_payload
12. post_process invalid active raises, enqueue never called
13. Regression: existing read-scope char_id forwarding still passes
14. Regression: existing write-scope char_id forwarding still passes
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.asset_registry as _reg_mod
from core.asset_registry import AssetRegistry
from core.memory.scope import MemoryScope

# Import all reader/writer modules at module level so lazy init runs before
# monkeypatch.chdir() changes the working directory in tests.
import core.memory.event_log           # noqa: F401
import core.memory.user_profile        # noqa: F401
import core.memory.mid_term            # noqa: F401
import core.memory.short_term          # noqa: F401
import core.memory.episodic_memory     # noqa: F401
import core.memory.user_identity       # noqa: F401
import core.dream.impression_loader    # noqa: F401
import core.memory.group_context       # noqa: F401
import core.memory.diary_context       # noqa: F401
import core.tools.reminder             # noqa: F401
import core.memory.mood_state          # noqa: F401
import core.user_relation              # noqa: F401
import core.memory.fixation_pipeline   # noqa: F401


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def chars_tree(tmp_path):
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
        json.dumps({
            "active_character": char_id,
            "enabled_lorebooks": [],
            "enabled_jailbreaks": [],
        }),
        encoding="utf-8",
    )


def _apply_fetch_stubs(monkeypatch):
    """Stub out all I/O dependencies in fetch_context."""
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

    try:
        monkeypatch.setattr(_dc, "load", lambda *a, **kw: "")
    except Exception:
        pass
    import core.tools.reminder as _rem
    try:
        monkeypatch.setattr(_rem, "get_reminders", lambda *a, **kw: [])
    except Exception:
        pass


# ── 1. _current_reality_scope uid == str(user_id) ────────────────────────────

def test_current_reality_scope_uid_equals_user_id(chars_tree, sandbox, registry):
    pipeline = _make_pipeline("yexuan", registry)
    _write_active(sandbox, "yexuan")

    scope = pipeline._current_reality_scope("u42")

    assert scope.uid == "u42", f"scope.uid must equal user_id, got {scope.uid!r}"


# ── 2. _current_reality_scope character_id == active_char_id ─────────────────

def test_current_reality_scope_character_id_equals_active(chars_tree, sandbox, registry):
    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")

    scope = pipeline._current_reality_scope("u1")

    assert scope.character_id == "character_b", (
        f"scope.character_id must equal active char, got {scope.character_id!r}"
    )


# ── 3. _current_reality_scope domain == "reality" ────────────────────────────

def test_current_reality_scope_domain_is_reality(chars_tree, sandbox, registry):
    pipeline = _make_pipeline("yexuan", registry)
    _write_active(sandbox, "yexuan")

    scope = pipeline._current_reality_scope("u1")

    assert scope.domain == "reality", f"scope.domain must be 'reality', got {scope.domain!r}"


# ── 4. _current_reality_scope raises on invalid active (no yexuan fallback) ───

def test_current_reality_scope_raises_on_invalid_active(chars_tree, sandbox, registry):
    pipeline = _make_pipeline("yexuan", registry)
    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "nonexistent_char", "enabled_lorebooks": [],
                    "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    with pytest.raises((ValueError, RuntimeError)):
        pipeline._current_reality_scope("u1")


# ── 5. fetch_context stores receive scope.character_id ────────────────────────

def test_fetch_context_stores_receive_scope_char_id(chars_tree, monkeypatch, sandbox, registry):
    """
    When pipeline is active 'character_b', fetch_context must forward
    scope.character_id='character_b' to short_term.load_for_prompt.
    """
    import core.memory.short_term as _st

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")
    _apply_fetch_stubs(monkeypatch)

    received_char_ids: list[str] = []

    def _spy(user_id, *, budget_rounds=None, near_k=5, char_id="yexuan"):
        received_char_ids.append(char_id)
        return []

    monkeypatch.setattr(_st, "load_for_prompt", _spy)

    asyncio.run(pipeline.fetch_context(user_id="u1", content="hello"))

    assert received_char_ids, "short_term.load_for_prompt must be called"
    assert all(c == "character_b" for c in received_char_ids), (
        f"short_term must receive scope.character_id='character_b', got {received_char_ids}"
    )


# ── 6. fetch_context scope.uid forwarded to stores ────────────────────────────

def test_fetch_context_scope_uid_forwarded_to_stores(chars_tree, monkeypatch, sandbox, registry):
    """
    short_term.load_for_prompt first positional arg must be scope.uid == str(user_id).
    """
    import core.memory.short_term as _st

    pipeline = _make_pipeline("yexuan", registry)
    _write_active(sandbox, "yexuan")
    _apply_fetch_stubs(monkeypatch)

    received_uids: list[str] = []

    def _spy(user_id, *, budget_rounds=None, near_k=5, char_id="yexuan"):
        received_uids.append(user_id)
        return []

    monkeypatch.setattr(_st, "load_for_prompt", _spy)

    asyncio.run(pipeline.fetch_context(user_id="u_scope_uid", content="hello"))

    assert received_uids, "short_term.load_for_prompt must be called"
    assert received_uids[0] == "u_scope_uid", (
        f"store must receive scope.uid='u_scope_uid', got {received_uids[0]!r}"
    )


# ── 7. fetch_context char switch: scope.character_id follows new active ───────

def test_fetch_context_scope_char_id_follows_char_switch(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    After switching active_character to character_b, the next fetch_context call
    must pass scope.character_id='character_b' to stores.
    """
    import core.memory.short_term as _st

    pipeline = _make_pipeline("yexuan", registry)
    _write_active(sandbox, "yexuan")
    _apply_fetch_stubs(monkeypatch)

    received: list[str] = []

    def _spy(user_id, *, budget_rounds=None, near_k=5, char_id="yexuan"):
        received.append(char_id)
        return []

    monkeypatch.setattr(_st, "load_for_prompt", _spy)

    asyncio.run(pipeline.fetch_context(user_id="u1", content="hello"))
    assert received and received[-1] == "yexuan", (
        f"First call must use yexuan, got {received}"
    )

    _write_active(sandbox, "character_b")
    asyncio.run(pipeline.fetch_context(user_id="u1", content="hello"))
    assert received[-1] == "character_b", (
        f"After switch, scope.character_id must be 'character_b', got {received[-1]!r}"
    )


# ── 8. post_process enqueue payload["scope"] == scope.to_payload() ───────────

@pytest.mark.asyncio
async def test_post_process_enqueue_scope_payload_present(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    summarize_to_midterm enqueue payload must contain a 'scope' field that
    equals MemoryScope.reality_scope(uid, active_char_id).to_payload().
    """
    from core.write_envelope import WriteEnvelope, SourceType

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")

    enqueued: list[dict] = []

    def _spy_enqueue(name, payload):
        enqueued.append({"name": name, "payload": payload})

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    with (
        patch("core.config_loader.get_config",
              return_value={"memory": {"summary_every_n_rounds": 20}}),
        patch("core.memory.short_term.load", return_value=[]),
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
        patch("core.memory.fixation_pipeline.capture_turn", return_value="u1_spy"),
        patch("core.post_process.slow_queue.enqueue", side_effect=_spy_enqueue),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
    ):
        await pipeline.post_process(user_id="u1", content="hello", reply="hi", envelope=env)

    mt_payloads = [e for e in enqueued if e["name"] == "summarize_to_midterm"]
    assert mt_payloads, "summarize_to_midterm must be enqueued"

    payload = mt_payloads[0]["payload"]
    assert "scope" in payload, "payload must contain 'scope' field"

    expected_scope = MemoryScope.reality_scope("u1", "character_b").to_payload()
    assert payload["scope"] == expected_scope, (
        f"payload['scope'] must equal scope.to_payload(), got {payload['scope']!r}"
    )


# ── 9. post_process payload scope consistent with uid/char_id ─────────────────

@pytest.mark.asyncio
async def test_post_process_payload_scope_consistent_with_uid_char_id(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    For all enqueue calls: payload['scope']['uid'] == payload['uid']
    and payload['scope']['character_id'] == payload['char_id'].
    """
    from core.write_envelope import WriteEnvelope, SourceType

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")

    enqueued: list[dict] = []

    def _spy_enqueue(name, payload):
        enqueued.append({"name": name, "payload": payload})

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    with (
        patch("core.config_loader.get_config",
              return_value={"memory": {"summary_every_n_rounds": 1}}),
        patch("core.memory.short_term.get_config",
              return_value={"memory": {"summary_every_n_rounds": 1}}),
        patch("core.memory.short_term.load", return_value=[{"role": "user", "content": "x"}]),
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
        patch("core.memory.fixation_pipeline.capture_turn", return_value="u1_spy"),
        patch("core.post_process.slow_queue.enqueue", side_effect=_spy_enqueue),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
    ):
        await pipeline.post_process(user_id="u1", content="hello", reply="hi", envelope=env)

    scoped = [e for e in enqueued if "scope" in e.get("payload", {})]
    assert scoped, "At least one enqueue must contain 'scope'"

    for entry in scoped:
        name = entry["name"]
        p = entry["payload"]
        scope_dict = p["scope"]
        assert scope_dict["uid"] == p["uid"], (
            f"{name}: scope.uid={scope_dict['uid']!r} != payload uid={p['uid']!r}"
        )
        assert scope_dict["character_id"] == p["char_id"], (
            f"{name}: scope.character_id={scope_dict['character_id']!r} != "
            f"payload char_id={p['char_id']!r}"
        )


# ── 10. post_process capture_turn_retry scope_payload ─────────────────────────

@pytest.mark.asyncio
async def test_post_process_capture_turn_retry_scope_payload(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    When capture_turn fails, capture_turn_retry enqueue must include
    scope payload matching the active char scope.
    """
    from core.write_envelope import WriteEnvelope, SourceType

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")

    enqueued: list[dict] = []

    def _spy_enqueue(name, payload):
        enqueued.append({"name": name, "payload": payload})

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    with (
        patch("core.config_loader.get_config",
              return_value={"memory": {"summary_every_n_rounds": 20}}),
        patch("core.memory.short_term.load", return_value=[]),
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
        patch("core.memory.fixation_pipeline.capture_turn",
              side_effect=RuntimeError("forced failure")),
        patch("core.post_process.slow_queue.enqueue", side_effect=_spy_enqueue),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
    ):
        await pipeline.post_process(user_id="u1", content="hello", reply="hi", envelope=env)

    retry_payloads = [e for e in enqueued if e["name"] == "capture_turn_retry"]
    assert retry_payloads, "capture_turn_retry must be enqueued on capture_turn failure"

    p = retry_payloads[0]["payload"]
    assert "scope" in p, "capture_turn_retry payload must contain 'scope'"

    expected = MemoryScope.reality_scope("u1", "character_b").to_payload()
    assert p["scope"] == expected, (
        f"capture_turn_retry scope must match active char scope, got {p['scope']!r}"
    )


# ── 11. post_process user_profile_update scope_payload ────────────────────────

@pytest.mark.asyncio
async def test_post_process_user_profile_update_scope_payload(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    user_profile_update enqueue payload must contain scope consistent
    with the active char when the profile update is triggered.
    """
    from core.write_envelope import WriteEnvelope, SourceType

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")

    enqueued: list[dict] = []

    def _spy_enqueue(name, payload):
        enqueued.append({"name": name, "payload": payload})

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    with (
        patch("core.config_loader.get_config",
              return_value={"memory": {"summary_every_n_rounds": 1}}),
        patch("core.memory.short_term.get_config",
              return_value={"memory": {"summary_every_n_rounds": 1}}),
        patch("core.memory.short_term.load",
              return_value=[{"role": "user", "content": "x"}]),
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
        patch("core.memory.fixation_pipeline.capture_turn", return_value="u1_spy"),
        patch("core.post_process.slow_queue.enqueue", side_effect=_spy_enqueue),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
    ):
        await pipeline.post_process(user_id="u1", content="hello", reply="hi", envelope=env)

    up_payloads = [e for e in enqueued if e["name"] == "user_profile_update"]
    assert up_payloads, "user_profile_update must be enqueued"

    p = up_payloads[0]["payload"]
    assert "scope" in p, "user_profile_update payload must contain 'scope'"
    assert p["scope"]["character_id"] == "character_b", (
        f"user_profile_update scope.character_id must be 'character_b', got "
        f"{p['scope']['character_id']!r}"
    )


# ── 12. post_process invalid active raises, enqueue never called ───────────────

@pytest.mark.asyncio
async def test_post_process_invalid_active_raises_no_enqueue(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    When active_character is invalid, post_process raises and enqueue is
    never called — no fallback yexuan scope is created.
    """
    from core.write_envelope import WriteEnvelope, SourceType

    pipeline = _make_pipeline("yexuan", registry)
    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "nonexistent_char",
                    "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    enqueue_called: list = []

    def _fail_enqueue(name, payload):
        enqueue_called.append(name)

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    with (
        patch("core.post_process.slow_queue.enqueue", side_effect=_fail_enqueue),
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
    ):
        with pytest.raises((ValueError, RuntimeError)):
            await pipeline.post_process(
                user_id="u1", content="hello", reply="hi", envelope=env
            )

    assert enqueue_called == [], (
        f"enqueue must NOT be called on invalid active_character, got {enqueue_called}"
    )


# ── 13. Regression: fetch_context char_id read-path forwarding ────────────────

def test_regression_fetch_context_char_id_forwarding(
    chars_tree, monkeypatch, sandbox, registry
):
    """Regression guard: existing T-01 read-path char_id forwarding still works."""
    import core.memory.user_profile as _up

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")
    _apply_fetch_stubs(monkeypatch)

    received: list[str] = []

    def _spy(user_id, *, char_id="yexuan"):
        received.append(char_id)
        return {}

    monkeypatch.setattr(_up, "load", _spy)

    asyncio.run(pipeline.fetch_context(user_id="u1", content="hello"))

    assert received and received[0] == "character_b", (
        f"Regression: user_profile.load must receive char_id='character_b', got {received}"
    )


# ── 14. Regression: post_process write-path char_id forwarding ────────────────

@pytest.mark.asyncio
async def test_regression_post_process_char_id_forwarding(
    chars_tree, monkeypatch, sandbox, registry
):
    """Regression guard: existing T-02 write-path char_id forwarding still works."""
    import core.memory.fixation_pipeline as _fp
    from core.write_envelope import WriteEnvelope, SourceType

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")

    captured: list[str] = []

    def _spy_ct(uid, user_msg, reply, emotion="neutral", turn_id=None,
                trigger_name="", envelope=None, *, char_id="yexuan", audit_extras=None,
                source=""):
        captured.append(char_id)
        return turn_id or f"{uid}_spy"

    monkeypatch.setattr(_fp, "capture_turn", _spy_ct)

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    with (
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
        patch("core.memory.short_term.load", return_value=[]),
        patch("core.post_process.slow_queue.enqueue", return_value=None),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
    ):
        await pipeline.post_process(user_id="u1", content="hello", reply="hi", envelope=env)

    assert captured and captured[0] == "character_b", (
        f"Regression: capture_turn must receive char_id='character_b', got {captured}"
    )
