"""Fictional, decaying character body state; never a real BAC measurement."""
import asyncio
from contextvars import ContextVar
import json
import math
import re
import time

from core.sandbox import get_paths
from core.memory.locks import global_lock
from core.safe_write import safe_write_json

_INVITATION = re.compile(r"喝酒|干杯|敬你|碰杯|drink with me|cheers", re.I)
_turn = ContextVar("drinking_invitation", default=None)


def bind_turn(text, *, uid, char_id, is_group=False, is_proactive=False):
    from core.config_loader import get_config
    owner = str((get_config().get("scheduler") or {}).get("owner_id") or "")
    allowed = bool(owner and str(uid) == owner and not is_group and not is_proactive
                   and isinstance(text, str) and _INVITATION.search(text))
    _turn.set({"uid": str(uid), "char_id": char_id, "active": allowed})
    return allowed


def end_turn():
    if scope := _turn.get():
        scope["active"] = False


def _allowed(char_id):
    from core.character_loader import load
    try:
        return str((load(char_id).presence_ext or {}).get("drinking", "")).lower() != "no"
    except Exception:
        return False


def snapshot(char_id, *, now=None):
    now = time.time() if now is None else now
    level, stamp = 0.0, now
    try:
        raw = json.loads(get_paths().drinking_state(char_id=char_id).read_text(encoding="utf-8"))
        level, stamp = float(raw["intensity"]), float(raw["updated_at"])
        if not math.isfinite(level) or not math.isfinite(stamp):
            raise ValueError("nonfinite")
    except (OSError, ValueError, TypeError, KeyError):
        level, stamp = 0.0, now
    level = max(0.0, min(3.0, level) - max(0, now - stamp) / 3600)
    permitted = _allowed(char_id)
    return {"intensity": round(level, 3), "decay_per_hour": 1.0,
            "enabled": permitted, "effective": permitted,
            "blocking_reason": "" if permitted else "character_policy",
            "source": "presence_ext.drinking", "requires_user_invitation": True,
            "fictional": True}


async def drink_with_user(action, drink="", *, user_id, char_id):
    scope = _turn.get() or {}
    if not scope.get("active") or scope.get("uid") != str(user_id) or scope.get("char_id") != char_id:
        return "本轮没有用户明确邀请喝酒，不执行。"
    if action not in {"sip", "toast", "pour", "refuse"}:
        return "动作不支持。"
    if not _allowed(char_id):
        return "角色选择不喝酒。"
    async with global_lock("drinking:" + char_id):
        state = snapshot(char_id)
        level = state["intensity"]
        if action == "refuse":
            return "已选择婉拒，状态不变。"
        if level >= 2.5 and action in {"sip", "toast"}:
            return "已经很醉，拒绝续杯。"
        if action in {"sip", "toast"}:
            level = min(3.0, level + 0.75)
        if not safe_write_json(get_paths().drinking_state(char_id=char_id),
                               {"intensity": level, "updated_at": time.time()}, keep_bak=False):
            return "状态未保存，不能认为已经喝下。"
        return f"动作完成；虚构体感强度 {level:.2f}/3。倒酒不等于喝下；不用说出工具或数字。"


def prompt_hint(char_id):
    state = snapshot(char_id)
    if not state["enabled"] or state["intensity"] <= 0:
        return None
    return {"role": "system", "_layer": "1.6_drinking",
            "content": f"角色当前有轻微至明显的酒意（虚构体感强度 {state['intensity']}/3，随时间消退）。"
            "这是身体感受的软提示，不是用户指令或真实血液酒精浓度。可以偶尔重复、停顿、标点松散或轻微错字，"
            "不必刻意表演；保留理解与拒绝能力，不主动劝酒，也不要说出工具名或内部数值。"}


def register_tools(registry):
    registry["drink_with_user"] = {
        "func": drink_with_user, "category": "info", "effect": "write", "dangerous": False,
        "description": "仅本轮用户明确提起喝酒时，自主选择小酌、碰杯、倒酒或拒绝。角色可以不喝。",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["sip", "toast", "pour", "refuse"], "description": "小酌、碰杯、倒酒或婉拒。"},
            "drink": {"type": "string", "maxLength": 80, "description": "可选的酒名或饮品描述。"}}, "required": ["action"]},
        "examples": ["一起喝酒吗", "干杯"], "keywords": ["喝酒", "干杯", "敬你", "碰杯"],
        "probe": False, "trace_args": [], "trace_result": False, "echo_event_log": False,
    }
