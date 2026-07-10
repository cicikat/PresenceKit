"""
tests/test_coplay_session.py — Brief 38 状态机单元测试

覆盖 core/coplay/session.py 的 off → armed → active → closing → armed 全周期，
以及非法转换的拒绝/幂等行为。
"""

from core.coplay.session import (
    CoplayStatus,
    arm,
    close_session,
    disarm,
    enter_active,
    enter_closing,
    is_active,
    is_armed,
    read_state,
)

UID = "u1"
CHAR = "yexuan"


def test_default_state_is_off(sandbox):
    state = read_state(UID, char_id=CHAR)
    assert state["status"] == CoplayStatus.OFF.value


def test_arm_from_off(sandbox):
    state = arm(UID, char_id=CHAR)
    assert state["status"] == CoplayStatus.ARMED.value
    assert read_state(UID, char_id=CHAR)["status"] == CoplayStatus.ARMED.value


def test_arm_is_idempotent_when_already_armed(sandbox):
    arm(UID, char_id=CHAR)
    state = arm(UID, char_id=CHAR)
    assert state["status"] == CoplayStatus.ARMED.value


def test_enter_active_requires_armed(sandbox):
    # OFF -> enter_active is rejected, stays OFF
    state = enter_active(UID, game_id="g1", game_name="Some Game", char_id=CHAR)
    assert state["status"] == CoplayStatus.OFF.value


def test_full_lifecycle_off_armed_active_closing_armed(sandbox):
    arm(UID, char_id=CHAR)
    state = enter_active(UID, game_id="g1", game_name="Some Game", char_id=CHAR)
    assert state["status"] == CoplayStatus.ACTIVE.value
    assert state["game_id"] == "g1"
    assert is_active(UID, char_id=CHAR)

    state = enter_closing(UID, char_id=CHAR)
    assert state["status"] == CoplayStatus.CLOSING.value
    assert not is_active(UID, char_id=CHAR)
    assert is_armed(UID, char_id=CHAR)  # closing still counts as "watcher should keep polling"

    state = close_session(UID, char_id=CHAR)
    assert state["status"] == CoplayStatus.ARMED.value
    # game_id/game_name are dropped once the session fully closes
    assert "game_id" not in state


def test_enter_active_idempotent_same_game(sandbox):
    arm(UID, char_id=CHAR)
    enter_active(UID, game_id="g1", game_name="Some Game", char_id=CHAR)
    state = enter_active(UID, game_id="g1", game_name="Some Game", char_id=CHAR)
    assert state["status"] == CoplayStatus.ACTIVE.value
    assert state["game_id"] == "g1"


def test_enter_closing_requires_active(sandbox):
    arm(UID, char_id=CHAR)
    state = enter_closing(UID, char_id=CHAR)  # still armed, not active
    assert state["status"] == CoplayStatus.ARMED.value


def test_disarm_hard_stops_from_any_state(sandbox):
    arm(UID, char_id=CHAR)
    enter_active(UID, game_id="g1", game_name="Some Game", char_id=CHAR)
    state = disarm(UID, char_id=CHAR)
    assert state["status"] == CoplayStatus.OFF.value
    assert not is_armed(UID, char_id=CHAR)


def test_char_id_isolation(sandbox):
    arm(UID, char_id="char_a")
    assert read_state(UID, char_id="char_a")["status"] == CoplayStatus.ARMED.value
    assert read_state(UID, char_id="char_b")["status"] == CoplayStatus.OFF.value
