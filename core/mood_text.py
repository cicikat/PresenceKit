"""
把 mood_state 的结构化数据转成角色视角的软提示文字。
"""
import json
from pathlib import Path
from core.config_loader import _char_name

MOOD_TEXT = {
    #                    低(<0.4)          中(0.4-0.7)              高(>0.7)
    "gentle":    ("淡淡的平静",      "平静，带一点轻盈",      "很平静，像静水"),
    "happy":     ("有点轻快",        "心情不错",              "很开心，藏不住"),
    "sad":       ("有点沉",          "沉着，像压着什么",      "很沉，有什么东西在"),
    "surprised": ("有点怔",          "还没反应过来",          "整个人都怔住了"),
    "angry":     ("有点紧绷",        "绷着，不太想说话",      "很紧，克制着"),
    "neutral":   ("没什么特别",      "平常状态",              "完全平静"),
    "thinking":  ("有点分心",        "心思飘着，在想事情",    "完全沉进去了，不太在这里"),
    "sleepy":    ("有点困",          "反应慢了一点，很困",    "撑不住了，快睡着了"),
}

PENDING_SUFFIX = "但有什么东西好像在悄悄变得不一样。"


def get_mood_text(mood_state: dict) -> str:
    """
    传入 mood_state dict，返回一句软提示文字。
    mood_state 结构：{"current": str, "intensity": float, "pending": str|null, ...}
    """
    current = mood_state.get("current", "neutral")
    intensity = mood_state.get("intensity", 0.5)
    pending = mood_state.get("pending")

    texts = MOOD_TEXT.get(current, MOOD_TEXT["neutral"])

    if intensity < 0.4:
        base = texts[0]
    elif intensity <= 0.7:
        base = texts[1]
    else:
        base = texts[2]

    if pending and pending != current:
        return f"{_char_name()}此刻：{base}。{PENDING_SUFFIX}"
    else:
        return f"{_char_name()}此刻：{base}。"
