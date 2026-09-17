"""聊天日志只读接口
GET /chat-log/dates      — 返回所有可用日期列表（倒序）
GET /chat-log/{date}     — 返回单日解析后的对话条目
owner_qq 由后端从 config 读取，接口路径不暴露 QQ 号。
"""

import json as _json
import re
import asyncio
import calendar
import sqlite3
from datetime import date as CalendarDate, timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from admin.auth import require_scopes
from core.config_loader import get_config
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope
from core.sandbox import get_paths, safe_user_id

router = APIRouter()

_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_FILE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}\.md$')


def _owner_qq() -> str:
    return str(get_config().get("scheduler", {}).get("owner_id", "")).strip()


def _resolve_char_id(char_id: str | None) -> str:
    """Resolve and validate a char_id for chat-log operations.

    If char_id is None, reads active_character from active_prompt_assets.json.
    Raises HTTP 503 if active_character is missing or unreadable.
    Raises HTTP 422 if the resolved or supplied char_id is not a known character.
    Never falls back to a hardcoded character.
    """
    from core.asset_registry import get_registry

    if char_id is None:
        try:
            data = _json.loads(get_paths().active_prompt_assets().read_text(encoding="utf-8"))
            char_id = (data.get("active_character") or "").strip()
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"读取 active_prompt_assets.json 失败: {e}")
        if not char_id:
            raise HTTPException(
                status_code=503,
                detail="active_prompt_assets.json 中 active_character 为空，请先设置活跃角色",
            )

    try:
        get_registry().resolve(char_id, "character")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return char_id


def _log_dir(char_id: str) -> Path:
    owner = _owner_qq()
    if not owner:
        raise HTTPException(status_code=500, detail="owner_id not configured")
    uid = safe_user_id(owner)
    scope = MemoryScope.reality_scope(uid, char_id)
    new = resolve_path(scope, "event_log")
    old = get_paths()._p("event_log") / uid
    # for_read() reads bytes — unsuitable for directories; check with is_dir() instead.
    return new if new.is_dir() else old


def _parse_day(text: str) -> list[dict]:
    """
    把单日 MD 文本解析成 entry 列表。
    格式：
      ## HH:MM
      **用户**：...
      > turn_id:...
      **他**：...
      > emotion:... intensity:N turn_id:...
      ---
    返回 time/user/assistant，以及可信 assistant 尾部元数据中的可选 turn_id。
    能解析多少算多少；整体无法识别时返回空列表由调用方处理。
    """
    entries = []
    # 按 ## 开头的时间行切块
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current:
                blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)

    for block in blocks:
        if not block:
            continue
        time_line = block[0]
        m = re.match(r'^## (\d{2}:\d{2})', time_line)
        if not m:
            continue
        time_str = m.group(1)

        user_lines: list[str] = []
        assistant_lines: list[str] = []
        state = "seek_user"
        turn_id = ""
        trigger = ""

        for index, line in enumerate(block[1:], start=1):
            stripped = line.strip()
            if stripped == "---":
                break
            # Only the writer's terminal assistant footer can identify a reply.
            # Quotes inside message bodies (even metadata-shaped quotes) are text.
            tail = ""
            if stripped.startswith("> "):
                tail = next((item.strip() for item in block[index + 1:] if item.strip()), "")
            assistant_meta = (
                state == "in_assistant"
                and re.fullmatch(r'> emotion:\S+ intensity:\d+(?: \S+:\S+)*', stripped)
                and tail == "---"
            )
            user_meta = (
                state == "in_user"
                and re.fullmatch(r'> (?:speaker:user|turn_id:\S+)(?: \S+:\S+)*', stripped)
                and re.match(r'^\*\*(?!用户\*\*)(.+?)\*\*[：:]', tail)
            )
            if assistant_meta or user_meta:
                if assistant_meta:
                    fields = stripped[2:].split()
                    trigger = next((part.partition(':')[2] for part in fields if part.startswith('trigger:')), '')
                    ids = [part.partition(":")[2] for part in fields if part.startswith("turn_id:")]
                    speakers = [part for part in fields if part.startswith("speaker:")]
                    if len(ids) == 1 and speakers in ([], ["speaker:assistant"]):
                        turn_id = ids[0]
                if user_meta:
                    state = "seek_assistant"
                else:
                    state = "done"
                continue

            if state == "seek_user":
                if stripped.startswith("**用户**：") or stripped.startswith("**用户**:"):
                    content = re.sub(r'^\*\*用户\*\*[：:]', '', stripped)
                    user_lines.append(content)
                    state = "in_user"
                else:
                    char_match = re.match(r'^\*\*(.+?)\*\*[：:](.*)', stripped)
                    if char_match:
                        assistant_lines.append(char_match.group(2))
                        state = "in_assistant"
            elif state == "in_user":
                if stripped.startswith("**") and "**：" in stripped or "**:" in stripped:
                    # 可能是他行
                    char_match = re.match(r'^\*\*(.+?)\*\*[：:](.*)', stripped)
                    if char_match and char_match.group(1) != "用户":
                        assistant_lines.append(char_match.group(2))
                        state = "in_assistant"
                    else:
                        user_lines.append(stripped)
                else:
                    user_lines.append(stripped)
            elif state == "seek_assistant":
                char_match = re.match(r'^\*\*(.+?)\*\*[：:](.*)', stripped)
                if char_match and char_match.group(1) != "用户":
                    assistant_lines.append(char_match.group(2))
                    state = "in_assistant"
            elif state == "in_assistant":
                assistant_lines.append(stripped)

        user_text = "\n".join(user_lines).strip()
        assistant_text = "\n".join(assistant_lines).strip()

        if not user_text and not assistant_text:
            continue

        entries.append({
            "time": time_str,
            "user": user_text,
            "assistant": assistant_text,
            **({"turn_id": turn_id} if turn_id else {}),
            **({'entry_kind': 'narration'} if trigger == 'action_trace' else {}),
        })

    return entries


