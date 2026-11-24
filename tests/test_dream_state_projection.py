from tests.fixtures.runtime_capabilities import read_registered_character
from tests.fixtures.public_assets import TEST_CHAR_ID
"""
tests/test_dream_state_projection.py — Brief 94 §2: GET /dream/state 结构化状态透出

desktop 此前把非 REALITY_CHAT 状态一律显示成「正在做梦，无法聊天」，但只有
DREAM_ACTIVE / DREAM_CLOSING 才真正阻塞现实聊天（get_reality_guard_status 定义）；
REALITY_AFTERGLOW（梦后余韵）期间聊天完全可用。本文件验证新增的
dream_state / since / expected_end / blocks_chat / stuck 五个投影字段与真实
聊天可用性（blocks_chat）严格一致，且 cooldown 会在 TTL 后正确降级为 idle
（证明状态字段自身永不回跳 REALITY_CHAT 这个已知边界被正确补偿）。

★ 每个"X不阻塞"断言都配对"X阻塞"的正样本，防止空断言伪装成验证。
"""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

_UID = "dream_proj_test"


def _get():
    from admin.routers.dream import dream_state_get
    with patch('admin.routers.dream_common._owner_uid', return_value=_UID):
        return asyncio.run(dream_state_get(read_character=read_registered_character))


# ═══════════════════════════════════════════════════════════════════════════════
# idle
# ═══════════════════════════════════════════════════════════════════════════════

def test_idle_no_dream_state_file(sandbox):
    """No dream_state.json at all → idle, nothing to time, chat not blocked."""
    result = _get()
    assert result["dream_state"] == "idle"
    assert result["since"] is None
    assert result["expected_end"] is None
    assert result["blocks_chat"] is False
    assert result["stuck"] is False


