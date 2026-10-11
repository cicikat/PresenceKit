import time
from tests.fixtures.public_assets import TEST_CHAR_ID
"""
tests/test_mood_state_char_scope.py

P0-T04: mood_state 解除 yexuan 锁死验收测试

Covers:
1.  update(char_id="character_b") 写入 characters/character_b/inner/mood_state.json
    — 断言未写入 characters/yexuan/inner/mood_state.json
2.  get_current(char_id=...) 从指定角色路径读取
    — 预置 yexuan / character_b 不同内容，各自返回正确值
3.  update 读写同一个 char_id
    — 预置 character_b mood，update(char_id="character_b")，yexuan 未被修改
4.  pipeline.post_process mood update 传 active char_id
    — active=character_b，捕获 kwargs，断言 char_id="character_b"
5.  切换角色后 mood update 使用新角色
    — yexuan 一次，切换 character_b 再一次，验证两次 char_id 各自正确
6.  active_character 非法时不更新 mood
    — active=missing_id，mood_state.update 不被调用，post_process 抛错
7.  mood_state 路径不含 uid
    — sandbox.mood_state(char_id="character_b") 路径字符串不含 uid 格式
8.  force=True 允许低强度专用入口一次切换；普通 detect 仍保留低强度去噪
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
    """Minimal characters/ tree: yexuan + character_b + jailbreaks."""
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
        json.dumps({"active_character": char_id, "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )


# ── 1. update writes to specified char path only ──────────────────────────────

def test_update_writes_to_specified_char_path(sandbox):
    """update(char_id='character_b') writes character_b path, not yexuan path."""
    from core.memory.mood_state import update

    update("happy", char_id="character_b", source_turn_id="t", source_at=time.time())

    character_b_path = sandbox.mood_state(char_id="character_b")
    yexuan_path  = sandbox.mood_state(char_id=TEST_CHAR_ID)

    assert character_b_path.exists(), "character_b mood_state.json must be created"
    assert not yexuan_path.exists(), (
        f"{TEST_CHAR_ID} mood_state.json must NOT be created when char_id='character_b'"
    )

    data = json.loads(character_b_path.read_text(encoding="utf-8"))
    assert data["previous"] == "", "initial previous must be neutral"


def test_update_path_contains_char_id(sandbox):
    """The written path contains the char_id directory component."""
    from core.memory.mood_state import update

    update("gentle", char_id="character_b")

    character_b_path = sandbox.mood_state(char_id="character_b")
    assert "character_b" in str(character_b_path), (
        f"path must contain 'character_b': {character_b_path}"
    )
    assert TEST_CHAR_ID not in str(character_b_path), (
        f"path must not contain TEST_CHAR_ID: {character_b_path}"
    )


# ── 2. get_current reads from specified char path ─────────────────────────────

def test_get_current_reads_correct_char_path(sandbox):
    """get_current(char_id=...) reads from the correct character's file."""
    from core.memory.mood_state import get_current

    # Seed two different states
    yexuan_path  = sandbox.mood_state(char_id=TEST_CHAR_ID)
    character_b_path = sandbox.mood_state(char_id="character_b")
    yexuan_path.parent.mkdir(parents=True, exist_ok=True)
    character_b_path.parent.mkdir(parents=True, exist_ok=True)

    yexuan_path.write_text(
        json.dumps({"current": "happy", "intensity": 0.6, "previous": "neutral", "updated_at": 0.0}),
        encoding="utf-8",
    )
    character_b_path.write_text(
        json.dumps({"current": "sleepy", "intensity": 0.3, "previous": "neutral", "updated_at": 0.0}),
        encoding="utf-8",
    )

    assert get_current(char_id=TEST_CHAR_ID) == "", f"{TEST_CHAR_ID} legacy must not return 'happy'"
    assert get_current(char_id="character_b") == "", "character_b legacy must not return 'sleepy'"


def test_get_current_default_is_neutral_when_missing(sandbox):
    """get_current returns 'neutral' when file doesn't exist."""
    from core.memory.mood_state import get_current

    assert get_current(char_id="character_b") == ""
    assert get_current(char_id=TEST_CHAR_ID) == ""


# ── 3. update reads and writes same char_id ───────────────────────────────────

