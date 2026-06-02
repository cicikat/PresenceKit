"""
core/write_envelope.py — WriteEnvelope v0 准入控制信封

零值对象（WriteEnvelope()）是最严格状态：
  can_write_memory=False, can_affect_mood=False

所有合法入口必须显式 stamp。未 stamp 的调用默认 fail-closed。
禁止字段间推断，唯一自动规则：is_test or is_debug → 强制关闭写入。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SourceType(str, Enum):
    UNKNOWN        = "unknown"
    USER_CHAT      = "user_chat"
    QQ             = "qq"
    INGEST         = "ingest"
    TRIGGER        = "trigger"
    SENSOR         = "sensor"
    SENSOR_WATCH   = "sensor/watch"
    DREAM_AFTERGLOW = "dream_afterglow"


class PerceptionSensitivity(str, Enum):
    OPEN          = "open"
    ATTITUDE_ONLY = "attitude_only"
    NEVER_SPEAK   = "never_speak"


@dataclass
class WriteEnvelope:
    source: SourceType               = SourceType.UNKNOWN
    can_write_memory: bool           = False
    can_affect_mood: bool            = False
    perception_sensitivity: PerceptionSensitivity = PerceptionSensitivity.OPEN
    is_test: bool                    = False
    is_debug: bool                   = False

    def __post_init__(self):
        # 唯一自动规则：test / debug 强制关闭所有写入
        if self.is_test or self.is_debug:
            self.can_write_memory = False
            self.can_affect_mood  = False


# ── Stamp 工厂函数 ─────────────────────────────────────────────────────────────

def stamp_user_chat() -> WriteEnvelope:
    """真实 owner chat（desktop / admin 面板）。"""
    return WriteEnvelope(
        source=SourceType.USER_CHAT,
        can_write_memory=True,
        can_affect_mood=True,
        perception_sensitivity=PerceptionSensitivity.OPEN,
    )


def stamp_qq() -> WriteEnvelope:
    """QQ owner 消息路径。"""
    return WriteEnvelope(
        source=SourceType.QQ,
        can_write_memory=True,
        can_affect_mood=True,
        perception_sensitivity=PerceptionSensitivity.OPEN,
    )


def stamp_ingest() -> WriteEnvelope:
    """批量注入 / 内部 ingest 路径。"""
    return WriteEnvelope(
        source=SourceType.INGEST,
        can_write_memory=True,
        can_affect_mood=True,
        perception_sensitivity=PerceptionSensitivity.OPEN,
    )


def stamp_trigger() -> WriteEnvelope:
    """Scheduler 定时触发路径。"""
    return WriteEnvelope(
        source=SourceType.TRIGGER,
        can_write_memory=True,
        can_affect_mood=True,
        perception_sensitivity=PerceptionSensitivity.ATTITUDE_ONLY,
    )


def stamp_sensor() -> WriteEnvelope:
    """Sensor / Watch assistant turn（已产生回复的路径）。"""
    return WriteEnvelope(
        source=SourceType.SENSOR,
        can_write_memory=True,
        can_affect_mood=True,
        perception_sensitivity=PerceptionSensitivity.ATTITUDE_ONLY,
    )


def stamp_sensor_watch() -> WriteEnvelope:
    """Sensor / Watch 原始感知（raw 数据写入，禁止写记忆）。"""
    return WriteEnvelope(
        source=SourceType.SENSOR_WATCH,
        can_write_memory=False,
        can_affect_mood=False,
        perception_sensitivity=PerceptionSensitivity.NEVER_SPEAK,
    )


def stamp_dream_afterglow() -> WriteEnvelope:
    """Dream afterglow writeback — only valid at Dream exit, never inside a Dream turn.

    source=DREAM_AFTERGLOW is the required source for integrate_afterglow().
    can_affect_mood is False — afterglow does not directly alter mood_state.
    """
    return WriteEnvelope(
        source=SourceType.DREAM_AFTERGLOW,
        can_write_memory=True,
        can_affect_mood=False,
        perception_sensitivity=PerceptionSensitivity.ATTITUDE_ONLY,
    )


def stamp_debug() -> WriteEnvelope:
    """调试路径，强制关闭所有写入。"""
    return WriteEnvelope(is_debug=True)


def stamp_test() -> WriteEnvelope:
    """测试路径，强制关闭所有写入。"""
    return WriteEnvelope(is_test=True)
