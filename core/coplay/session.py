"""
core/coplay/session.py — CoplaySession 状态机: off → armed → active → closing → armed

参考 core/dream/dream_state.py 的实现风格：文件即状态，read_state/write_state
经 safe_write_json 落盘，get_paths().coplay_state_path() 做用户/角色双重隔离。
不用内存锁做"线程安全"——多进程也要安全，只有文件级 safe_write_json 靠得住。

状态语义：
  off      — 陪玩模式未开启（默认）
  armed    — 用户开了陪玩模式，watcher 正在轮询，但还没检测到游戏
  active   — 检测到游戏进程，正在陪玩
  closing  — 游戏退出，收尾链（Brief 42）执行中

closing 收尾完成后回到 armed（继续等下一局），不回到 off——off 只由用户显式
disarm 触达。这样 Brief 39 的 watcher 可以在同一次"开启陪玩模式"里跨多局游戏
持续工作，不需要每局结束后手动重新开启。
"""

import logging
from enum import Enum
from typing import Any

from core.data_paths import DEFAULT_CHAR_ID
from core.safe_write import safe_write_json
from core.sandbox import get_paths, safe_user_id

logger = logging.getLogger(__name__)


class CoplayStatus(str, Enum):
    OFF = "off"
    ARMED = "armed"
    ACTIVE = "active"
    CLOSING = "closing"


_VALID_STATUSES: frozenset[str] = frozenset(s.value for s in CoplayStatus)


def default_state(user_id: str | int) -> dict[str, Any]:
    return {
        "user_id": safe_user_id(user_id),
        "status": CoplayStatus.OFF.value,
    }


def read_state(user_id: str | int, *, char_id: str = DEFAULT_CHAR_ID) -> dict[str, Any]:
    path = get_paths().coplay_state_path(user_id, char_id=char_id)
    if not path.exists():
        return default_state(user_id)

    try:
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[coplay_state] read failed uid={user_id}: {e}")
        return default_state(user_id)

    if not isinstance(data, dict) or data.get("status") not in _VALID_STATUSES:
        logger.warning(f"[coplay_state] invalid state shape/status uid={user_id}")
        return default_state(user_id)

    data.setdefault("user_id", safe_user_id(user_id))
    return data


def write_state(user_id: str | int, state: dict[str, Any], *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    if not isinstance(state, dict):
        raise TypeError("coplay state must be a dict")

    status = state.get("status")
    if isinstance(status, CoplayStatus):
        state = {**state, "status": status.value}
        status = state["status"]
    if status not in _VALID_STATUSES:
        raise ValueError(f"unknown coplay status: {status!r}")

    payload = {**state, "user_id": safe_user_id(user_id)}
    path = get_paths().coplay_state_path(user_id, char_id=char_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return safe_write_json(path, payload)


def is_active(user_id: str | int, *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    """True 当且仅当 status == active。用于 coplay_echo 每轮判定（Brief 38 §四）。"""
    return read_state(user_id, char_id=char_id).get("status") == CoplayStatus.ACTIVE.value


def is_armed(user_id: str | int, *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    """True 当 status in {armed, active, closing} —— watcher 应该继续轮询的范围。"""
    return read_state(user_id, char_id=char_id).get("status") in (
        CoplayStatus.ARMED.value, CoplayStatus.ACTIVE.value, CoplayStatus.CLOSING.value,
    )


def arm(user_id: str | int, *, char_id: str = DEFAULT_CHAR_ID) -> dict[str, Any]:
    """off → armed。已经 armed/active/closing 时幂等，原样返回（不打断正在进行的对局）。"""
    state = read_state(user_id, char_id=char_id)
    if state.get("status") != CoplayStatus.OFF.value:
        logger.info("[coplay_session] arm() no-op, already status=%s uid=%s", state.get("status"), user_id)
        return state
    state["status"] = CoplayStatus.ARMED.value
    write_state(user_id, state, char_id=char_id)
    logger.info("[coplay_session] armed uid=%s", user_id)
    return state


def disarm(user_id: str | int, *, char_id: str = DEFAULT_CHAR_ID) -> dict[str, Any]:
    """硬关闭 —— 任意状态 → off。总是成功（用户随时能关掉陪玩模式）。"""
    state = read_state(user_id, char_id=char_id)
    prev_status = state.get("status")
    game_id = state.get("game_id")
    state = default_state(user_id)
    write_state(user_id, state, char_id=char_id)
    logger.info("[coplay_session] disarmed uid=%s (was %s, game_id=%s)", user_id, prev_status, game_id)
    return state


def enter_active(user_id: str | int, *, game_id: str, game_name: str, char_id: str = DEFAULT_CHAR_ID) -> dict[str, Any]:
    """armed → active（watcher 检测到游戏时调用）。已在 active 同一 game_id 时幂等。"""
    state = read_state(user_id, char_id=char_id)
    status = state.get("status")
    if status == CoplayStatus.ACTIVE.value and state.get("game_id") == game_id:
        return state
    if status != CoplayStatus.ARMED.value:
        logger.warning(
            "[coplay_session] enter_active() rejected, status=%s (need armed) uid=%s", status, user_id,
        )
        return state
    state["status"] = CoplayStatus.ACTIVE.value
    state["game_id"] = game_id
    state["game_name"] = game_name
    write_state(user_id, state, char_id=char_id)
    logger.info("[coplay_session] active uid=%s game_id=%s game_name=%s", user_id, game_id, game_name)
    return state


def enter_closing(user_id: str | int, *, char_id: str = DEFAULT_CHAR_ID) -> dict[str, Any]:
    """active → closing（watcher 检测到游戏退出时调用）。"""
    state = read_state(user_id, char_id=char_id)
    if state.get("status") != CoplayStatus.ACTIVE.value:
        logger.warning(
            "[coplay_session] enter_closing() rejected, status=%s (need active) uid=%s",
            state.get("status"), user_id,
        )
        return state
    state["status"] = CoplayStatus.CLOSING.value
    write_state(user_id, state, char_id=char_id)
    logger.info("[coplay_session] closing uid=%s game_id=%s", user_id, state.get("game_id"))
    return state


def close_session(user_id: str | int, *, char_id: str = DEFAULT_CHAR_ID) -> dict[str, Any]:
    """closing → armed（Brief 42 收尾链跑完后调用，回到 armed 继续等下一局）。"""
    state = read_state(user_id, char_id=char_id)
    if state.get("status") != CoplayStatus.CLOSING.value:
        logger.warning(
            "[coplay_session] close_session() rejected, status=%s (need closing) uid=%s",
            state.get("status"), user_id,
        )
        return state
    state = {"user_id": safe_user_id(user_id), "status": CoplayStatus.ARMED.value}
    write_state(user_id, state, char_id=char_id)
    logger.info("[coplay_session] closed → armed uid=%s", user_id)
    return state
