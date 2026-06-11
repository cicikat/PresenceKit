"""
Watch 事件接收路由
接收来自可穿戴设备（Apple Watch 等）推送的健康事件，
转发给 scheduler 触发对应的主动消息。

事件格式（POST /watch/event）：
  {"type": "heart_rate", "value": 120}
  {"type": "sleep_end"}
  {"type": "heart_rate", "value": 85}

只接受管理面 Authorization Bearer token（verify_token）。
"""

from datetime import datetime, datetime as _dt

from fastapi import APIRouter, Depends, HTTPException

from admin.auth import verify_token
from core.config_loader import get_config
from core.memory.user_profile import load as _load_profile, save as _save_profile


def _append_heart_rate_event(user_id: str, value: int, triggered: bool):
    from core.write_envelope import stamp_sensor_watch
    _env = stamp_sensor_watch()
    if not _env.can_write_memory:
        return
    profile = _load_profile(user_id)
    events = profile.get("heart_rate_events", [])
    events.append({
        "time": _dt.now().strftime("%Y-%m-%d %H:%M"),
        "value": value,
        "triggered": triggered,
    })
    profile["heart_rate_events"] = events[-20:]
    _save_profile(user_id, profile)

router = APIRouter()

# 最近一次 Watch 事件快照（内存缓存，重启清零）
_last_watch_data: dict = {}

# sleep_end 缓冲区，收集5分钟内所有阶段后合并处理
_sleep_buffer: list = []
_sleep_flush_task = None


