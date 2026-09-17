"""ProactiveLedger — 所有主动发言的最后一道闸 + 唯一记账点（CC 任务 19 · B）。

决策权威仍是 `gating._decide()`；ledger 是它查询的数据源，两层不冲突：
  - `gating._decide()` 在挑 winner 前用 `can_send()` 过滤候选 —— 只读，不记账。
  - 真正发送成功后，各发言出口（`execution.execute_prompt()` / `sensor_aware.handle_tick()`
    / `manual_trigger` / watch emergency）调用 `record_send()` 记账。
  - 角色冷却（`loop._mark(name, char_id=)`）互不抑制；本 ledger 仍按 uid 做跨触发器
    gap / 日预算 / 未回复上限，这是保留的必要全局限流，不是已删除的全局 `_mark(name)` 双写。

接管原先分散在 `loop.py`（`_next_proactive_ts` 全局间隔 + jitter 一次性采样，A2）和
执行出口里的状态，新增当日发送预算
（`scheduler.max_daily_proactive`，默认 8，emergency 豁免但仍记账）。

RC5 修复：此前 `_mark_global_proactive()` 只在 `execution.execute_prompt()` 成功后调用，
sensor_aware / manual_trigger 等出口完全不记账，导致 gating 看到的
"上次主动时间" 是残缺的。这里把所有已知发言出口统一收口到同一账本。
这是唯一的主动发言运行时账本。
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime

from core.sandbox import get_paths

logger = logging.getLogger(__name__)

_NEXT_ALLOWED_KEY = "next_allowed_ts"
_DAILY_COUNT_KEY = "daily_count"
_DAILY_DAY_KEY = "daily_logical_day"
_RECENT_KEY = "recent"
_CONTINUITY_KEY = "continuity_by_uid"

_state: dict = {
    "next_allowed_ts": 0.0,
    "daily_count": 0,
    "daily_logical_day": "",
    "recent": [],  # [{"trigger_name", "gist", "ts", "channel"}], 最近 3 条
    "continuity_by_uid": {},  # uid -> {last_proactive_sent_at,last_user_message_at,user_turn_seq,consecutive_unanswered_talks,last_char_id}
}
_loaded = False
_loaded_path_token: str | None = None


def _cfg() -> dict:
    from core.config_loader import get_config
    return get_config().get("scheduler", {})


def _gap_seconds() -> float:
    return float(_cfg().get("global_proactive_min_gap_seconds", 90 * 60))


def _daily_budget() -> int:
    return int(_cfg().get("max_daily_proactive", 8))


def _logical_day_str(now_ts: float | None = None) -> str:
    from core.scheduler.rhythm import logical_day
    now_ts = time.time() if now_ts is None else now_ts
    return logical_day(datetime.fromtimestamp(now_ts)).isoformat()


def _load() -> None:
    """惰性加载，进程内只读一次磁盘；写入均走 _persist() 保持内存与磁盘一致。"""
    global _loaded, _loaded_path_token, _state
    token = str(get_paths().root_dir().resolve())
    if _loaded and (_loaded_path_token is None or token == _loaded_path_token):
        return
    _state = {
        "next_allowed_ts": 0.0,
        "daily_count": 0,
        "daily_logical_day": "",
        "recent": [],
        "continuity_by_uid": {},
    }
    _loaded = True
    _loaded_path_token = token
    try:
        import json
        p = get_paths().proactive_ledger()
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            _state["next_allowed_ts"] = float(d.get(_NEXT_ALLOWED_KEY, 0) or 0)
            _state["daily_count"] = int(d.get(_DAILY_COUNT_KEY, 0) or 0)
            _state["daily_logical_day"] = str(d.get(_DAILY_DAY_KEY, "") or "")
            recent = d.get(_RECENT_KEY, [])
            _state["recent"] = recent if isinstance(recent, list) else []
            continuity = d.get(_CONTINUITY_KEY, {})
            _state["continuity_by_uid"] = continuity if isinstance(continuity, dict) else {}
    except Exception as e:
        logger.warning("[proactive_ledger] 状态读取失败，使用默认空状态: %s", e)


def _persist() -> None:
    global _loaded_path_token
    try:
        from core.safe_write import safe_write_json
        p = get_paths().proactive_ledger()
        _loaded_path_token = str(get_paths().root_dir().resolve())
        p.parent.mkdir(parents=True, exist_ok=True)
        safe_write_json(p, {
            _NEXT_ALLOWED_KEY: _state["next_allowed_ts"],
            _DAILY_COUNT_KEY: _state["daily_count"],
            _DAILY_DAY_KEY: _state["daily_logical_day"],
            _RECENT_KEY: _state["recent"],
            _CONTINUITY_KEY: _state["continuity_by_uid"],
        })
    except Exception as e:
        logger.warning("[proactive_ledger] 状态写入失败: %s", e)


def _roll_daily_if_needed(now_ts: float) -> None:
    today = _logical_day_str(now_ts)
    if _state["daily_logical_day"] != today:
        _state["daily_logical_day"] = today
        _state["daily_count"] = 0


def can_send(trigger_name: str, *, priority: str = "normal", uid: str = "") -> tuple[bool, str]:
    """只读检查：不修改任何状态，供 gating._decide() 过滤候选。

    emergency 优先级恒返回 True（间隔/预算均豁免），但仍需在真正发送后调用
    record_send() 记账 —— 豁免的是"能不能发"，不是"要不要记"。
    """
    _load()
    owner_uid = str(uid or _default_owner_uid())
    if owner_uid and int(_continuity(owner_uid).get("consecutive_unanswered_talks") or 0) >= 2:
        return False, "unanswered_cap"
    if priority == "emergency":
        return True, "emergency_exempt"
    now = time.time()
    if now < _state["next_allowed_ts"]:
        return False, "gap_not_elapsed"
    _roll_daily_if_needed(now)
    if _state["daily_count"] >= _daily_budget():
        return False, "daily_budget_exceeded"
    return True, "ok"


def _continuity(uid: str) -> dict:
    return _state["continuity_by_uid"].setdefault(str(uid), {
        "last_proactive_sent_at": 0.0,
        "last_user_message_at": 0.0,
        "user_turn_seq": 0,
        "consecutive_unanswered_talks": 0,
        "last_char_id": "",
    })


def _default_owner_uid() -> str:
    try:
        return str(_cfg().get("owner_id") or "")
    except Exception:
        return ""


def record_send(trigger_name: str, *, channel: str = "", gist: str = "", uid: str = "", char_id: str = "") -> None:
    """记账：写 next_allowed_ts（一次性 jitter 采样、只增不减，A2 语义）+ 当日计数 + 最近 gist。

    对所有已知主动发言出口都应调用，包括 emergency（can_send 豁免发送限制，但记账不豁免）。
    """
    _load()
    now = time.time()
    _roll_daily_if_needed(now)
    _state["daily_count"] += 1
    gap = _gap_seconds()
    _state["next_allowed_ts"] = now + gap + random.uniform(0, 0.2 * gap)
    if gist:
        _state["recent"].append({
            "trigger_name": trigger_name,
            "gist": gist[:40].strip(),
            "ts": now,
            "channel": channel,
        })
        _state["recent"] = _state["recent"][-3:]
    owner_uid = str(uid or _default_owner_uid())
    if owner_uid:
        continuity = _continuity(owner_uid)
        continuity["consecutive_unanswered_talks"] = int(continuity.get("consecutive_unanswered_talks") or 0) + 1
        continuity["last_proactive_sent_at"] = now
        continuity["last_char_id"] = str(char_id or continuity.get("last_char_id") or "")
    _persist()


def record_user_message(uid: str) -> int:
    """Only a real owner message clears continuity; sensors/tools/internal events never call this."""
    _load()
    continuity = _continuity(str(uid))
    now = time.time()
    try:
        user_turn_seq = int(continuity.get("user_turn_seq") or 0) + 1
    except (TypeError, ValueError):
        user_turn_seq = 1
    continuity["user_turn_seq"] = user_turn_seq
    continuity["last_user_message_at"] = now
    if now > float(continuity.get("last_proactive_sent_at") or 0):
        continuity["consecutive_unanswered_talks"] = 0
    _persist()
    return user_turn_seq


def continuity_status(uid: str) -> dict:
    _load()
    return dict(_continuity(str(uid)))


def continuity_hint() -> str:
    """B3 承接感：读最近一条 gist，生成"别重复上次话题"的软提示。fail-open：失败返回 ''。"""
    _load()
    try:
        recent = _state["recent"]
        if not recent:
            return ""
        last = recent[-1]
        gist = str(last.get("gist") or "").strip()
        ts = float(last.get("ts") or 0)
        if not gist or not ts:
            return ""
        mins_ago = max(1, int((time.time() - ts) / 60))
        return (
            f"（你上一次主动找她是在 {mins_ago} 分钟前，说的是「{gist}」。"
            "这次别重复那件事；可以自然承接它，或换一个新的由头开口，像真人那样有连贯的心思。）"
        )
    except Exception:
        return ""


def snapshot(uid: str = "") -> dict:
    """观测用：GET /scheduler/proactive-ledger 消费，管理面板"止血生效没有"一眼可查。"""
    _load()
    now = time.time()
    _roll_daily_if_needed(now)
    result = {
        "effective_gap_seconds": _gap_seconds(),
        "next_allowed_ts": _state["next_allowed_ts"],
        "next_allowed_in_seconds": max(0, round(_state["next_allowed_ts"] - now)),
        "daily_count": _state["daily_count"],
        "daily_budget": _daily_budget(),
        "daily_logical_day": _state["daily_logical_day"],
        "recent": list(_state["recent"]),
    }
    if uid or _default_owner_uid():
        result["continuity"] = continuity_status(str(uid or _default_owner_uid()))
    return result