@router.get("/dates", summary="获取聊天日志日期列表")
async def list_dates(char_id: str | None = None, auth=Depends(require_scopes("memory.read"))):
    resolved = _resolve_char_id(char_id)
    log_dir = _log_dir(resolved)
    dates = []
    if log_dir.exists():
        for f in log_dir.iterdir():
            if _FILE_RE.match(f.name):
                dates.append(f.stem)
    from core.memory.action_trace import recent
    from datetime import datetime
    dates.extend(datetime.fromtimestamp(row['display_activity']['ts']).strftime('%Y-%m-%d')
                 for row in recent(_owner_qq(), resolved, max_items=30, window_hours=24 * 36500)
                 if isinstance(row.get('display_activity'), dict))
    dates = sorted(set(dates), reverse=True)
    return {"dates": dates, "count": len(dates)}


@router.get("/stats/calendar", summary="对话热力图与每日用量")
async def calendar_stats(
    period: Literal["day", "week", "month", "year"] = "month",
    date: CalendarDate | None = None,
    start: CalendarDate | None = None,
    end: CalendarDate | None = None,
    char_id: str | None = None,
    auth=Depends(require_scopes("memory.read", "state.read")),
):
    """自然日数据；周从周一开始。显式起止日期最多 366 天，含首尾。"""
    from core.conversation_stats import query
    resolved = _resolve_char_id(char_id)
    owner = _owner_qq()
    if not owner:
        raise HTTPException(503, "owner_id not configured")
    # Unscoped legacy directories cannot prove which character owns a turn.
    log_dir = resolve_path(MemoryScope.reality_scope(safe_user_id(owner), resolved), "event_log")
    anchor = date or CalendarDate.today()
    if (start is None) != (end is None):
        raise HTTPException(422, "start and end must be supplied together")
    if start is None:
        if period == "day":
            start = end = anchor
        elif period == "week":
            start = anchor - timedelta(days=anchor.weekday())
            end = start + timedelta(days=6)
        elif period == "month":
            start = anchor.replace(day=1)
            end = anchor.replace(day=calendar.monthrange(anchor.year, anchor.month)[1])
        else:
            start, end = anchor.replace(month=1, day=1), anchor.replace(month=12, day=31)
    if end < start or (end - start).days > 365 or start.year < 1970 or end == CalendarDate.max:
        raise HTTPException(422, "range must contain 1 to 366 days")

    def read():
        result = query(start, end, uid=owner, char_id=resolved)
        for item in result["days"]:
            item.setdefault("chat_rounds_source", "counter")
            if item["coverage"] != "complete":
                path = log_dir / (item["date"] + ".md")
                if path.exists():
                    entries = _parse_day(path.read_text(encoding="utf-8"))
                    pairs = [entry for entry in entries if entry["user"] and entry["assistant"]
                             and entry.get("entry_kind") != "narration"]
                    count = len({entry["turn_id"] for entry in pairs if entry.get("turn_id")})
                    count += sum(not entry.get("turn_id") for entry in pairs)
                    item["chat_rounds"] = max(item["chat_rounds"] or 0, count)
                    item["chat_rounds_source"] = "retained_chat_log_partial"
            if item["date"] > CalendarDate.today().isoformat():
                item["coverage"] = "future"
                for key in ("chat_rounds", "tool_calls", "image_views", "input_tokens", "output_tokens", "total_tokens"):
                    item[key] = None
        return result
    try:
        result = await asyncio.to_thread(read)
    except (OSError, sqlite3.Error):
        raise HTTPException(503, "conversation statistics unavailable") from None
    result.update(start=start.isoformat(), end=end.isoformat(), char_id=resolved,
                  period=period, week_starts_on="monday", schema_version=1)
    result["totals"] = {key: sum(item[key] or 0 for item in result["days"])
                        for key in ("chat_rounds", "tool_calls", "image_views", "input_tokens", "output_tokens", "total_tokens", "usage_missing_calls")}
    result["totals_partial"] = any(item["coverage"] in {"partial", "unavailable"}
                                    or item["usage_missing_calls"] for item in result["days"])
    return result


