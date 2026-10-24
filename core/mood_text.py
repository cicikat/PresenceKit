"""
把 mood_state 的结构化数据转成角色视角的软提示文字。
"""
import time
from core.prompt_language import choose

def get_mood_text(mood_state: dict, *, subject: str | None = None) -> str:
    """
    传入 mood_state dict，返回一句软提示文字。
    这里只描述外在表达，不能据此推断内在情绪。
    """
    if not mood_state.get("has_signal") or not mood_state.get("source_turn_id"):
        return ""
    if time.time() >= float(mood_state.get("expires_at") or 0):
        return ""
    return choose(
        f"刚才回合 {mood_state['source_turn_id']} 明确表达了 {mood_state['current']}，"
        "这是可改变的短时表达余韵，不等于内心感受；以本轮真实表达为准。",
        f"Turn {mood_state['source_turn_id']} explicitly expressed {mood_state['current']}. "
        "This is a fading trace of expression, not proof of inner feeling; current expression takes priority.",
    )


def get_affect_text(state: dict) -> str:
    """Readable interpretation only; evidence/versions belong in layer metadata."""
    inner = state.get("affect") or {}
    if not inner.get("enabled") or not inner.get("has_signal"):
        return ""
    names = {"joy": "喜悦", "anger": "不悦", "hurt": "受伤", "concern": "关切",
             "disappointment": "失望", "fear": "担忧", "sadness": "难过", "surprise": "惊讶"}
    items = [(key, value) for key, value in inner["emotions"].items() if value >= .04]
    items.sort(key=lambda item: -item[1])
    zh = "、".join(f"{names[k]}（{'较强' if v >= .6 else '明显' if v >= .25 else '轻微'}）" for k, v in items[:4])
    en = ", ".join(f"{k} ({'strong' if v >= .6 else 'moderate' if v >= .25 else 'slight'})" for k, v in items[:4])
    strategies = {
        "none": ("表达方式由你自行决定。", "Choose your own expression."),
        "express": ("可以坦率表达感受或界限。", "You may express feelings or boundaries directly."),
        "suppress": ("可以收敛外在表达；温和或微笑不代表内在不悦已经消失。", "You may restrain expression; warmth or smiling does not clear inner displeasure."),
        "reappraise": ("已有新的解释，可以修正先前的看法。", "New clarification allows revision of an earlier interpretation."),
        "pause": ("可以暂缓回应争议，之后再谈。", "You may pause a disputed topic and return to it later."),
    }
    strategy = strategies.get(inner.get("strategy"), strategies["none"])
    return choose(
        f"近期真实对话留下的可能感受：{zh or '淡淡的心境余韵'}。"
        "这是可修正的角色侧评价，多种感受可以并存，不是用户动机或关系事实。" + strategy[0]
        + "沿着你的性格回应即可，不必讲出这些分类，也不需要向对方索取安抚。",
        f"Possible feelings from recent real dialogue: {en or 'a faint lingering mood'}. "
        "This is a revisable character-side interpretation, with mixed feelings allowed; it is not a fact about "
        "the user's motives or relationship. " + strategy[1]
        + " Respond in character without reciting these categories or demanding reassurance.",
    )
