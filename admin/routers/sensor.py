"""
手机传感器数据接收路由（口袋角色）
接收来自手机APP推送的传感器数据，存入用户画像供角色感知。

数据格式（POST /sensor/push）：
  {
    "steps": 3200,
    "battery": 85,
    "charging": true,
    "plugged": "usb",
    "location": "杭州",
    "screen_sessions": 12,
    "timestamp": 1714000000
  }

所有端点均使用管理面 Bearer token 鉴权。
"""

import json
import time
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Literal, Optional
from admin.auth import require_scopes
from core.config_loader import get_config
from core.memory import realtime_state
from core.sandbox import get_paths

router = APIRouter()

# 最近一次手机传感器快照（内存缓存，重启清零）
_last_sensor_data: dict = {}

# 供电方式白名单，与手机端 BatteryStatus.pluggedValues 对齐。
_PLUGGED_VALUES = {"ac", "usb", "wireless", "none"}

_SENSITIVE_WINDOW_KEYWORDS = (
    "密码", "password", "银行", "bank", "支付", "payment",
    "微信支付", "alipay", "私聊", "secret", "1password",
    "登录", "login", "身份证", "信用卡",
)


def _is_sensitive_window_text(text: str) -> bool:
    folded = str(text or "").casefold()
    return any(keyword.casefold() in folded for keyword in _SENSITIVE_WINDOW_KEYWORDS)


def _save_sensor_to_health_state(data: dict):
    """Persist objective sensor observations outside the character profile."""
    oid = str(get_config().get("scheduler", {}).get("owner_id", ""))
    if not oid:
        return

    from core.memory import health_state

    def apply_sensor(state: dict) -> None:
        log = state["phone_sensor_log"]
        log.append({
            "time":            datetime.now().strftime("%Y-%m-%d %H:%M"),
            "steps":           data.get("steps"),
            "battery":         data.get("battery"),
            "charging":        data.get("charging"),
            "plugged":         data.get("plugged"),
            "location":        data.get("location"),
            "screen_sessions": data.get("screen_sessions"),
        })
        state["phone_sensor_log"] = log[-30:]

        today = datetime.now().strftime("%Y-%m-%d")
        summary = dict(state["phone_sensor_today"] or {})
        if summary.get("date") != today:
            summary = {}
        if data.get("steps") is not None:
            summary["steps"] = max(summary.get("steps", 0), data["steps"])
        if data.get("battery") is not None:
            summary["battery"] = data["battery"]
        # 充电状态只在这次上报确实读到时才覆盖摘要；缺失不清空上一次的已知值，
        # 也不写 False —— "不知道" 和 "没在充电" 不能塌成同一个字段。
        if data.get("charging") is not None:
            summary["charging"] = data["charging"]
        if data.get("plugged") is not None:
            summary["plugged"] = data["plugged"]
        if data.get("location"):
            summary["location"] = data["location"]
        if data.get("screen_sessions") is not None:
            summary["screen_sessions"] = max(summary.get("screen_sessions", 0), data["screen_sessions"])
        summary["date"] = today
        summary["last_updated"] = datetime.now().isoformat(timespec="seconds")
        state["phone_sensor_today"] = summary

    health_state.mutate(oid, apply_sensor)


