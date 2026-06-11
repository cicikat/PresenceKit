"""
手机传感器数据接收路由（口袋角色）
接收来自手机APP推送的传感器数据，存入用户画像供角色感知。

数据格式（POST /sensor/push）：
  {
    "steps": 3200,
    "battery": 85,
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
from typing import Optional
from admin.auth import verify_token
from core.config_loader import get_config
from core.memory.user_profile import load as _load_profile, save as _save_profile
from core.memory import realtime_state
from core.sandbox import get_paths, _TRANSITION_CHARACTER_INNER

router = APIRouter()

# 最近一次手机传感器快照（内存缓存，重启清零）
_last_sensor_data: dict = {}


def _save_sensor_to_profile(data: dict):
    """把传感器数据聚合后存入用户画像"""
    from core.write_envelope import stamp_sensor_watch
    _env = stamp_sensor_watch()
    if not _env.can_write_memory:
        return
    oid = str(get_config().get("scheduler", {}).get("owner_id", ""))
    if not oid:
        return

    profile = _load_profile(oid)

    # 存入 phone_sensor_log，保留最近30条
    log = profile.get("phone_sensor_log", [])
    log.append({
        "time":            datetime.now().strftime("%Y-%m-%d %H:%M"),
        "steps":           data.get("steps"),
        "battery":         data.get("battery"),
        "location":        data.get("location"),
        "screen_sessions": data.get("screen_sessions"),
    })
    profile["phone_sensor_log"] = log[-30:]

    # 聚合今日摘要，角色读的是这个，不是原始流水
    today = datetime.now().strftime("%Y-%m-%d")
    summary = profile.get("phone_sensor_today", {})

    # 步数取最大值（今日累计）
    if data.get("steps") is not None:
        summary["steps"] = max(summary.get("steps", 0), data["steps"])

    # 电量记录最新值
    if data.get("battery") is not None:
        summary["battery"] = data["battery"]

    # 位置记录最新值
    if data.get("location"):
        summary["location"] = data["location"]

    # 亮屏次数取最大值
    if data.get("screen_sessions") is not None:
        summary["screen_sessions"] = max(summary.get("screen_sessions", 0), data["screen_sessions"])

    summary["date"] = today
    summary["last_updated"] = datetime.now().strftime("%H:%M")
    profile["phone_sensor_today"] = summary

    _save_profile(oid, profile)


@router.post("/sensor/push", summary="接收手机传感器数据")
async def receive_sensor_data(body: dict, auth=Depends(verify_token)):
    """
    手机APP每30分钟推送一次传感器数据。

    body字段（均可选，有什么传什么）：
      steps          — 今日步数
      battery        — 当前电量（0-100）
      location       — 城市名（可选）
      screen_sessions — 今日亮屏次数
      timestamp      — 时间戳（可选，不传用服务器时间）
    """

    # 基础校验
    steps = body.get("steps")
    battery = body.get("battery")

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

    data = {
        "steps":           steps,
        "battery":         battery,
        "location":        str(body.get("location", "")).strip() or None,
        "screen_sessions": body.get("screen_sessions"),
    }

    # 更新内存快照
    _last_sensor_data.clear()
    _last_sensor_data.update({
        **data,
        "received_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })

    # 存入用户画像
    _save_sensor_to_profile(data)

    return {"message": "传感器数据已接收", "data": data}


@router.get("/sensor/status", summary="获取最近一次手机传感器快照")
async def get_sensor_status(auth=Depends(verify_token)):
    """返回最近一次推送的传感器数据快照"""
    return _last_sensor_data


@router.get("/sensor/today", summary="获取今日传感器聚合摘要")
async def get_sensor_today(auth=Depends(verify_token)):
    """返回今日聚合摘要，角色的context读这个"""
    oid = str(get_config().get("scheduler", {}).get("owner_id", ""))
    if not oid:
        return {}
    profile = _load_profile(oid)
    return profile.get("phone_sensor_today", {})


# ── Pydantic 模型（仅 /sensor/realtime 使用，不对外暴露）─────────────────────

class _RealtimeInput(BaseModel):
    keystrokes: int = Field(ge=0)
    mouse_clicks: int = Field(ge=0)
    mouse_distance_px: int = Field(ge=0)
    idle_seconds: int = Field(ge=0)


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
    auth=Depends(verify_token),
):
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
async def get_realtime_snapshot(auth=Depends(verify_token)):
    snap = realtime_state.get()
    if snap is None:
        return {
            "ts": None,
            "stale_seconds": None,
            "presence": "active",
            "continuous_at_desk_seconds": None,
            "sensor_version": None,
            "window_seconds": None,
            "input": None,
            "focus": None,
            "screen": None,
        }
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
async def get_behavior_status(auth=Depends(verify_token)):
    from core.scheduler.triggers import sensor_aware

    return sensor_aware.get_last_decision()


@router.post("/sensor/activity", summary="接收桌宠端活动快照")
async def receive_activity_snapshot(payload: dict, auth=Depends(verify_token)):
    """桌宠端每5分钟推送一次屏幕活动快照，写入文件供 prompt_builder 读取。"""
    # Resolve active char_id — fail-loud, no yexuan fallback.
    try:
        _apa = json.loads(
            get_paths().active_prompt_assets().read_text(encoding="utf-8")
        )
        char_id = (_apa.get("active_character") or "").strip()
    except Exception as _e:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "[sensor.activity] active_prompt_assets 读取失败，跳过写入: %s", _e
        )
        return {"status": "skipped", "reason": "active_character_unresolvable"}

    if not char_id:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "[sensor.activity] active_character 为空，跳过写入"
        )
        return {"status": "skipped", "reason": "active_character_empty"}

    oid = str(get_config().get("scheduler", {}).get("owner_id", ""))
    payload["received_at"] = time.time()
    payload_len = len(json.dumps(payload, ensure_ascii=False))

    import logging as _logging
    _logging.getLogger(__name__).info(
        "[sensor.activity] uid=%s char_id=%s source=sensor payload_len=%d",
        oid, char_id, payload_len,
    )

    p = get_paths().activity_snapshot(char_id=char_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if _TRANSITION_CHARACTER_INNER:
        old = get_paths()._p("activity_snapshot.json")
        old.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return {"status": "ok", "char_id": char_id}
