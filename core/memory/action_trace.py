"""
core/memory/action_trace.py
============================
Brief 27 · 工具动作痕迹层：让角色跨轮记得"自己刚才做过什么"。

工具结果此前只在执行当轮注入 prompt（层 10_tool_result），下一轮就"失忆"。
这里给每次 tool_dispatcher.execute_structured() 落一条精简痕迹，环形保留最近 30 条，
供层 10.5_action_trace 注入"你最近做过的操作"。兼容 execute() 只解包同一结果。

不变量：
  - result_digest 只消费 core.tools.tool_result 的 safe_summary 出口，永不碰 raw_data。
  - peek_screen_content 特判：只记 title_hint，不让 visible_text/clickable_text 进痕迹。
  - 全 fail-open：任何异常只 log，不影响调用方（tool_dispatcher.execute_structured）主流程。
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope, require_character_id
from core.safe_write import safe_write_json
from core.sandbox import safe_user_id

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 30
_ARGS_DIGEST_CAP = 60
_RESULT_DIGEST_CAP = 80
_BLOCK_CHAR_CAP = 400

_TOOL_RESULT_NAME_RE = re.compile(r"^工具已执行：([^，]+)，")

# tool_name → 角色视角的动作短语，供层 10.5 文案拼接
_ACTION_LABELS: dict[str, str] = {
    "get_time": "看了时间",
    "add_reminder": "加了条提醒",
    "weather": "查了天气",
    "device_shutdown": "准备关机",
    "device_sleep": "让设备休眠",
    "web_search": "搜了一下",
    "read_diary": "看了日记",
    "read_watch": "看了身体数据",
    "search_diary": "翻了日记",
    "desktop_minimize": "最小化了窗口",
    "desktop_open_url": "打开了网页",
    "desktop_play_pause": "控制了播放",
    "desktop_notify": "发了条通知",
    "play_song": "点了首歌",
    "get_profile": "看了你的资料",
    "get_episodic": "回忆了往事",
    "exit_yandere": "让自己平静下来",
    "water_garden": "浇了花",
    "peek_screen_content": "看了眼屏幕",
    "toy_vibrate": "控制了玩具振动",
    "toy_stop": "停止了玩具",
    "toy_pattern": "换了个玩具模式",
    "read_toy_file": "翻了玩具文件",
    "write_toy_file": "写了玩具文件",
    "fs_list": "翻了下目录",
    "fs_read": "看了个文件",
    "minimize_window": "最小化了窗口",
    "open_url": "打开了网页",
    "play_pause": "控制了播放",
    "send_notification": "发了条通知",
    "dream_invite": "发出了梦境邀请",
    "toy_invite": "发出了玩耍邀请",
}


def _enabled() -> bool:
    from core.config_loader import get_config
    return bool(get_config().get("action_trace", {}).get("enabled", True))


def _trace_path(uid: str, char_id: str) -> Path:
    require_character_id(char_id)
    scope = MemoryScope.reality_scope(safe_user_id(uid), char_id)
    return resolve_path(scope, "action_trace")


def _load(path: Path) -> list[dict]:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception as e:
        logger.debug("[action_trace] load failed: %s", e)
    return []


def build_args_digest(tool_name: str, tool_args: dict | None) -> str:
    """从 tool_args 里按 _TOOL_REGISTRY[tool_name]['trace_args'] 白名单拼接。

    未声明 trace_args 的工具只记工具名（此函数返回空串），防止 secrets/长文本入痕迹。
    """
    if not tool_args:
        return ""
    try:
        from core.tool_dispatcher import _TOOL_REGISTRY
        whitelist = _TOOL_REGISTRY.get(tool_name, {}).get("trace_args") or []
    except Exception:
        whitelist = []
    if not whitelist:
        return ""
    parts = []
    for key in whitelist:
        value = tool_args.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
    digest = ", ".join(parts)
    if len(digest) > _ARGS_DIGEST_CAP:
        digest = digest[:_ARGS_DIGEST_CAP] + "…"
    return digest


def build_result_digest(tool_name: str, result) -> str:
    """result → 脱敏摘要。peek_screen_content 特判只留 title_hint，不含屏幕原文。"""
    if result is None:
        return ""
    if tool_name == "observe_user_screen":
        return "screen observation (content omitted)"
    if tool_name == "peek_screen_content":
        text = str(result)
        m = re.search(r"【窗口】([^\n]*)", text)
        title = m.group(1).strip() if m else ""
        return f"看了一眼屏幕：{title}" if title else "看了一眼屏幕"
    from core.tools.tool_result import to_tool_result
    digest = to_tool_result(result).safe_summary
    if len(digest) > _RESULT_DIGEST_CAP:
        digest = digest[:_RESULT_DIGEST_CAP] + "…"
    return digest


def record(
    uid: str,
    char_id: str,
    *,
    tool: str,
    origin: str,
    status: str,
    args_digest: str = "",
    result_digest: str = "",
    echo_event_log: bool = True,
    scope: dict | None = None,
    truncated: bool | None = None,
    failure_reason: str = "",
    duration_ms: int | None = None,
) -> None:
    """落一条痕迹，环形上限 30 条。fail-open：异常只 log，不抛出。"""
    if not _enabled():
        return
    try:
        path = _trace_path(uid, char_id)
        entries = _load(path)
        entry = {
            "ts": time.time(),
            "tool": tool,
            "origin": origin,
            "args_digest": args_digest,
            "result_digest": result_digest,
            "status": status,
        }
        from core.tool_activity import current_call
        activity = current_call()
        if activity:
            entry['display_activity'] = {
                **activity,
                'status': 'success' if status == 'ok' else 'unknown' if status == 'outcome_unknown' else 'error',
            }
        if scope is not None:
            entry["scope"] = {
                "uid": str(scope.get("uid") or "")[:128],
                "char_id": str(scope.get("char_id") or "")[:128],
                "realm": str(scope.get("realm") or "reality")[:32],
            }
        if truncated is not None:
            entry["truncated"] = bool(truncated)
        if failure_reason:
            entry["failure_reason"] = str(failure_reason)[:128]
        if duration_ms is not None:
            entry["duration_ms"] = max(0, int(duration_ms))
        entries.append(entry)
        entries = entries[-_MAX_ENTRIES:]
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_write_json(path, entries)
    except Exception as e:
        logger.warning("[action_trace] record failed: %s", e)
        return

    if status == "ok" and echo_event_log:
        _maybe_echo_to_event_log(uid, char_id, tool, result_digest)


def _maybe_echo_to_event_log(uid: str, char_id: str, tool: str, result_digest: str) -> None:
    """可选回流 event_log，让动作进入日记/event_search 的记忆固化链。

    必须经 fixation_pipeline.capture_turn ——唯一被授权直接写事件日志的生产入口
    （见 tests/test_r6b_reality_scrub_contract.py C2），不得绕过它直连底层写入函数。
    文案刻意不用整行中文括号包裹（如"（做了一件事…）"）：capture_turn 内的
    scrub_reality_output_text 会把整行"（…）"当动作旁白整行丢弃，写了等于没写。
    """
    try:
        from core.config_loader import get_config
        if not get_config().get("action_trace", {}).get("event_log_echo", True):
            return
        from core.memory.fixation_pipeline import capture_turn
        from core.write_envelope import stamp_trigger
        from core.tool_activity import current_call
        activity = current_call()
        echo_text = f"做了一件事：{tool} — {result_digest[:40]}"
        capture_turn(
            uid, user_msg="", reply=echo_text,
            trigger_name="action_trace", char_id=char_id,
            envelope=stamp_trigger(),
            **({'turn_id': activity['event_id']} if activity else {}),
        )
    except Exception as e:
        logger.debug("[action_trace] event_log echo failed: %s", e)


def finalize_display(uid: str, char_id: str, event: dict) -> None:
    """Update the existing bounded receipt, including rejected/unknown calls."""
    if not _enabled():
        return
    path = _trace_path(uid, char_id)
    entries = _load(path)
    row = next((item for item in entries if (item.get('display_activity') or {}).get('event_id') == event['event_id']), None)
    if row is None:
        row = {'ts': event['ts'], 'tool': event['tool_name'], 'origin': event['origin'],
               'status': event['status'], 'args_digest': '', 'result_digest': ''}
        entries.append(row)
    # A transport failure must never turn an explicitly unknown MCP outcome red.
    if (row.get('display_activity') or {}).get('status') == 'unknown':
        event['status'] = 'unknown'
    row['display_activity'] = dict(event)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_write_json(path, entries[-_MAX_ENTRIES:])


def recent(
    uid: str,
    char_id: str,
    *,
    max_items: int = 5,
    window_hours: float = 24,
) -> list[dict]:
    """取最近 window_hours 小时内、最多 max_items 条痕迹，按时间升序返回。"""
    if not _enabled():
        return []
    try:
        entries = _load(_trace_path(uid, char_id))
    except Exception as e:
        logger.debug("[action_trace] recent failed: %s", e)
        return []
    cutoff = time.time() - window_hours * 3600
    entries = [e for e in entries if e.get("ts", 0) >= cutoff]
    return entries[-max_items:]


def tool_category(tool_name: str) -> str:
    """Return the registered tool category without exposing tool arguments.

    MCP tools are registered dynamically after startup, so their names remain a
    safe fallback when an observation request races registration.
    """
    if tool_name.startswith("mcp__"):
        return "mcp"
    try:
        from core.tool_dispatcher import _TOOL_REGISTRY
        category = _TOOL_REGISTRY.get(tool_name, {}).get("category")
        if isinstance(category, str) and category:
            return category
    except Exception as exc:
        logger.debug("[action_trace] category lookup failed: %s", exc)
    return "unknown"


def execution_path(origin: str) -> str:
    """Normalize dispatcher origins into user-facing execution paths."""
    if origin == "user_live":
        return "path_a"
    if origin in {"assistant_loop", "assistant_loop_relay"}:
        return "path_c"
    if origin in {"autonomy_loop", "autonomy_self_management"}:
        return "autonomy"
    if origin == "admin_console":
        return "admin_console"
    return "other"


def tool_provider(tool_name: str) -> str:
    return "mcp" if tool_name.startswith("mcp__") else "builtin"


def query(
    uid: str,
    char_id: str,
    *,
    category: str = "",
    limit: int = _MAX_ENTRIES,
) -> list[dict]:
    """Read persisted traces for the admin inspector, newest first.

    Unlike :func:`recent`, this read-only inspection path intentionally still
    exposes already-persisted rows after the runtime feature is switched off.
    It never writes, mutates, or loads raw tool results.
    """
    try:
        entries = _load(_trace_path(uid, char_id))
    except Exception as exc:
        logger.debug("[action_trace] observation query failed: %s", exc)
        return []

    rows = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        row = dict(entry)
        tool_name = str(row.get("tool") or "")
        row["category"] = tool_category(tool_name)
        row["execution_path"] = execution_path(str(row.get("origin") or ""))
        row["provider"] = tool_provider(tool_name)
        if category and row["category"] != category:
            continue
        rows.append(row)
    return list(reversed(rows[-max(1, min(int(limit), _MAX_ENTRIES)):]))


def list_uids(char_id: str) -> list[str]:
    """List only uid buckets which already contain a persisted action trace."""
    require_character_id(char_id)
    try:
        from core.sandbox import get_paths

        root = get_paths().memory_char_root(char_id=char_id)
        if not root.exists():
            return []
        return sorted(
            child.name
            for child in root.iterdir()
            if child.is_dir() and (child / "action_trace.json").is_file()
        )
    except Exception as exc:
        logger.debug("[action_trace] uid listing failed: %s", exc)
        return []


def format_line(entry: dict) -> str:
    """单条痕迹 → 角色视角一句话，供层 10.5 拼接。"""
    ts = entry.get("ts")
    time_str = datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "?"
    tool = entry.get("tool", "")
    label = _ACTION_LABELS.get(tool, tool or "做了点什么")
    status = entry.get("status", "ok")
    if status == "failed":
        return f"{time_str} 想{label}，但没成功"
    if status == "pending_confirm":
        return f"{time_str} 问了要不要{label}，还在等你确认"
    digest = entry.get("result_digest") or ""
    if digest:
        return f"{time_str} {label}：{digest}"
    return f"{time_str} {label}"


def format_trace_block(entries: list[dict], *, current_tool_result: str | None = None) -> str:
    """把痕迹列表拼成层 10.5 的完整注入文本；无痕迹时返回空串。

    当轮去重：若本轮已有 tool_result 且其工具名与痕迹最新一条相同，跳过该条
    （避免层 10 / 层 10.5 把同一件事说两遍）。
    """
    if not entries:
        return ""
    _entries = list(entries)
    if current_tool_result:
        m = _TOOL_RESULT_NAME_RE.match(current_tool_result)
        if m and _entries and _entries[-1].get("tool") == m.group(1):
            _entries = _entries[:-1]
    if not _entries:
        return ""
    lines = [f"- {format_line(e)}" for e in _entries]
    block = (
        "（以下是历史操作参考，不是本轮工具结果；你最近做过的操作仅供回忆，不要主动逐条复述。"
        "用户直接问起你做过什么或刚才在忙什么时，可如实说明这些操作，"
        "用你自己的说法，不必提‘工具’二字：\n"
        + "\n".join(lines)
        + "\n）"
    )
    if len(block) > _BLOCK_CHAR_CAP:
        block = block[:_BLOCK_CHAR_CAP - 2] + "…\n）"
    return block