@router.post("/sensor/push", summary="接收手机传感器数据")
async def receive_sensor_data(body: dict, auth=Depends(require_scopes("sensor.write"))):
    """
    手机APP每30分钟推送一次传感器数据。

    body字段（均可选，有什么传什么）：
      steps          — 今日步数
      battery        — 当前电量（0-100）
      charging       — 是否正在充电（bool；读不到时省略该 key，不要发 false）
      plugged        — 供电方式（ac / usb / wireless / none）
      location       — 城市名（可选）
      screen_sessions — 今日亮屏次数
      timestamp      — 时间戳（可选，不传用服务器时间）
    """

    # 基础校验
    steps = body.get("steps")
    battery = body.get("battery")
    charging = body.get("charging")
    plugged = body.get("plugged")

    if steps is not None:
        try:
            steps = int(steps)
            if steps < 0:
                raise ValueError
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="steps 必须为非负整数")

    if battery is not None:
        try:
            battery = int(battery)
            if not (0 <= battery <= 100):
                raise ValueError
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="battery 必须为 0-100 的整数")

    # bool 而非 truthy：1 / "true" 一律拒绝，否则客户端一处笔误就会把
    # "不知道" 静默变成 "正在充电"。
    if charging is not None and not isinstance(charging, bool):
        raise HTTPException(status_code=422, detail="charging 必须为布尔值")

    if plugged is not None:
        plugged = str(plugged).strip().lower()
        if plugged not in _PLUGGED_VALUES:
            raise HTTPException(
                status_code=422,
                detail=f"plugged 必须为 {'/'.join(sorted(_PLUGGED_VALUES))} 之一",
            )

    data = {
        "steps":           steps,
        "battery":         battery,
        "charging":        charging,
        "plugged":         plugged,
        "location":        str(body.get("location", "")).strip() or None,
        "screen_sessions": body.get("screen_sessions"),
    }

    # 更新内存快照
    _last_sensor_data.clear()
    _last_sensor_data.update({
        **data,
        "received_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })

    _save_sensor_to_health_state(data)

    return {"message": "传感器数据已接收", "data": data}


@router.get("/sensor/status", summary="获取最近一次手机传感器快照")
async def get_sensor_status(auth=Depends(require_scopes("state.read"))):
    """返回最近一次推送的传感器数据快照"""
    return _last_sensor_data


@router.get("/sensor/today", summary="获取今日传感器聚合摘要")
async def get_sensor_today(auth=Depends(require_scopes("state.read"))):
    """返回今日聚合摘要，角色的context读这个"""
    oid = str(get_config().get("scheduler", {}).get("owner_id", ""))
    if not oid:
        return {}
    from core.memory import health_state
    return health_state.load(oid).get("phone_sensor_today") or {}


# ── Pydantic 模型（仅 /sensor/realtime 使用，不对外暴露）─────────────────────

class _RealtimeInput(BaseModel):
    keystrokes: int = Field(ge=0)
    mouse_clicks: int = Field(ge=0)
    mouse_distance_px: int = Field(ge=0)
    idle_seconds: int = Field(ge=0)
    edit_hint: Optional[Literal["typing_long", "editing", "deleting", "idle"]] = None


class _RealtimeFocus(BaseModel):
    app: str
    title_hint: str
    switch_count: int = Field(ge=0)


class _RealtimeScreen(BaseModel):
    package_name: str = ""
    app_label: str = ""
    window_title: str = ""
    visible_text: list[str] = []
    clickable_text: list[str] = []


class _RealtimeIngest(BaseModel):
    window_seconds: int = Field(ge=1, le=300)
    ts: float
    sensor_version: str
    input: _RealtimeInput
    focus: _RealtimeFocus
    screen: Optional[_RealtimeScreen] = None


@router.post("/sensor/realtime", summary="接收桌面端实时传感器快照")
async def receive_realtime_snapshot(
    payload: _RealtimeIngest,
    auth=Depends(require_scopes("sensor.write")),
):
    # Sidecar 应整帧跳过敏感窗口；这里再次 fail-closed，防止旧客户端或误配置泄漏。
    if (
        _is_sensitive_window_text(payload.focus.title_hint)
        or (
            payload.screen is not None
            and _is_sensitive_window_text(payload.screen.window_title)
        )
    ):
        return {"ok": False, "skipped": "sensitive_window"}

    # title_hint server-side 兜底截断
    if len(payload.focus.title_hint) > 80:
        payload.focus.title_hint = payload.focus.title_hint[:80]

    data = payload.model_dump()
    if payload.screen is not None:
        data["screen"] = {
            "package_name": payload.screen.package_name[:120],
            "app_label": payload.screen.app_label[:80],
            "window_title": payload.screen.window_title[:120],
            "visible_text": [
                str(x).strip()[:80]
                for x in payload.screen.visible_text
                if str(x).strip()
            ][:60],
            "clickable_text": [
                str(x).strip()[:80]
                for x in payload.screen.clickable_text
                if str(x).strip()
            ][:40],
        }

    realtime_state.update(data)
    return {"ok": True, "received_at": time.time()}


@router.get("/sensor/realtime", summary="读取最新实时状态快照")
async def get_realtime_snapshot(auth=Depends(require_scopes("state.read"))):
    snap = realtime_state.get()
    if snap is None:
        # Keep the no-sample state structurally distinct from a usable snapshot.
        # Returning a snapshot-shaped object full of nulls makes typed clients
        # believe nested input/focus fields are safe to dereference.
        return {"_no_data": True}
    return {
        "ts":                          snap["ts"],
        "stale_seconds":               int(time.time() - snap["received_at"]),
        "presence":                    realtime_state.get_presence(),
        "continuous_at_desk_seconds":  realtime_state.get_continuous_at_desk_seconds(),
        "sensor_version":              snap["sensor_version"],
        "window_seconds":              snap["window_seconds"],
        "input":                       snap["input"],
        "focus":                       snap["focus"],
        "screen":                      snap.get("screen"),
    }


@router.get("/sensor/behavior/status", summary="读取最近一次 sensor_aware 行为裁决")
async def get_behavior_status(auth=Depends(require_scopes("state.read"))):
    from core.scheduler.triggers import sensor_aware

    return sensor_aware.get_last_decision()