async def _flush_sleep_buffer():
    """等待1分钟后合并所有睡眠阶段，作为一条完整睡眠处理"""
    import logging
    logging.getLogger(__name__).info(f"[watch] flush开始，缓冲区条数: {len(_sleep_buffer)}")
    import asyncio
    await asyncio.sleep(60)  # 等1分钟

    if not _sleep_buffer:
        return

    sleep_start = _sleep_buffer[0]["sleep_start"]
    sleep_end_time = _sleep_buffer[-1]["sleep_end_time"]
    try:
        from datetime import datetime as _dt
        t_start = _dt.strptime(sleep_start, "%H:%M")
        t_end = _dt.strptime(sleep_end_time, "%H:%M")
        diff = (t_end - t_start).total_seconds()
        if diff < 0:
            diff += 86400
        duration_minutes = round(diff / 60, 1)
    except Exception:
        duration_minutes = 0

    merged = {
        "sleep_start":      sleep_start,
        "sleep_end_time":   sleep_end_time,
        "duration_minutes": duration_minutes,
    }
    _sleep_buffer.clear()

    # 存入 sleep_segments
    oid = str(get_config().get("scheduler", {}).get("owner_id", ""))
    if oid:
        from core.write_envelope import stamp_sensor_watch
        _env = stamp_sensor_watch()
        if _env.can_write_memory:
            from core.memory.user_profile import load as _load, save as _save
            profile = _load(oid)
            profile.setdefault("sleep_segments", [])
            profile["sleep_segments"].append({
                "time":             datetime.now().isoformat(),
                "duration_minutes": merged["duration_minutes"],
                "sleep_start":      merged["sleep_start"],
                "sleep_end_time":   merged["sleep_end_time"],
            })
            if len(profile["sleep_segments"]) > 20:
                profile["sleep_segments"] = profile["sleep_segments"][-20:]
            _save(oid, profile)

    # 更新快照
    _last_watch_data.clear()
    _last_watch_data.update({
        "event_type":     "sleep_end",
        "timestamp":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "received_at":    datetime.now().isoformat(),
        **merged,
    })
    import logging
    logging.getLogger(__name__).info(f"[watch] 快照已更新: {_last_watch_data}")

    sleep_start_str = merged.get("sleep_start", "")
    duration = merged.get("duration_minutes", 0)
    from core.config_loader import _char_name
    sleep_comment = ""
    if sleep_start_str:
        try:
            start_hour = int(sleep_start_str.split(":")[0])
            enough   = duration >= 360
            too_much = duration >= 600

            def _slot(h):
                if 2 <= h <= 6:       return "late_night"
                if h >= 23 or h == 0: return "night"
                return "normal"

            slot = _slot(start_hour)
            _cname = _char_name()

            if too_much:
                extra = "而且凌晨才睡，" if slot == "late_night" else ""
                sleep_comment = f"睡了很久，{extra}{_cname}担心是不是太累了或者身体不舒服"
            else:
                _table = {
                    ("late_night", True):  f"凌晨才睡，但好在睡够了，{_cname}心疼但松了口气",
                    ("late_night", False): f"凌晨才睡还没睡够，{_cname}又心疼又生气",
                    ("night",      True):  f"睡得有点晚，但睡够了，{_cname}会提一句",
                    ("night",      False): f"睡得晚又没睡够，{_cname}会念叨一下",
                    ("normal",     True):  f"睡得早也睡够了，{_cname}会夸一句",
                    ("normal",     False): f"睡得还行但没睡够，{_cname}会关心一下",
                }
                sleep_comment = _table[(slot, enough)]
        except Exception:
            pass

    # 趋势分析（最近5条sleep_segments）
    trend_comment = ""
    try:
        from core.memory import user_profile as _up
        profile = _up.load(oid)
        segments = profile.get("sleep_segments", [])[-5:]
        if len(segments) >= 3:
            durations = [s.get("duration_minutes", 0) for s in segments]
            starts = [int(s.get("sleep_start", "0:0").split(":")[0]) for s in segments]
            
            avg_recent = sum(durations[-3:]) / 3
            avg_prev = sum(durations[:-3]) / max(len(durations[:-3]), 1)
            late_nights = sum(1 for h in starts[-3:] if h >= 1 and h <= 6)
            
            if late_nights >= 3:
                trend_comment = "，而且最近连续好几天都凌晨才睡"
            elif avg_recent < avg_prev - 60:
                trend_comment = "，最近睡眠时间在缩短"
    except Exception:
        pass

    hours = int(duration // 60)
    minutes = int(duration % 60)
    now_hour = datetime.now().hour
    _cname = get_config().get("character", {}).get("name", "他")
    if now_hour < 12:
        prompt = f"（{_cname}看到你醒了，昨晚睡了{hours}小时{minutes}分钟，{sleep_comment}{trend_comment}）"
    else:
        prompt = f"（{_cname}看到你醒了，睡了{hours}小时{minutes}分钟，{sleep_comment}{trend_comment}）"

    from core import scheduler
    await scheduler.on_watch_event("sleep_end", {**merged, "prompt": prompt})


@router.post("/watch/event", summary="接收 Watch 健康事件")
async def receive_watch_event(
    body: dict,
    _auth: bool = Depends(verify_token),
):
    """
    外部设备推送健康事件的入口。

    body 字段：
      type  — 事件类型：heart_rate / sleep_end
      value — 数值（心率时必填）

    鉴权：仅接受管理面 Authorization Bearer token。
    """

    event_type = str(body.get("type", "")).strip()
    if not event_type:
        raise HTTPException(status_code=422, detail="缺少 type 字段")

    data = {}
    if event_type == "heart_rate":
        val = body.get("value")
        if val is None:
            raise HTTPException(status_code=422, detail="heart_rate 事件需要 value 字段")
        try:
            data["value"] = int(val)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="value 必须为整数")
    elif event_type == "sleep_end":
        import asyncio
        sleep_start_raw = str(body.get("sleep_start", ""))
        sleep_end_raw = str(body.get("sleep_end", ""))
        # 捷径可能把多个时间用换行拼成一个字段，取第一个入睡时间和最后一个起床时间
        sleep_start_list = [s.strip() for s in sleep_start_raw.split("\n") if s.strip()]
        sleep_end_list = [s.strip() for s in sleep_end_raw.split("\n") if s.strip()]
        sleep_start = sleep_start_list[-1] if sleep_start_list else ""
        sleep_end_time = sleep_end_list[0] if sleep_end_list else ""
        # duration捷径传的不可靠，用起床时间-入睡时间自己算
        try:
            from datetime import datetime as _dt
            t_start = _dt.strptime(sleep_start, "%H:%M")
            t_end = _dt.strptime(sleep_end_time, "%H:%M")
            diff = (t_end - t_start).total_seconds()
            if diff < 0:
                diff += 86400  # 跨午夜
            duration_minutes = round(diff / 60, 1)
        except Exception:
            duration_minutes = 0

        # 存入缓冲区
        _sleep_buffer.append({
            "sleep_start":      sleep_start,
            "sleep_end_time":   sleep_end_time,
            "duration_minutes": duration_minutes,
        })

        # 重置或启动合并任务（每次收到新数据都重置5分钟计时）
        global _sleep_flush_task
        if _sleep_flush_task and not _sleep_flush_task.done():
            _sleep_flush_task.cancel()
        _sleep_flush_task = asyncio.create_task(_flush_sleep_buffer())

        return {"message": "sleep_end 已缓冲，等待合并", "data": {}}
    else:
        raise HTTPException(status_code=422, detail=f"不支持的事件类型: {event_type}")

    # 记录最近事件快照（心率）
    _last_watch_data.clear()
    _last_watch_data.update({
        "event_type": event_type,
        "timestamp":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **data,
    })
    _last_watch_data["received_at"] = datetime.now().isoformat()

    # 写入 user_profile（心率事件）
    oid = str(get_config().get("scheduler", {}).get("owner_id", ""))
    if oid and event_type == "heart_rate":
        _append_heart_rate_event(oid, data["value"], triggered=False)

    import asyncio
    from core import scheduler
    asyncio.create_task(scheduler.on_watch_event(event_type, data))

    return {"message": f"事件 {event_type} 已接收", "data": data}


@router.get("/watch/status", summary="获取最近一次 Watch 事件状态")
async def get_watch_status(auth=Depends(verify_token)):
    """返回最近一次推送的 Watch 事件快照，未收到任何事件时返回空 dict"""
    return _last_watch_data