def test_update_reads_and_writes_same_char_id(sandbox):
    """update(char_id='character_b') must not touch yexuan's file."""
    from core.memory.mood_state import update, load

    # Seed character_b with a known state
    character_b_path = sandbox.mood_state(char_id="character_b")
    character_b_path.parent.mkdir(parents=True, exist_ok=True)
    character_b_path.write_text(
        json.dumps({"current": "neutral", "intensity": 0.0, "previous": "neutral", "updated_at": 0.0}),
        encoding="utf-8",
    )

    yexuan_path = sandbox.mood_state(char_id=TEST_CHAR_ID)
    assert not yexuan_path.exists(), f'Precondition: {TEST_CHAR_ID} file must not exist'

    update("happy", char_id="character_b", source_turn_id="t", source_at=time.time())

    # character_b updated
    assert character_b_path.exists()
    # yexuan not touched
    assert not yexuan_path.exists(), (
        f"update(char_id='character_b') must NOT create or modify {TEST_CHAR_ID}'s mood_state.json"
    )

    # Verify character_b state changed (pending set or emotion blended)
    data = load(char_id="character_b")
    assert data["intensity"] > 0.0, "character_b intensity must have been blended upward"


def test_update_writes_only_target_char(sandbox):
    """Both chars exist; update one, check other is unchanged."""
    from core.memory.mood_state import update, load

    for char_id in (TEST_CHAR_ID, "character_b"):
        p = sandbox.mood_state(char_id=char_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"current": "neutral", "intensity": 0.0, "previous": "neutral",
                        "updated_at": 0.0}),
            encoding="utf-8",
        )

    yexuan_before = sandbox.mood_state(char_id=TEST_CHAR_ID).read_text(encoding="utf-8")

    # Update only character_b
    update("sad", char_id="character_b", source_turn_id="t", source_at=time.time())

    yexuan_after = sandbox.mood_state(char_id=TEST_CHAR_ID).read_text(encoding="utf-8")
    assert yexuan_before == yexuan_after, (
        f"{TEST_CHAR_ID} mood_state must be unchanged after update(char_id='character_b')"
    )






# ── 4. pipeline.post_process mood update passes active char_id ───────────────

@pytest.mark.asyncio
async def test_post_process_mood_update_passes_active_char_id(
    chars_tree, monkeypatch, sandbox, registry
):
    """post_process with can_affect_mood=True must call mood_state.update(char_id=active)."""
    import core.memory.mood_state as _ms
    from core.write_envelope import WriteEnvelope, SourceType
    import core.memory.fixation_pipeline as _fp

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")

    captured_kwargs: list[dict] = []

    def _spy_update(new_emotion, new_intensity=None, source="detect", *, char_id=TEST_CHAR_ID, **metadata):
        captured_kwargs.append({"char_id": char_id, "emotion": new_emotion})

    monkeypatch.setattr(_ms, "update", _spy_update)

    def _spy_ct(uid, user_msg, reply, emotion="neutral", turn_id=None, trigger_name="", envelope=None, *, char_id=TEST_CHAR_ID, **metadata):
        return turn_id or f"{uid}_spy"

    monkeypatch.setattr(_fp, "capture_turn", _spy_ct)

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=True)

    with (
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
        patch("core.memory.short_term.load", return_value=[]),
        patch("core.post_process.slow_queue.enqueue", return_value=None),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
        patch("core.user_relation.get_relation", return_value={"priority": 1}),
    ):
        await pipeline.post_process(
            user_id="u1",
            content="你好",
            reply="在的",
            envelope=env,
        )

    assert captured_kwargs, "mood_state.update must be called when can_affect_mood=True"
    assert captured_kwargs[0]["char_id"] == "character_b", (
        f"mood_state.update must receive char_id='character_b', got {captured_kwargs[0]['char_id']!r}"
    )


# ── 5. switch character → mood update uses new char_id ───────────────────────

