"""
tests/test_coplay_commentator.py — Brief 41: 主动开口 D5 静默规则 + proposer 编排。
"""

import time
from unittest.mock import AsyncMock, patch

import pytest

from core.coplay import commentator, observer, session
from core.coplay.observer import GameMoment

UID = "owner1"
CHAR = "yexuan"


@pytest.fixture(autouse=True)
def _reset_state():
    observer.clear_moments_for_test()
    yield
    observer.clear_moments_for_test()


@pytest.fixture(autouse=True)
def _owner_uid():
    import core.config_loader as cl
    original = cl.get_config().get("scheduler")
    cl.get_config()["scheduler"] = {**(original or {}), "owner_id": UID}
    yield
    if original is not None:
        cl.get_config()["scheduler"] = original


# ═══════════════════════════════════════════════════════════════════════════
# _pick_moment
# ═══════════════════════════════════════════════════════════════════════════

def test_pick_moment_none_when_empty():
    assert commentator._pick_moment(UID) is None


def test_pick_moment_discards_combat_start():
    observer.push_moment(UID, GameMoment(kind="combat_start", summary="进入战斗"))
    assert commentator._pick_moment(UID) is None


def test_pick_moment_ignores_stale_moments():
    stale = GameMoment(kind="death", summary="很久以前死过一次")
    stale.ts = time.time() - commentator.MOMENT_FRESHNESS_SECONDS - 100
    observer.push_moment(UID, stale)
    assert commentator._pick_moment(UID) is None


def test_pick_moment_prioritizes_death_over_idle():
    observer.push_moment(UID, GameMoment(kind="idle", summary="没什么变化"))
    observer.push_moment(UID, GameMoment(kind="death", summary="死了"))
    picked = commentator._pick_moment(UID)
    assert picked.kind == "death"


def test_pick_moment_allows_combat_end():
    observer.push_moment(UID, GameMoment(kind="combat_end", summary="打完了"))
    picked = commentator._pick_moment(UID)
    assert picked.kind == "combat_end"


# ═══════════════════════════════════════════════════════════════════════════
# propose_coplay_commentary
# ═══════════════════════════════════════════════════════════════════════════

def test_propose_none_when_not_active(sandbox):
    observer.push_moment(UID, GameMoment(kind="death", summary="死了"))
    assert commentator.propose_coplay_commentary() is None


def test_propose_none_when_active_but_no_moments(sandbox):
    session.arm(UID, char_id=CHAR)
    session.enter_active(UID, game_id="g1", game_name="Some Game", char_id=CHAR)
    assert commentator.propose_coplay_commentary() is None


def test_propose_none_when_only_discard_kind(sandbox):
    session.arm(UID, char_id=CHAR)
    session.enter_active(UID, game_id="g1", game_name="Some Game", char_id=CHAR)
    observer.push_moment(UID, GameMoment(kind="combat_start", summary="进入战斗"))
    assert commentator.propose_coplay_commentary() is None


def test_propose_returns_proposal_when_active_with_moment(sandbox):
    session.arm(UID, char_id=CHAR)
    session.enter_active(UID, game_id="g1", game_name="Some Game", char_id=CHAR)
    observer.push_moment(UID, GameMoment(kind="idle", summary="停下来了"))

    proposal = commentator.propose_coplay_commentary()
    assert proposal is not None
    assert proposal.trigger_name == "coplay_commentary"
    from core.scheduler.state_machine import TriggerState
    assert proposal.requires_state == [TriggerState.QUIET]
    assert proposal.bypass_state_machine is False


def test_propose_adds_highlight_for_death(sandbox):
    from core.coplay.game_state import read_game_state

    session.arm(UID, char_id=CHAR)
    session.enter_active(UID, game_id="g1", game_name="Some Game", char_id=CHAR)
    observer.push_moment(UID, GameMoment(kind="death", summary="被BOSS秒了"))

    commentator.propose_coplay_commentary()

    state = read_game_state(UID, "g1", char_id=CHAR)
    assert any(h["summary"] == "被BOSS秒了" for h in state["highlights"])


def test_propose_does_not_add_highlight_for_idle(sandbox):
    from core.coplay.game_state import read_game_state

    session.arm(UID, char_id=CHAR)
    session.enter_active(UID, game_id="g1", game_name="Some Game", char_id=CHAR)
    observer.push_moment(UID, GameMoment(kind="idle", summary="没什么变化"))

    commentator.propose_coplay_commentary()

    state = read_game_state(UID, "g1", char_id=CHAR)
    assert state["highlights"] == []


@pytest.mark.asyncio
async def test_execute_calls_execute_prompt_with_expected_args(sandbox):
    session.arm(UID, char_id=CHAR)
    session.enter_active(UID, game_id="g1", game_name="Some Game", char_id=CHAR)
    observer.push_moment(UID, GameMoment(kind="achievement", summary="解锁了成就A"))

    proposal = commentator.propose_coplay_commentary()
    assert proposal is not None

    fake_result = object()
    with patch(
        "core.scheduler.execution.execute_prompt", new=AsyncMock(return_value=fake_result),
    ) as mock_exec:
        result = await proposal.execute(dry_run=True)

    assert result is fake_result
    _, kwargs = mock_exec.call_args
    assert kwargs["trigger_name"] == "coplay_commentary"
    assert kwargs["would_mark"] == ["coplay_commentary"]
    assert kwargs["recall_policy"] == "none"
    assert kwargs["dry_run"] is True
    assert "解锁了成就A" in kwargs["prompt_factory"]()