@router.get("/{date}", summary="获取单日聊天日志")
async def get_day(date: str, char_id: str | None = None, auth=Depends(require_scopes("memory.read"))):
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=422, detail="date format must be YYYY-MM-DD")
    resolved = _resolve_char_id(char_id)
    log_dir = _log_dir(resolved)
    path = log_dir / f"{date}.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ''
    entries = _parse_day(text)
    # Recover recent tool receipts from the existing bounded action trace.
    # Older action echoes remain narration; no inferred success or chain IDs.
    from core.memory.action_trace import recent
    from datetime import datetime
    activities = [row['display_activity'] for row in recent(_owner_qq(), resolved, max_items=30, window_hours=24 * 36500)
                  if isinstance(row.get('display_activity'), dict)
                  and datetime.fromtimestamp(row['display_activity']['ts']).strftime('%Y-%m-%d') == date]
    activity_ids = {item['event_id'] for item in activities}
    if not path.exists() and not activities:
        raise HTTPException(status_code=404, detail="log not found")
    entries = [entry for entry in entries if not (entry.get('entry_kind') == 'narration' and entry.get('turn_id') in activity_ids)]
    entries.extend({'time': datetime.fromtimestamp(item['ts']).strftime('%H:%M'), 'ts': item['ts'],
                    'user': '', 'assistant': '', 'tool_activity': item} for item in activities)
    entries.sort(key=lambda entry: entry['time'])
    # Display-only projection from the canonical ledger; never replace memory text.
    # Missing/older ledgers retain the legacy plain-text history.
    from core.memory.event_query import get_event, EventQueryError
    scope = MemoryScope.reality_scope(_owner_qq(), resolved)
    for entry in entries:
        if not entry.get("turn_id") or not entry.get("assistant"):
            continue
        try:
            event = get_event(scope, entry["turn_id"] + ":assistant", include_isolated=True)
        except EventQueryError:
            continue
        if event and not event.get("tombstoned") and event.get("visible_text"):
            from core.response_processor import inline_display_text
            entry["assistant_display_text"] = inline_display_text(event["visible_text"])
    raw_fallback = len(entries) == 0 and bool(text.strip())

    return {
        "date": date,
        "entries": entries,
        "raw_fallback": raw_fallback,
    }
