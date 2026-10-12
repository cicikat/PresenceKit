from core.memory.health_state import load as load_health_state


def read_watch_for_user(user_id: str, query: str = "", *, char_id: str | None = None) -> str:
    """
    读取用户的watch数据，返回给角色的自然语言描述。
    query可以是"睡眠"/"心率"/"运动"/"最近"，不填返回综合摘要。
    """
    health_state = load_health_state(user_id, char_id=char_id)

    sleep_segments = [
        s for s in health_state.get("sleep_segments", [])
        if s.get("duration_minutes", 0) > 0
    ][-3:]

    heart_rate_events = health_state.get("heart_rate_events", [])[-3:]

    if not sleep_segments and not heart_rate_events:
        return "暂时没有身体数据记录"

    lines = []

    if sleep_segments:
        lines.append("最近的睡眠记录：")
        for seg in reversed(sleep_segments):
            date_str = seg["time"][:10]
            start = seg.get("sleep_start", "")
            end = seg.get("sleep_end_time", "")
            dur = int(seg.get("duration_minutes", 0))
            h, m = dur // 60, dur % 60
            lines.append(f"  {date_str} 入睡{start} 起床{end} 共{h}小时{m}分钟")

    if heart_rate_events:
        lines.append("最近的心率记录：")
        for ev in reversed(heart_rate_events):
            lines.append(
                f"  {ev.get('time', '')} 心率{ev.get('value', '')} "
                f"{'（触发过关心）' if ev.get('triggered') else ''}"
            )

    return "\n".join(lines)