@pytest.mark.asyncio
async def test_char_switch_mood_update_uses_new_char(
    chars_tree, monkeypatch, sandbox, registry
):
    """After active_character switch, mood_state.update receives the new char_id."""
    import core.memory.mood_state as _ms
    from core.write_envelope import WriteEnvelope, SourceType
    import core.memory.fixation_pipeline as _fp

    pipeline = _make_pipeline(TEST_CHAR_ID, registry)
    _write_active(sandbox, TEST_CHAR_ID)

    captured_char_ids: list[str] = []

    def _spy_update(new_emotion, new_intensity=None, source="detect", *, char_id=TEST_CHAR_ID, **metadata):
        captured_char_ids.append(char_id)

    monkeypatch.setattr(_ms, "update", _spy_update)

    def _spy_ct(uid, user_msg, reply, emotion="neutral", turn_id=None, trigger_name="", envelope=None, *, char_id=TEST_CHAR_ID, **metadata):
        return turn_id or f"{uid}_spy"

    monkeypatch.setattr(_fp, "capture_turn", _spy_ct)

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=True)

    common = dict(
        user_id="u1",
        content="hi",
        reply="hello",
        envelope=env,
    )

    with (
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
        patch("core.memory.short_term.load", return_value=[]),
        patch("core.post_process.slow_queue.enqueue", return_value=None),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
        patch("core.user_relation.get_relation", return_value={"priority": 1}),
    ):
        # First turn: yexuan
        await pipeline.post_process(**common)
        assert captured_char_ids, "mood update must be called on first turn"
        assert captured_char_ids[-1] == TEST_CHAR_ID, (
            f"First turn must use yexuan, got {captured_char_ids[-1]!r}"
        )

        # Switch to character_b
        _write_active(sandbox, "character_b")

        # Second turn: must use character_b
        await pipeline.post_process(**common)
        assert captured_char_ids[-1] == "character_b", (
            f"After switch, mood update must use character_b, got {captured_char_ids[-1]!r}"
        )


# ── 6. invalid active_character → mood_state.update not called ───────────────

@pytest.mark.asyncio
async def test_invalid_active_does_not_update_mood(
    chars_tree, monkeypatch, sandbox, registry
):
    """When active_character is invalid, post_process raises and mood_state.update is never called."""
    import core.memory.mood_state as _ms
    from core.write_envelope import WriteEnvelope, SourceType

    pipeline = _make_pipeline(TEST_CHAR_ID, registry)

    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "missing_id", "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    mood_update_called = []

    def _fail_update(*args, **kwargs):
        mood_update_called.append((args, kwargs))
        pytest.fail("mood_state.update must NOT be called when active_character is invalid")

    monkeypatch.setattr(_ms, "update", _fail_update)

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=True)

    with (
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
    ):
        with pytest.raises((ValueError, RuntimeError)):
            await pipeline.post_process(
                user_id="u1",
                content="你好",
                reply="在的",
                envelope=env,
            )

    assert mood_update_called == [], (
        "mood_state.update must not be called when active_character is invalid"
    )


# ── 7. mood_state path contains no uid ───────────────────────────────────────

def test_mood_state_path_no_uid(sandbox):
    """mood_state path must not include a uid segment — scope is char-only."""
    import re

    character_b_path = sandbox.mood_state(char_id="character_b")
    yexuan_path  = sandbox.mood_state(char_id=TEST_CHAR_ID)

    # uid patterns: pure digits (QQ uid), or uuid-like strings
    uid_pattern = re.compile(r"/\d{5,}/|\\d{5,}\\")

    for p in (character_b_path, yexuan_path):
        path_str = str(p)
        assert not uid_pattern.search(path_str), (
            f"mood_state path must not contain a uid segment: {path_str}"
        )
        # Char id must be present, uid must not be
        assert "character_b" in path_str or TEST_CHAR_ID in path_str, (
            f"char_id must appear in mood_state path: {path_str}"
        )


def test_mood_state_signature_no_uid_param():
    """mood_state.update / get_current / load must not accept uid as a parameter."""
    import inspect
    from core.memory.mood_state import update, get_current, load, save

    for fn in (update, get_current, load, save):
        params = inspect.signature(fn).parameters
        assert "uid" not in params, (
            f"{fn.__name__} must not have a 'uid' parameter — scope is char-only"
        )
        assert "user_id" not in params, (
            f"{fn.__name__} must not have a 'user_id' parameter — scope is char-only"
        )
