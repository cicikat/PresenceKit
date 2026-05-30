"""聊天日志只读接口
GET /chat-log/dates      — 返回所有可用日期列表（倒序）
GET /chat-log/{date}     — 返回单日解析后的对话条目
owner_qq 由后端从 config 读取，接口路径不暴露 QQ 号。
"""

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from admin.auth import verify_token
from core.config_loader import get_config
from core.sandbox import get_paths, safe_user_id

router = APIRouter()

_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_FILE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}\.md$')


def _owner_qq() -> str:
    return str(get_config().get("scheduler", {}).get("owner_id", "")).strip()


def _log_dir() -> Path:
    owner = _owner_qq()
    if not owner:
        raise HTTPException(status_code=500, detail="owner_id not configured")
    uid = safe_user_id(owner)
    new = get_paths().user_memory_root(uid) / "event_log"
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
      **叶瑄**：...
      > emotion:... intensity:N turn_id:...
      ---
    返回 [{"time": "HH:MM", "user": "...", "assistant": "..."}]
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

        for line in block[1:]:
            stripped = line.strip()
            if stripped == "---":
                break
            if stripped.startswith("> "):
                # meta 行，跳过，切换状态
                if state == "in_user":
                    state = "seek_assistant"
                elif state == "in_assistant":
                    state = "done"
                continue

            if state == "seek_user":
                if stripped.startswith("**用户**：") or stripped.startswith("**用户**:"):
                    content = re.sub(r'^\*\*用户\*\*[：:]', '', stripped)
                    user_lines.append(content)
                    state = "in_user"
            elif state == "in_user":
                if stripped.startswith("**") and "**：" in stripped or "**:" in stripped:
                    # 可能是叶瑄行
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
        })

    return entries


@router.get("/dates", summary="获取聊天日志日期列表")
async def list_dates(auth=Depends(verify_token)):
    log_dir = _log_dir()
    dates = []
    if log_dir.exists():
        for f in log_dir.iterdir():
            if _FILE_RE.match(f.name):
                dates.append(f.stem)
    dates.sort(reverse=True)
    return {"dates": dates, "count": len(dates)}


@router.get("/{date}", summary="获取单日聊天日志")
async def get_day(date: str, auth=Depends(verify_token)):
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=422, detail="date format must be YYYY-MM-DD")
    log_dir = _log_dir()
    path = log_dir / f"{date}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="log not found")

    text = path.read_text(encoding="utf-8")
    entries = _parse_day(text)
    raw_fallback = len(entries) == 0 and bool(text.strip())

    return {
        "date": date,
        "entries": entries,
        "raw_fallback": raw_fallback,
    }
