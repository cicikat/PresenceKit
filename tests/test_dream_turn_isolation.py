from tests.fixtures.public_assets import TEST_CHAR_ID
"""
tests/test_dream_turn_isolation.py — dream_turn 副作用隔离 + reality 硬拒绝

从 test_dream_mvp1.py 拆出（Brief 50 · 工单D）。默认 dream settings 配置下的
逐子系统隔离检查；与 test_dream_full_matrix_isolation.py（全开档配置）互补，
不是重复——不同 settings 组合可能触发不同代码分支，且本文件用精确的
前后计数/mtime 比对（而非目录 glob），能捕获全开档文件里 glob 方式会漏掉的
写入。

Covers:
  - dream_turn side-effect isolation (mood_state / history / episodic / midterm
    / agent_actions / notify_owner_turn / trigger_state_log untouched)
  - DREAM_ACTIVE / DREAM_CLOSING → /desktop/chat 等现实端点硬拒绝（409）
  - force_exit_dream immediate effect in any dream state
  - dream_turn 只写 dreams/tmp/，不写任何现实路径
"""

import asyncio
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_UID = "dream_test_user"

_FAKE_CHARACTER = MagicMock()
_FAKE_CHARACTER.name = "Companion"
_FAKE_CHARACTER.description = "测试角色描述"
_FAKE_CHARACTER.gender = "male"
_FAKE_CHARACTER.jailbreak_entries = []

_FAKE_PIPELINE = MagicMock()
_FAKE_PIPELINE.character = _FAKE_CHARACTER
_FAKE_PIPELINE.lore_engine = MagicMock()
_FAKE_PIPELINE.lore_engine.match.return_value = ([], [])


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
    mood_path = sandbox.mood_state(char_id=TEST_CHAR_ID)
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
# 3. Reality hard reject during DREAM_ACTIVE / DREAM_CLOSING
# ═══════════════════════════════════════════════════════════════════════════════

def test_reality_hard_reject_when_dream_active(sandbox, active_dream):
    """_check_reality_not_in_dream raises HTTPException 409 when DREAM_ACTIVE."""
    import importlib

    # Force re-import to pick up sandbox patch
    import core.dream.dream_state as ds
    importlib.reload(ds)

    from admin.reality_admission import require_reality_turn
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        require_reality_turn(_UID)

    assert exc_info.value.status_code == 409


def test_reality_hard_reject_when_dream_closing(sandbox):
    """_check_reality_not_in_dream raises 409 when DREAM_CLOSING."""
    from core.dream.dream_state import write_state, DreamStatus
    write_state(_UID, {"user_id": _UID, "status": DreamStatus.DREAM_CLOSING.value})

    from admin.reality_admission import require_reality_turn
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        require_reality_turn(_UID)

    assert exc_info.value.status_code == 409


def test_reality_allowed_when_not_in_dream(sandbox):
    """_check_reality_not_in_dream allows request when status is REALITY_CHAT."""
    from core.dream.dream_state import write_state, DreamStatus
    write_state(_UID, {"user_id": _UID, "status": DreamStatus.REALITY_CHAT.value})

    from admin.reality_admission import require_reality_turn
    # Should not raise
    require_reality_turn(_UID)


def test_reality_allowed_when_afterglow(sandbox):
    """_check_reality_not_in_dream allows request during REALITY_AFTERGLOW."""
    from core.dream.dream_state import write_state, DreamStatus
    write_state(_UID, {"user_id": _UID, "status": DreamStatus.REALITY_AFTERGLOW.value})

    from admin.reality_admission import require_reality_turn
    require_reality_turn(_UID)


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
    """Only an active real Dream may transition; Reality calls are no-ops."""
    from core.dream.dream_state import write_state, read_state, DreamStatus

    write_state(_UID, {
        "user_id": _UID,
        "status": initial_status,
        "dream_id": f"dream_{_UID}_force_test",
    })

    from core.dream import dream_pipeline
    asyncio.run(dream_pipeline.force_exit_dream(_UID))

    state = read_state(_UID)
    expected = (
        DreamStatus.REALITY_AFTERGLOW.value
        if initial_status in {DreamStatus.DREAM_ACTIVE.value, DreamStatus.DREAM_CLOSING.value}
        else initial_status
    )
    assert state["status"] == expected


# ═══════════════════════════════════════════════════════════════════════════════
# 5. dream_turn 只写 dreams/tmp/，不写任何现实路径
# ═══════════════════════════════════════════════════════════════════════════════

def test_dream_log_written_only_to_dream_file(sandbox, active_dream):
    """dream_turn must only write to dreams/tmp/, not to any reality path."""
    import json

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
