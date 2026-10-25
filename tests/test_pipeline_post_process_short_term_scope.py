from tests.fixtures.public_assets import TEST_CHAR_ID
"""
tests/test_pipeline_post_process_short_term_scope.py

P1-0C.6: pipeline.post_process 中 _st.load 的 char_id 透传验收测试

Covers:
1.  post_process 中 profile 判断用 _st.load 收到 char_id="character_b"，不默认 yexuan。
2.  active 从 yexuan 切 character_b 后，第二次 post_process 的 _st.load 读取 character_b bucket。
3.  内容级验证：character_b post_process 的 user_profile_update recent 不含 yexuan bucket 唯一词。
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.asset_registry as _reg_mod
from core.asset_registry import AssetRegistry

# Import at module level so lazy module-level init runs before monkeypatch.chdir
import core.memory.short_term    # noqa: F401
import core.memory.event_log     # noqa: F401
import core.memory.fixation_pipeline  # noqa: F401


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
        json.dumps({"active_character": char_id, "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )


# ── 1. _st.load 收到 char_id="character_b"，不默认 yexuan ─────────────────────────

@pytest.mark.asyncio
async def test_post_process_st_load_receives_char_id_character_b(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    Inside post_process, both _st.load calls for the profile-update check
    must forward char_id='character_b', not the default TEST_CHAR_ID.
    """
    from core.write_envelope import WriteEnvelope, SourceType

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")

    captured_char_ids: list[str] = []

    def _spy_load(user_id, *, char_id=TEST_CHAR_ID):
        captured_char_ids.append(char_id)
        return []

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    with (
        patch("core.config_loader.get_config", return_value={"memory": {"summary_every_n_rounds": 20}}),
        patch("core.memory.short_term.load", side_effect=_spy_load),
        patch("core.memory.fixation_pipeline.capture_turn", return_value="u1_spy"),
        patch("core.post_process.slow_queue.enqueue", return_value=None),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
    ):
        await pipeline.post_process(user_id="u1", content="你好", reply="在的", envelope=env)

    assert captured_char_ids, "_st.load must be called at least once inside post_process"
    bad = [c for c in captured_char_ids if c != "character_b"]
    assert not bad, (
        f"All _st.load calls must receive char_id='character_b'; got unexpected: {bad}"
    )


# ── 2. yexuan → character_b 切换后，第二次读取使用 character_b ────────────────────────

@pytest.mark.asyncio
async def test_post_process_st_load_uses_new_char_id_after_switch(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    After writing active_character character_b, the next post_process call passes
    char_id='character_b' to _st.load (not TEST_CHAR_ID from the initial pipeline state).
    """
    from core.write_envelope import WriteEnvelope, SourceType

    pipeline = _make_pipeline(TEST_CHAR_ID, registry)
    _write_active(sandbox, TEST_CHAR_ID)

    captured: list[str] = []

    def _spy_load(user_id, *, char_id=TEST_CHAR_ID):
        captured.append(char_id)
        return []

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    with (
        patch("core.config_loader.get_config", return_value={"memory": {"summary_every_n_rounds": 20}}),
        patch("core.memory.short_term.load", side_effect=_spy_load),
        patch(
            "core.memory.fixation_pipeline.capture_turn",
            side_effect=lambda uid, *a, turn_id=None, **kw: turn_id or f"{uid}_spy",
        ),
        patch("core.post_process.slow_queue.enqueue", return_value=None),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
    ):
        # First turn: yexuan active
        await pipeline.post_process(user_id="u1", content="你好", reply="在的", envelope=env)
        first_calls = list(captured)
        assert first_calls, "_st.load must be called on first turn"
        assert all(c == TEST_CHAR_ID for c in first_calls), (
            f"First turn must read char_id=TEST_CHAR_ID; got {first_calls}"
        )

        captured.clear()
        _write_active(sandbox, "character_b")

        # Second turn: character_b active
        await pipeline.post_process(user_id="u1", content="今天怎样", reply="挺好的", envelope=env)
        assert captured, "_st.load must be called on second turn"
        assert all(c == "character_b" for c in captured), (
            f"After switch, _st.load must use char_id='character_b'; got {captured}"
        )


# ── 3. 内容级：profile recent 来自 character_b bucket，不含 yexuan 唯一词 ──────────

@pytest.mark.asyncio
async def test_post_process_profile_recent_reads_character_b_bucket_only(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    When user_profile_update triggers, the 'recent' slice sent to slow_queue
    must contain only character_b-bucket content. Yexuan-only sentinel must be absent.
    """
    import core.memory.short_term as _st_mod
    from core.write_envelope import WriteEnvelope, SourceType

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")

    uid = "u_pp_content_scope"
    YEXUAN_ONLY = f'草莓大福-{TEST_CHAR_ID}专属内容'
    CHARACTER_B_SIGNAL = "荔枝DemoUser-character_b专属"

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    # Override every_n=1 so profile update always triggers regardless of history length.
    # Also covers _st_mod.append calls which internally call get_config().
    mock_cfg = {"memory": {"summary_every_n_rounds": 1}}

    enqueued: list[dict] = []

    def _spy_enqueue(name, payload):
        enqueued.append({"name": name, "payload": payload})

    with (
        patch("core.config_loader.get_config", return_value=mock_cfg),
        # short_term.py imports get_config at module level, so patch its namespace too
        patch("core.memory.short_term.get_config", return_value=mock_cfg),
        patch("core.memory.fixation_pipeline.capture_turn", return_value=f"{uid}_spy"),
        patch("core.post_process.slow_queue.enqueue", side_effect=_spy_enqueue),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
    ):
        # Pre-populate both buckets inside the patch so get_config mock applies
        _st_mod.append(uid, "user", YEXUAN_ONLY, char_id=TEST_CHAR_ID)
        _st_mod.append(uid, "user", CHARACTER_B_SIGNAL, char_id="character_b")

        await pipeline.post_process(user_id=uid, content="你好", reply="在的", envelope=env)

    profile_updates = [e for e in enqueued if e["name"] == "user_profile_update"]
    assert profile_updates, "user_profile_update must be enqueued when profile update triggers"

    recent = profile_updates[0]["payload"].get("recent", [])
    recent_texts = " ".join(m.get("content", "") for m in recent)

    assert CHARACTER_B_SIGNAL in recent_texts, (
        f"profile recent must contain character_b sentinel '{CHARACTER_B_SIGNAL}'; got: {recent_texts!r}"
    )
    assert YEXUAN_ONLY not in recent_texts, (
        f"profile recent must NOT contain yexuan sentinel '{YEXUAN_ONLY}'; got: {recent_texts!r}"
    )
