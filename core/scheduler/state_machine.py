"""
调度器触发状态机。

Phase 2 Step 1 只观测，不干预现有触发器发送路径。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from threading import RLock

from core.safe_write import safe_append_jsonl, safe_write_json
from core.sandbox import get_paths

logger = logging.getLogger(__name__)

# ── CHATTING → QUIET：Step 6 前先集中硬编码，之后迁到 policy.yaml ───────────
CHAT_TO_QUIET_BASE_SECONDS = 5 * 60
HIGH_EMOTION_INTENSITY = 0.7
MEDIUM_EMOTION_INTENSITY = 0.4

# ── QUIET ↔ RESTLESS：sensor 事件率滞后参数，Step 6 迁到 policy.yaml ─────────
QUIET_TO_ACTIVE_EVENTS = 5
QUIET_TO_ACTIVE_WINDOW_SECONDS = 3 * 60
QUIET_TO_ACTIVE_PERSIST_SECONDS = 60
ACTIVE_TO_QUIET_BASE_SECONDS = 10 * 60
ACTIVE_TO_QUIET_AFTER_RECENT_CHAT_SECONDS = 5 * 60
RECENT_CHAT_WINDOW_SECONDS = 30 * 60


def _now() -> float:
    return time.time()


class TriggerState(str, Enum):
    CHATTING = "CHATTING"
    QUIET = "QUIET"
    RESTLESS = "RESTLESS"


@dataclass
class _UserTriggerState:
    state: TriggerState = TriggerState.QUIET
    since_ts: float = 0.0
    last_owner_turn_ts: float = 0.0
    session_turn_count: int = 0
    sensor_event_times: list[float] = field(default_factory=list)
    restless_candidate_since_ts: float | None = None
    last_sensor_event_ts: float = 0.0

    def to_json(self) -> dict:
        return {
            "state": self.state.value,
            "since_ts": self.since_ts,
            "last_owner_turn_ts": self.last_owner_turn_ts,
            "session_turn_count": self.session_turn_count,
            "sensor_event_times": self.sensor_event_times,
            "restless_candidate_since_ts": self.restless_candidate_since_ts,
            "last_sensor_event_ts": self.last_sensor_event_ts,
        }

    @classmethod
    def from_json(cls, raw: dict) -> "_UserTriggerState":
        try:
            state = TriggerState(raw.get("state", TriggerState.QUIET.value))
        except ValueError:
            state = TriggerState.QUIET
        return cls(
            state=state,
            since_ts=float(raw.get("since_ts") or 0.0),
            last_owner_turn_ts=float(raw.get("last_owner_turn_ts") or 0.0),
            session_turn_count=int(raw.get("session_turn_count") or 0),
            sensor_event_times=[float(x) for x in raw.get("sensor_event_times", [])],
            restless_candidate_since_ts=(
                float(raw["restless_candidate_since_ts"])
                if raw.get("restless_candidate_since_ts") is not None
                else None
            ),
            last_sensor_event_ts=float(raw.get("last_sensor_event_ts") or 0.0),
        )


def _duration_factor(turn_count: int) -> float:
    if turn_count <= 3:
        return 0.6
    if turn_count <= 10:
        return 1.0
    if turn_count <= 20:
        return 1.4
    return 1.8


def _emotion_factor(intensity: float) -> float:
    if intensity >= HIGH_EMOTION_INTENSITY:
        return 0.5
    if intensity >= MEDIUM_EMOTION_INTENSITY:
        return 0.8
    return 1.0


def calculate_final_delay_seconds(session_turn_count: int, intensity: float | None = None) -> float:
    """计算 CHATTING → QUIET 的动态滞后秒数，供状态机和单测共用。"""
    if intensity is None:
        try:
            from core.memory.mood_state import get_intensity

            intensity = float(get_intensity())
        except Exception:
            intensity = 0.0
    return CHAT_TO_QUIET_BASE_SECONDS * _duration_factor(session_turn_count) * _emotion_factor(float(intensity))


class TriggerStateMachine:
    def __init__(self) -> None:
        self._lock = RLock()
        self._states: dict[str, _UserTriggerState] = {}
        self._load()

    def notify_owner_turn(self, uid: str) -> None:
        uid = str(uid or "").strip()
        if not uid:
            return
        now = _now()
        with self._lock:
            item = self._ensure(uid, now)
            old = item.state
            item.state = TriggerState.CHATTING
            item.last_owner_turn_ts = now
            item.session_turn_count += 1
            item.restless_candidate_since_ts = None
            if old != TriggerState.CHATTING:
                item.since_ts = now
                self._append_transition(uid, old, item.state, "owner_turn", now)
            self._persist()

    def feed_sensor_tick(self, uid: str, event_count: int) -> None:
        uid = str(uid or "").strip()
        if not uid:
            return
        event_count = max(0, int(event_count or 0))
        now = _now()
        with self._lock:
            item = self._ensure(uid, now)
            self._maybe_chatting_to_quiet(uid, item, now)
            if event_count > 0:
                item.last_sensor_event_ts = now
                item.sensor_event_times.extend([now] * event_count)
            cutoff = now - QUIET_TO_ACTIVE_WINDOW_SECONDS
            item.sensor_event_times = [ts for ts in item.sensor_event_times if ts >= cutoff]

            if item.state == TriggerState.QUIET:
                self._maybe_quiet_to_restless(uid, item, now)
            elif item.state == TriggerState.RESTLESS:
                self._maybe_restless_to_quiet(uid, item, now)
            self._persist()

    def get_state(self, uid: str) -> TriggerState:
        uid = str(uid or "").strip()
        if not uid:
            return TriggerState.QUIET
        now = _now()
        with self._lock:
            item = self._ensure(uid, now)
            self._maybe_chatting_to_quiet(uid, item, now)
            self._persist()
            return item.state

    def snapshot(self, uid: str) -> dict:
        uid = str(uid or "").strip()
        now = _now()
        with self._lock:
            item = self._ensure(uid, now)
            return item.to_json()

    def _ensure(self, uid: str, now: float) -> _UserTriggerState:
        if uid not in self._states:
            self._states[uid] = _UserTriggerState(since_ts=now)
        return self._states[uid]

    def _maybe_chatting_to_quiet(self, uid: str, item: _UserTriggerState, now: float) -> None:
        if item.state != TriggerState.CHATTING:
            return
        last_turn = item.last_owner_turn_ts or item.since_ts
        if now - last_turn < calculate_final_delay_seconds(item.session_turn_count):
            return
        old = item.state
        item.state = TriggerState.QUIET
        item.since_ts = now
        item.session_turn_count = 0
        item.restless_candidate_since_ts = None
        self._append_transition(uid, old, item.state, "chatting_final_delay_elapsed", now)

    def _maybe_quiet_to_restless(self, uid: str, item: _UserTriggerState, now: float) -> None:
        if len(item.sensor_event_times) < QUIET_TO_ACTIVE_EVENTS:
            item.restless_candidate_since_ts = None
            return
        if item.restless_candidate_since_ts is None:
            item.restless_candidate_since_ts = now
            return
        if now - item.restless_candidate_since_ts < QUIET_TO_ACTIVE_PERSIST_SECONDS:
            return
        old = item.state
        item.state = TriggerState.RESTLESS
        item.since_ts = now
        item.restless_candidate_since_ts = None
        self._append_transition(uid, old, item.state, "sensor_events_persisted", now)

    def _maybe_restless_to_quiet(self, uid: str, item: _UserTriggerState, now: float) -> None:
        recent_chat = item.last_owner_turn_ts and now - item.last_owner_turn_ts < RECENT_CHAT_WINDOW_SECONDS
        threshold = ACTIVE_TO_QUIET_AFTER_RECENT_CHAT_SECONDS if recent_chat else ACTIVE_TO_QUIET_BASE_SECONDS
        last_sensor = item.last_sensor_event_ts or item.since_ts
        if now - last_sensor < threshold:
            return
        old = item.state
        item.state = TriggerState.QUIET
        item.since_ts = now
        item.sensor_event_times = []
        item.restless_candidate_since_ts = None
        self._append_transition(uid, old, item.state, "sensor_silent", now)

    def _load(self) -> None:
        try:
            p = get_paths().scheduler_state()
            if not p.exists():
                return
            raw = json.loads(p.read_text(encoding="utf-8"))
            block = raw.get("trigger_state", {})
            if not isinstance(block, dict):
                return
            self._states = {
                str(uid): _UserTriggerState.from_json(value)
                for uid, value in block.items()
                if isinstance(value, dict)
            }
        except Exception as e:
            logger.warning("[trigger_state] 状态读取失败: %s", e)

    def _persist(self) -> None:
        try:
            p = get_paths().scheduler_state()
            existing = {}
            if p.exists():
                existing = json.loads(p.read_text(encoding="utf-8"))
            existing["trigger_state"] = {uid: item.to_json() for uid, item in self._states.items()}
            safe_write_json(p, existing)
        except Exception as e:
            logger.warning("[trigger_state] 状态写入失败: %s", e)

    def _append_transition(
        self,
        uid: str,
        from_state: TriggerState,
        to_state: TriggerState,
        reason: str,
        ts: float,
    ) -> None:
        safe_append_jsonl(
            get_paths().trigger_state_log(),
            {
                "ts": ts,
                "uid": uid,
                "from": from_state.value,
                "to": to_state.value,
                "reason": reason,
            },
        )


_machine = TriggerStateMachine()


def notify_owner_turn(uid: str) -> None:
    _machine.notify_owner_turn(uid)


def feed_sensor_tick(uid: str, event_count: int) -> None:
    _machine.feed_sensor_tick(uid, event_count)


def get_state(uid: str) -> TriggerState:
    return _machine.get_state(uid)


def snapshot(uid: str) -> dict:
    return _machine.snapshot(uid)


def _reset_for_tests() -> None:
    global _machine
    _machine = TriggerStateMachine()
