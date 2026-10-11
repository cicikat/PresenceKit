"""
把 mood_state 的结构化数据转成角色视角的软提示文字。
"""
import json
import time
from pathlib import Path
from core.config_loader import _char_name

def get_mood_text(mood_state: dict, *, subject: str | None = None) -> str:
    """
    传入 mood_state dict，返回一句软提示文字。
    mood_state 结构：{"current": str, "intensity": float, "pending": str|null,
                     "previous": str|null, "updated_at": float, ...}
    """
    if not mood_state.get("has_signal") or not mood_state.get("source_turn_id"):
        return ""
    if time.time() >= float(mood_state.get("expires_at") or 0):
        return ""
    return (f"刚才回合 {mood_state['source_turn_id']} 明确表达了 {mood_state['current']}，"
            f"这是可改变的短时余韵，截止 {mood_state['expires_at']}；以本轮真实表达为准。")