def test_idle_explicit_reality_chat(sandbox):
    from core.dream.dream_state import write_state, DreamStatus
    write_state(_UID, {"user_id": _UID, "status": DreamStatus.REALITY_CHAT.value})
    result = _get()
    assert result["dream_state"] == "idle"
    assert result["blocks_chat"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# dreaming — DREAM_ACTIVE / DREAM_CLOSING really do block reality chat
# ═══════════════════════════════════════════════════════════════════════════════

def test_dreaming_active_blocks_chat_and_reports_since(sandbox):
    from core.dream.dream_state import write_state, DreamStatus
    started = time.time() - 120  # 2 minutes into the dream
    write_state(_UID, {
        "user_id": _UID,
        "status": DreamStatus.DREAM_ACTIVE.value,
        "dream_id": f"dream_{_UID}_x",
        "dream_started_at": started,
    })
    result = _get()
    assert result["dream_state"] == "dreaming"
    assert result["since"] == pytest.approx(started)
    assert result["expected_end"] is None, "dream length is never predictable"
    assert result["blocks_chat"] is True, "DREAM_ACTIVE must hard-block reality chat"
    assert result["stuck"] is False


def test_dreaming_closing_also_blocks_chat(sandbox):
    """Positive control: DREAM_CLOSING is bucketed as dreaming too and still blocks."""
    from core.dream.dream_state import write_state, DreamStatus
    write_state(_UID, {
        "user_id": _UID,
        "status": DreamStatus.DREAM_CLOSING.value,
        "dream_id": f"dream_{_UID}_x",
        "dream_started_at": time.time() - 30,
    })
    result = _get()
    assert result["dream_state"] == "dreaming"
    assert result["blocks_chat"] is True


def test_dreaming_stuck_flag_past_threshold(sandbox):
    from core.dream.dream_state import write_state, DreamStatus, DREAM_STUCK_THRESHOLD_SECONDS
    started = time.time() - (DREAM_STUCK_THRESHOLD_SECONDS + 60)
    write_state(_UID, {
        "user_id": _UID,
        "status": DreamStatus.DREAM_ACTIVE.value,
        "dream_id": f"dream_{_UID}_stuck",
        "dream_started_at": started,
    })
    result = _get()
    assert result["dream_state"] == "dreaming"
    assert result["stuck"] is True


def test_dreaming_not_stuck_well_within_threshold(sandbox):
    """Positive control for the stuck flag: a fresh dream is not flagged stuck."""
    from core.dream.dream_state import write_state, DreamStatus
    write_state(_UID, {
        "user_id": _UID,
        "status": DreamStatus.DREAM_ACTIVE.value,
        "dream_id": f"dream_{_UID}_fresh",
        "dream_started_at": time.time(),
    })
    result = _get()
    assert result["stuck"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# cooldown — REALITY_AFTERGLOW does NOT block chat (the bug this brief fixes)
# ═══════════════════════════════════════════════════════════════════════════════

def test_cooldown_within_window_does_not_block_chat(sandbox):
    from core.dream.dream_state import write_state, DreamStatus, DREAM_COOLDOWN_SECONDS
    exited = time.time() - 60
    write_state(_UID, {
        "user_id": _UID,
        "status": DreamStatus.REALITY_AFTERGLOW.value,
        "last_exited_at": exited,
    })
    result = _get()
    assert result["dream_state"] == "cooldown"
    assert result["since"] == pytest.approx(exited)
    assert result["expected_end"] == pytest.approx(exited + DREAM_COOLDOWN_SECONDS)
    assert result["blocks_chat"] is False, (
        "REALITY_AFTERGLOW must NOT block reality chat — this is the exact bug "
        "desktop's blanket '正在做梦无法聊天' message misrepresented"
    )


def test_cooldown_expired_degrades_to_idle(sandbox):
    """
    ★ Known boundary compensation: the raw `status` field never auto-transitions
    back to REALITY_CHAT (docs/dream.md §六/§七). Without this degrade, `dream_state`
    would report "cooldown" forever after one dream. Verify it correctly falls
    back to idle once DREAM_COOLDOWN_SECONDS has elapsed.
    """
    from core.dream.dream_state import write_state, DreamStatus, DREAM_COOLDOWN_SECONDS
    write_state(_UID, {
        "user_id": _UID,
        "status": DreamStatus.REALITY_AFTERGLOW.value,
        "last_exited_at": time.time() - (DREAM_COOLDOWN_SECONDS + 3600),
    })
    result = _get()
    assert result["dream_state"] == "idle"
    assert result["blocks_chat"] is False
    # raw status is unaffected by this projection — only the derived field degrades
    assert result["status"] == "REALITY_AFTERGLOW"


# ═══════════════════════════════════════════════════════════════════════════════
# End-to-end smoke: idle → dreaming → cooldown via the real enter/close pipeline
# (mirrors the brief's manual acceptance check, driven in-process)
# ═══════════════════════════════════════════════════════════════════════════════

def test_full_transition_idle_dreaming_cooldown(sandbox):
    from core.dream.dream_pipeline import enter_dream, force_exit_dream

    uid = _UID + "_e2e"
    with patch('admin.routers.dream_common._owner_uid', return_value=uid):
        from admin.routers.dream import dream_state_get

        pre = asyncio.run(dream_state_get(read_character=read_registered_character))
        assert pre["dream_state"] == "idle"
        assert pre["blocks_chat"] is False

        with patch("core.dream.dream_context.build_snapshot", new=AsyncMock(return_value={
            "created_at": time.time(),
            "user_id": uid,
            "yexuan_awareness": "lucid_shared",
            "boundary": "dream_only",
            "entry_reason": "",
            "relationship_state": {},
            "recent_reality_context": "",
            "episodic_summary": "",
            "mid_term_context": "",
            "profile_impression": "",
        })):
            enter_result = asyncio.run(enter_dream(uid, char_id=TEST_CHAR_ID))
        assert enter_result.get("ok") is True

        mid = asyncio.run(dream_state_get(read_character=read_registered_character))
        assert mid["dream_state"] == "dreaming"
        assert mid["blocks_chat"] is True
        assert mid["since"] is not None

        with patch("core.dream.dream_log.archive_current"), \
             patch("core.dream.dream_hud.delete_hud_state"), \
             patch("asyncio.create_task") as mock_ct:
            asyncio.run(force_exit_dream(uid))
            # drain the background summary task synchronously so no warnings leak
            if mock_ct.call_args:
                coro = mock_ct.call_args[0][0]
                coro.close()

        post = asyncio.run(dream_state_get(read_character=read_registered_character))
        assert post["dream_state"] == "cooldown"
        assert post["blocks_chat"] is False
        assert post["since"] is not None
        assert post["expected_end"] is not None
