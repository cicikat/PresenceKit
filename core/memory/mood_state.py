"""
mood_state — 角色情绪状态持久化。
scope = character only，无 uid 维度。
来源回合绑定的短时表达；读取不续期，旧枚举不作为当前信号。
"""
import json
import logging
import time
from pathlib import Path

from core.sandbox import get_paths
from core.safe_write import safe_write_json
from core.llm_output_validator import record_failure, is_paused, reset
from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)

# 情绪强度映射（用于漂移计算）
EMOTION_INTENSITY = {
    "neutral":   0.0,
    "gentle":    0.3,
    "thinking":  0.2,
    "happy":     0.6,
    "sad":       0.6,
    "surprised": 0.7,
    "angry":     0.8,
    "sleepy":    0.3,
    "yandere":   1.0,
}

TTL_SECONDS = 30 * 60

_DEFAULT = {"current": "", "previous": "", "intensity": 0.0, "updated_at": 0.0,
            "has_signal": False, "source_turn_id": "", "source_at": 0.0,
            "expires_at": 0.0, "version": 0, "schema_version": 2}


def _read_path(char_id: str = DEFAULT_CHAR_ID) -> Path:
    return get_paths().mood_state(char_id=char_id)


def _write_path(char_id: str = DEFAULT_CHAR_ID) -> Path:
    return get_paths().mood_state(char_id=char_id)


def load(*, char_id: str = DEFAULT_CHAR_ID) -> dict:
    try:
        state = json.loads(_read_path(char_id).read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("schema_version") != 2:
            return dict(_DEFAULT)
        now = time.time()
        if not (float(state.get("source_at") or 0) <= now < float(state.get("expires_at") or 0)):
            state = {**state, "current": "", "intensity": 0.0, "has_signal": False}
        return state
    except (OSError, ValueError, TypeError):
        return dict(_DEFAULT)


def save(state: dict, *, char_id: str = DEFAULT_CHAR_ID) -> None:
    if not safe_write_json(_write_path(char_id), state):
        raise OSError("expression persistence failed")


def update(new_emotion: str, new_intensity: float | None = None, source: str = "explicit",
           *, char_id: str = DEFAULT_CHAR_ID, force: bool = False,
           source_turn_id: str = "", source_at: float | None = None,
           ttl_seconds: float = TTL_SECONDS, retract: bool = False) -> dict:
    state = load(char_id=char_id)
    now = time.time()
    if source not in ("explicit", "perform") or not source_turn_id or source_at is None:
        return state
    occurred = float(source_at)
    if occurred > now or occurred < float(state.get("last_source_at") or 0):
        return state
    if occurred == float(state.get("last_source_at") or 0):
        return state
    # A no-signal turn advances the source watermark, but invents no neutral mood.
    state = {**state, "last_source_at": occurred, "last_turn_id": source_turn_id,
             "version": int(state.get("version") or 0) + 1, "schema_version": 2}
    if retract:
        state.update(current="", intensity=0.0, has_signal=False, expires_at=0.0)
    elif new_emotion in EMOTION_INTENSITY and occurred + min(TTL_SECONDS, max(0, ttl_seconds)) > now:
        state.update(previous=state.get("current", ""), current=new_emotion,
                     source_at=occurred, source_turn_id=source_turn_id,
                     intensity=max(0.0, min(1.0, float(new_intensity if new_intensity is not None else EMOTION_INTENSITY[new_emotion]))),
                     has_signal=True, updated_at=occurred,
                     expires_at=occurred + min(TTL_SECONDS, max(0, ttl_seconds)), source=source)
    save(state, char_id=char_id)
    return state


def get_current(*, char_id: str = DEFAULT_CHAR_ID) -> str:
    return load(char_id=char_id).get("current", "")


def get_intensity(*, char_id: str = DEFAULT_CHAR_ID) -> float:
    return load(char_id=char_id).get("intensity", 0.0)
