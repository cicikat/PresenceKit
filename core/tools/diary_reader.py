"""
日记读取模块
读取 config.yaml diary.obsidian_path 下的日记和心理感悟
"""
import re
from pathlib import Path
from datetime import date, timedelta
from core.error_handler import log_error

_DIARY_FILENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


def _diary_root(*, char_id: str | None = None) -> Path:
    from core.config_loader import get_config
    from core.sandbox import get_paths
    from core.prompt_asset_selection import active_character
    char_id = char_id or active_character()
    p = get_config().get("diary", {}).get("by_character", {}).get(char_id, {}).get("obsidian_path", "")
    return Path(p) if p else get_paths().diary_fallback(char_id=char_id)

def read_diary(target_date: date, *, char_id: str | None = None) -> str:
    from core.deployment_capabilities import is_remote_server

    if is_remote_server():
        from core import diary_mirror

        return diary_mirror.read_entry(target_date, char_id=char_id)
    """读取指定日期的日记，返回文本，不存在返回空字符串"""
    filename = f"{target_date.strftime('%Y-%m-%d')}.md"
    for path in _diary_root(char_id=char_id).rglob(filename):
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception as e:
            from core.error_handler import log_error
            log_error("diary_reader.read_diary", e)
    return ""

def read_recent(days: int = 3, *, char_id: str | None = None) -> str:
    from core.prompt_asset_selection import active_character
    char_id = char_id or active_character()
    """读取最近N天的所有md文件，拼接返回"""
    today = date.today()
    parts = []
    for i in range(1, days + 1):
        target = today - timedelta(days=i)
        text = read_diary(target, char_id=char_id)
        if text:
            parts.append(f"# {target}\n{text}")
    return "\n\n".join(parts)

def yesterday_missing(*, char_id: str | None = None) -> bool:
    """昨天是否没有日记"""
    yesterday = date.today() - timedelta(days=1)
    return read_diary(yesterday, char_id=char_id) == ""


def has_any_diary_entry(*, char_id: str | None = None) -> bool:
    """日记目录里是否曾经出现过至少一篇日记（不限昨天/最近几天）。

    未配置 obsidian_path 且本地 diary_fallback/ 目录也从没写过任何一篇时返回
    False——diary_reminder 类触发器据此判断"用户根本没在用日记功能"，不能把
    "从没配置/从没写过"当成"漏了一天没写"（Brief 97 追加修复）。
    """
    from core.deployment_capabilities import is_remote_server

    if is_remote_server():
        from core import diary_mirror

        return diary_mirror.has_any_entry(char_id=char_id)
    root = _diary_root(char_id=char_id)
    if not root.exists():
        return False
    try:
        return any(
            path.is_file() and _DIARY_FILENAME_RE.match(path.name)
            for path in root.rglob("*.md")
        )
    except Exception as e:
        log_error("diary_reader.has_any_diary_entry", e)
        return False
