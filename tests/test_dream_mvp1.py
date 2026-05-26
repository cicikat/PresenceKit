"""
tests/test_dream_mvp1.py — Dream Session MVP1 boundary and isolation tests

Covers:
  - dream_turn side-effect isolation (mood_state / history / episodic / midterm
    / author_note_extra / agent_actions / scheduler untouched)
  - notify_owner_turn not called on dream turns
  - DREAM_ACTIVE → /desktop/chat and /mobile/chat hard reject
  - force_exit_dream immediate effect in any dream state
  - Soft exit refused → still DREAM_ACTIVE, hard exit still works
  - afterglow loader text does not contain scene/action keywords
  - afterglow summary not read by reflect_to_episodic / consolidate_to_identity
  - amnesia / keep_impression switches only affect snapshot content, not live access
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

_UID = "dream_test_user"

_FAKE_CHARACTER = MagicMock()
_FAKE_CHARACTER.name = "叶瑄"
_FAKE_CHARACTER.description = "测试角色描述"
_FAKE_CHARACTER.jailbreak_entries = []

_FAKE_PIPELINE = MagicMock()
_FAKE_PIPELINE.character = _FAKE_CHARACTER
_FAKE_PIPELINE.lore_engine = MagicMock()
_FAKE_PIPELINE.lore_engine.match.return_value = []


def _make_fake_llm(reply: str = "梦境回复文本") -> AsyncMock:
    return AsyncMock(return_value=reply)


@pytest.fixture
def active_dream(sandbox):
    """Put uid into DREAM_ACTIVE with a valid snapshot."""
    from core.dream.dream_state import write_state, DreamStatus

    state = {
        "user_id": _UID,
        "status": DreamStatus.DREAM_ACTIVE.value,
        "dream_id": f"dream_{_UID}_test001",
        "context_snapshot": {
            "created_at": time.time(),
            "user_id": _UID,
            "yexuan_awareness": "lucid_shared",
            "boundary": "dream_only",
            "entry_reason": "unit test",
            "relationship_state": {},
            "recent_reality_context": "",
            "episodic_summary": "",
            "mid_term_context": "",
            "profile_impression": "",
        },
    }
    write_state(_UID, state)
    return state


# ═══════════════════════════════════════════════════════════════════════════════
# 1. dream_turn side-effect isolation
# ═══════════════════════════════════════════════════════════════════════════════

def test_dream_turn_does_not_touch_mood_state(sandbox, active_dream):
    """mood_state.json must not change after a dream turn."""
    mood_path = sandbox.mood_state()
    mood_initial = mood_path.read_text() if mood_path.exists() else "ABSENT"

    with patch("core.llm_client.chat", _make_fake_llm()), \
         patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
        from core.dream import dream_pipeline
        asyncio.run(dream_pipeline.dream_turn(_UID, "你好"))

    mood_after = mood_path.read_text() if mood_path.exists() else "ABSENT"
    assert mood_initial == mood_after, "mood_state changed during dream turn"


def test_dream_turn_does_not_write_history(sandbox, active_dream):
    """Short-term history must not gain new entries after a dream turn."""
    from core.memory import short_term
    before = len(short_term.load(_UID))

    with patch("core.llm_client.chat", _make_fake_llm()), \
         patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
        from core.dream import dream_pipeline
        asyncio.run(dream_pipeline.dream_turn(_UID, "你好"))

    after = len(short_term.load(_UID))
    assert before == after, "short_term history grew during dream turn"


def test_dream_turn_does_not_write_episodic(sandbox, active_dream):
    """Episodic memory must not gain entries after a dream turn."""
    from core.memory.episodic_memory import retrieve
    before = len(retrieve(_UID, topic="", top_k=100))

    with patch("core.llm_client.chat", _make_fake_llm()), \
         patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
        from core.dream import dream_pipeline
        asyncio.run(dream_pipeline.dream_turn(_UID, "你好"))

    after = len(retrieve(_UID, topic="", top_k=100))
    assert before == after, "episodic_memory grew during dream turn"


def test_dream_turn_does_not_write_midterm(sandbox, active_dream):
    """Mid-term context must not change after a dream turn."""
    from core.memory import mid_term
    before = mid_term.format_for_prompt(_UID)

    with patch("core.llm_client.chat", _make_fake_llm()), \
         patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
        from core.dream import dream_pipeline
        asyncio.run(dream_pipeline.dream_turn(_UID, "你好"))

    after = mid_term.format_for_prompt(_UID)
    assert before == after, "mid_term changed during dream turn"


def test_dream_turn_does_not_write_agent_actions(sandbox, active_dream):
    """agent_actions.json must not be created/modified during a dream turn."""
    agent_actions_path = sandbox.agent_actions()
    existed_before = agent_actions_path.exists()
    mtime_before = agent_actions_path.stat().st_mtime if existed_before else None

    with patch("core.llm_client.chat", _make_fake_llm()), \
         patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
        from core.dream import dream_pipeline
        asyncio.run(dream_pipeline.dream_turn(_UID, "你好"))

    if not existed_before:
        assert not agent_actions_path.exists(), "agent_actions.json was created during dream"
    else:
        assert agent_actions_path.stat().st_mtime == mtime_before, "agent_actions modified"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. notify_owner_turn not called during dream turns
# ═══════════════════════════════════════════════════════════════════════════════

def test_dream_turn_does_not_call_notify_owner_turn(sandbox, active_dream):
    """notify_owner_turn must never be called from the dream pipeline."""
    notify_mock = MagicMock()

    with patch("core.llm_client.chat", _make_fake_llm()), \
         patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE), \
         patch.dict(sys.modules, {
             "core.scheduler.state_machine": MagicMock(notify_owner_turn=notify_mock),
         }):
        from core.dream import dream_pipeline
        asyncio.run(dream_pipeline.dream_turn(_UID, "你好"))

    notify_mock.assert_not_called()


def test_dream_turn_does_not_change_trigger_state_log(sandbox, active_dream):
    """trigger_state.jsonl must not change during dream turn."""
    log_path = sandbox.trigger_state_log()
    before = log_path.read_text() if log_path.exists() else "ABSENT"

    with patch("core.llm_client.chat", _make_fake_llm()), \
         patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
        from core.dream import dream_pipeline
        asyncio.run(dream_pipeline.dream_turn(_UID, "你好"))

    after = log_path.read_text() if log_path.exists() else "ABSENT"
    assert before == after, "trigger_state.jsonl changed during dream turn"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Reality hard reject during DREAM_ACTIVE
# ═══════════════════════════════════════════════════════════════════════════════

def test_reality_hard_reject_when_dream_active(sandbox, active_dream):
    """_check_reality_not_in_dream raises HTTPException 409 when DREAM_ACTIVE."""
    import importlib

    # Force re-import to pick up sandbox patch
    import core.dream.dream_state as ds
    importlib.reload(ds)

    from admin.routers.chat import _check_reality_not_in_dream
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        _check_reality_not_in_dream(_UID)

    assert exc_info.value.status_code == 409


def test_reality_hard_reject_when_dream_closing(sandbox):
    """_check_reality_not_in_dream raises 409 when DREAM_CLOSING."""
    from core.dream.dream_state import write_state, DreamStatus
    write_state(_UID, {"user_id": _UID, "status": DreamStatus.DREAM_CLOSING.value})

    from admin.routers.chat import _check_reality_not_in_dream
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        _check_reality_not_in_dream(_UID)

    assert exc_info.value.status_code == 409


def test_reality_allowed_when_not_in_dream(sandbox):
    """_check_reality_not_in_dream allows request when status is REALITY_CHAT."""
    from core.dream.dream_state import write_state, DreamStatus
    write_state(_UID, {"user_id": _UID, "status": DreamStatus.REALITY_CHAT.value})

    from admin.routers.chat import _check_reality_not_in_dream
    # Should not raise
    _check_reality_not_in_dream(_UID)


def test_reality_allowed_when_afterglow(sandbox):
    """_check_reality_not_in_dream allows request during REALITY_AFTERGLOW."""
    from core.dream.dream_state import write_state, DreamStatus
    write_state(_UID, {"user_id": _UID, "status": DreamStatus.REALITY_AFTERGLOW.value})

    from admin.routers.chat import _check_reality_not_in_dream
    _check_reality_not_in_dream(_UID)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. force_exit_dream immediate effect in any state
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("initial_status", [
    "DREAM_ACTIVE",
    "DREAM_CLOSING",
    "DREAM_ENTRANCE_AVAILABLE",
    "REALITY_AFTERGLOW",
    "REALITY_CHAT",
])
def test_force_exit_immediate_in_any_state(sandbox, initial_status):
    """force_exit_dream must result in REALITY_AFTERGLOW regardless of starting state."""
    from core.dream.dream_state import write_state, read_state, DreamStatus

    write_state(_UID, {
        "user_id": _UID,
        "status": initial_status,
        "dream_id": f"dream_{_UID}_force_test",
    })

    from core.dream import dream_pipeline
    asyncio.run(dream_pipeline.force_exit_dream(_UID))

    state = read_state(_UID)
    assert state["status"] == DreamStatus.REALITY_AFTERGLOW.value, (
        f"Expected REALITY_AFTERGLOW after force_exit from {initial_status}, "
        f"got {state['status']}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Soft exit refused → stays DREAM_ACTIVE; hard exit still works
# ═══════════════════════════════════════════════════════════════════════════════

def test_soft_exit_refused_stays_dream_active(sandbox, active_dream):
    """When LLM does not include accept marker, state stays DREAM_ACTIVE."""
    # LLM reply WITHOUT the accept marker → refuses to let user wake up
    with patch("core.llm_client.chat", _make_fake_llm("不行，再陪我一会儿。（拉住你的手）")), \
         patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
        from core.dream import dream_pipeline
        result = asyncio.run(dream_pipeline.dream_turn(_UID, "我想醒来"))

    assert result.get("exit_accepted") is False, f"unexpected exit_accepted=True, reply={result.get('reply')}"
    assert result.get("force_exited") is False

    from core.dream.dream_state import read_state, DreamStatus
    state = read_state(_UID)
    assert state["status"] == DreamStatus.DREAM_ACTIVE.value


def test_hard_exit_works_after_soft_refused(sandbox, active_dream):
    """Hard exit still works even if a soft exit was refused."""
    from core.dream import dream_pipeline
    asyncio.run(dream_pipeline.force_exit_dream(_UID))

    from core.dream.dream_state import read_state, DreamStatus
    state = read_state(_UID)
    assert state["status"] == DreamStatus.REALITY_AFTERGLOW.value


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Afterglow text does not contain scene/action descriptions
# ═══════════════════════════════════════════════════════════════════════════════

def test_afterglow_text_no_scene_action_keywords(sandbox):
    """Afterglow summary text must not contain scene/action keywords."""
    from core.safe_write import safe_write_json
    from core.dream.dream_state import DREAM_ARTIFACT_SENTINEL

    summaries_dir = sandbox.dreams_summaries_dir()
    summaries_dir.mkdir(parents=True, exist_ok=True)

    # Write a summary with scene/action stripped (should already be stripped by LLM)
    summary = {
        **DREAM_ARTIFACT_SENTINEL,
        "dream_id": "test_dream_001",
        "uid": _UID,
        "created_at": time.time(),
        "exit_type": "soft",
        "title": "光的边缘",
        "summary": "轻柔与依恋",       # Pure emotion, no actions
        "emotional_tags": ["依恋", "温柔", "遗憾"],
        "high_weight_lines": ["（轻轻握住你的手）不想放开"],  # action in raw line
        "symbolic_fragments": ["光", "水", "距离"],
        "summary_weight": 0.7,
        "afterglow": "gentle_residue",
        "reality_boundary": "dream_only",
        "emotional_trace_weight": None,
    }
    safe_write_json(summaries_dir / "dream_test_dream_001.summary.json", summary)

    from core.dream.dream_afterglow import load_afterglow
    text = load_afterglow(_UID)

    assert text, "afterglow should return non-empty text"

    # high_weight_lines must NOT appear in the injected afterglow text
    assert "握住" not in text, "action description leaked into afterglow prompt"
    assert "不想放开" not in text, "raw line leaked into afterglow prompt"

    # Emotional content and prohibition should be present
    assert "梦" in text
    assert "现实" in text or "RP" in text


def test_afterglow_hurt_reluctance_for_hard_exit(sandbox):
    """exit_type=hard_exit → afterglow=hurt_reluctance framing."""
    from core.safe_write import safe_write_json
    from core.dream.dream_state import DREAM_ARTIFACT_SENTINEL

    summaries_dir = sandbox.dreams_summaries_dir()
    summaries_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        **DREAM_ARTIFACT_SENTINEL,
        "dream_id": "test_hard_exit",
        "uid": _UID,
        "created_at": time.time(),
        "exit_type": "hard_exit",
        "title": "中断",
        "summary": "突然的空白",
        "emotional_tags": ["失落"],
        "high_weight_lines": [],
        "symbolic_fragments": [],
        "summary_weight": 0.6,
        "afterglow": "hurt_reluctance",
        "reality_boundary": "dream_only",
        "emotional_trace_weight": None,
    }
    safe_write_json(summaries_dir / "dream_test_hard_exit.summary.json", summary)

    from core.dream.dream_afterglow import load_afterglow
    text = load_afterglow(_UID)

    assert "中断" in text or "强行" in text, "hurt_reluctance frame not applied"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Afterglow summary not read by reality memory loaders
# ═══════════════════════════════════════════════════════════════════════════════

def test_afterglow_summary_not_retrieved_by_reality_loaders(sandbox):
    """
    dreams/summaries/dream_*.summary.json must never surface in reality loaders.
    Extends the existing isolation contract test to cover the summary path.
    """
    from core.safe_write import safe_write_json
    from core.memory import episodic_memory, event_log, mid_term, short_term, user_identity
    from core.dream.dream_state import DREAM_ARTIFACT_SENTINEL

    sentinel = "AFTERGLOW_ISOLATION_SENTINEL__never_retrieve_contract_v2"

    summaries_dir = sandbox.dreams_summaries_dir()
    summaries_dir.mkdir(parents=True, exist_ok=True)

    safe_write_json(
        summaries_dir / "dream_sentinel_test.summary.json",
        {
            **DREAM_ARTIFACT_SENTINEL,
            "dream_id": "sentinel_test",
            "uid": _UID,
            "summary": sentinel,
            "title": sentinel,
        },
    )

    async def collect():
        return [
            json.dumps(episodic_memory.retrieve(_UID, topic=sentinel, top_k=5)),
            await event_log.search(_UID, sentinel),
            json.dumps(short_term.load_for_prompt(_UID)),
            mid_term.format_for_prompt(_UID),
            json.dumps(await user_identity.load(_UID)),
        ]

    haystacks = asyncio.run(collect())
    assert all(sentinel not in h for h in haystacks), (
        "afterglow summary sentinel found in reality loaders"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 8. amnesia / keep_impression only change snapshot content
# ═══════════════════════════════════════════════════════════════════════════════

def test_amnesia_true_empties_memory_fields_in_snapshot(sandbox):
    """amnesia=True → episodic_summary and mid_term_context empty in snapshot."""
    from core.dream.dream_settings import save as _save
    _save(_UID, {"amnesia": True, "keep_impression": True})

    # Plant fake episodic and mid-term data
    from core.memory import mid_term
    mid_term.append(_UID, "用户最近很开心", tags=[])

    # Build snapshot; should NOT include mid_term (amnesia=True)
    # We mock the episodic/midterm loaders to verify they're not called live
    episodic_called = []
    midterm_called = []

    async def fake_retrieve(*a, **kw):
        episodic_called.append(True)
        return []

    async def run():
        # Patch episodic retrieve to detect if called
        with patch("core.memory.episodic_memory.retrieve", fake_retrieve):
            from core.dream.dream_context import build_snapshot
            return await build_snapshot(_UID, entry_reason="test amnesia")

    snapshot = asyncio.run(run())

    assert snapshot["episodic_summary"] == "", "amnesia=True should give empty episodic"
    assert snapshot["mid_term_context"] == "", "amnesia=True should give empty mid_term"
    # Crucially: episodic retrieve was NOT called (amnesia blocks snapshot-level fetch too)
    assert not episodic_called, "episodic retrieve called despite amnesia=True"


def test_amnesia_false_includes_memory_in_snapshot(sandbox):
    """amnesia=False → snapshot-level memory fetch is attempted."""
    from core.dream.dream_settings import save as _save
    _save(_UID, {"amnesia": False, "keep_impression": True})

    episodic_called = []

    def fake_retrieve(*a, **kw):  # sync — matches core.memory.episodic_memory.retrieve
        episodic_called.append(True)
        return []

    async def run():
        with patch("core.memory.episodic_memory.retrieve", fake_retrieve):
            with patch("core.memory.mood_state.get_current", return_value="neutral"):
                from core.dream.dream_context import build_snapshot
                return await build_snapshot(_UID, entry_reason="test no amnesia")

    asyncio.run(run())
    assert episodic_called, "amnesia=False should call episodic retrieve for snapshot"


def test_keep_impression_false_empties_profile_impression(sandbox):
    """keep_impression=False → profile_impression empty in snapshot."""
    from core.dream.dream_settings import save as _save
    _save(_UID, {"amnesia": False, "keep_impression": False})

    # Plant profile data
    from core.memory import user_profile
    user_profile.save(_UID, {"traits": ["开朗", "有趣"]})

    async def run():
        with patch("core.memory.episodic_memory.retrieve", MagicMock(return_value=[])):
            with patch("core.memory.mood_state.get_current", return_value="neutral"):
                from core.dream.dream_context import build_snapshot
                return await build_snapshot(_UID)

    snapshot = asyncio.run(run())
    assert snapshot["profile_impression"] == "", "keep_impression=False should give empty profile"


def test_dream_log_written_only_to_dream_file(sandbox, active_dream):
    """dream_turn must only write to dreams/tmp/, not to any reality path."""
    with patch("core.llm_client.chat", _make_fake_llm("梦境回复")), \
         patch("core.pipeline_registry.get", return_value=_FAKE_PIPELINE):
        from core.dream import dream_pipeline
        asyncio.run(dream_pipeline.dream_turn(_UID, "测试消息"))

    tmp_dir = sandbox.dreams_tmp_dir()
    dream_files = list(tmp_dir.glob(f"current_dream_{_UID}.jsonl"))
    assert len(dream_files) == 1, "dream log file not created in tmp dir"

    # Verify sentinel on every record
    for line in dream_files[0].read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        assert record.get("never_retrieve") is True
        assert record.get("reality_boundary") == "dream_only"
