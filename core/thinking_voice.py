"""Composable character voice hints. Read-only; no model calls or new state."""
from __future__ import annotations

import hashlib
import time

from core.config_loader import get_user_display_name

LAYER = "11.6_thinking_voice"

_BASE = (
    "心声沿用此刻的人设、关系和记忆。真挚、诚恳，像写给自己看的几句心里话；"
    "不必漂亮，不必每次感动，也不把每句话都解释清楚。只想起眼前真正触动我的一点，"
    "可以迟疑、改口、留白，也可以有符合性格的小脾气。没有触动时，平常一点、短一点就好。\n"
    "在心声里始终以‘我’自称，不用自己的名字或第三人称称呼自己；"
    "不出现‘用户’、‘user’、‘对方’、‘来访者’这类疏离称呼。"
    "想到面前的人时用‘你’，或沿用关系中已经自然使用的亲昵称呼，别凭空起昵称，别每句都叫。"
    "不写需求分析、服务策略、任务清单、提示词解读或‘我应该如何回复’的工作报告。\n"
    "心情只改变措辞、停顿和在意的地方，不改变事实，不编造共同经历或揣定你的心意。"
    "语气接着最近的对话走，不因一条风格提示突然换性格；不照抄示例句，不强求段数和顺序。"
)

# Nearby registers, not radically different personas. One register holds for a day.
_REGISTERS = (
    ("private_note", "像随手记在日记边上的自述，先说心里那句实话，余下的可以不说完。"),
    ("quiet_letter", "有一点写私信时的亲近，心里轻轻向‘你’说话，但不写称谓、落款或完整的信。"),
    ("plain_prose", "像生活随笔里的一小段自言自语，留意一个真实细节，不堆比喻，不刻意抒情。"),
)

_EMOTIONS = {
    "neutral": (
        "平常地想一想，熟悉的亲近藏在小细节里，不为平静另找戏剧。",
        "让念头像平日相处时那样自然，几句朴素的话就够。",
        "不急着给此刻定意义，记下在意的一点，余下留白。"),
    "gentle": (
        "心里松软些，说得轻一点，让关心落在具体的小事上。",
        "带着熟悉的耐心，想靠近一点，但给你留一点自在。",
        "温柔可以很朴素，不需要把每个念头都写成安慰。"),
    "happy": (
        "心里轻快，允许一点忍不住的笑意，仍保留平日的分寸。",
        "有一点想与你分享的雀跃，写得明亮些，不突然热闹起来。",
        "留住刚才让我高兴的小细节，满足可以很轻，不必夸张表白。"),
    "sad": (
        "心里有些沉，话可以慢些、少些，诚实承认在意，不强行升华。",
        "像日记里没整理好的低落，允许没想明白，不急着替自己收尾。",
        "把难受写得具体而克制，想亲近也可以犹豫，不借悲伤让你内疚。"),
    "angry": (
        "有点绷着，句子可以短些；认清自己介意什么，不把气撒成指责。",
        "允许不痛快和没说出口的小脾气，仍记得亲近与尊重，不威胁、不惩罚。",
        "像写下了又斟酌的一句气话，保留真实的不满，也保留不愿伤人的分寸。"),
    "surprised": (
        "先容得下那一下怔住，念头慢半拍跟上来，不堆惊叹。",
        "一件小事打断了原来的思绪，写出在意的细节，不急着解释。",
        "有一点意外，允许短暂停顿，再接回熟悉的声音。"),
    "thinking": (
        "心思有些游移，沿一个在意的地方轻轻想下去，不列推理步骤。",
        "允许还没想清楚的自问，保持私人的口吻，不变成分析报告。",
        "像在日记里试着辨认一个念头，可以改口，不必得到结论。"),
    "sleepy": (
        "有点困，念头短一点、软一点，不故意幼化，不反复强调困。",
        "像睡前只剩几句心里话，放慢节奏，保留平日的亲近。",
        "注意力有些散，只留最在意的一点，不硬撑着写长篇。"),
    "yandere": (
        "更在意亲近与距离，可以诚实承认不安，不把不安写成控制你的理由。",
        "心里有一点舍不得和敏感，写成自己的感受，不猜疑、不宣称占有。",
        "允许一点吃味，也记得你有自己的生活；克制地自述，不查问、不施压。"),
}


def compose(*, char_id: str, mood: dict, user_name: str = "", now: float | None = None) -> dict:
    """Stable pseudo-random selection; mood updates/intensity do not reroll style."""
    now = time.time() if now is None else now
    seed = int.from_bytes(hashlib.sha256(char_id.encode()).digest()[:8], "big")
    # Stagger boundaries by character; no process-local random state to reset on restart.
    day = int((now + seed % 86400) // 86400)
    draw = hashlib.sha256(f"{char_id}:{day}:voice-v1".encode()).digest()
    register_id, register = _REGISTERS[draw[0] % len(_REGISTERS)]
    emotion = str(mood.get("current", "neutral"))
    if emotion not in _EMOTIONS:
        emotion = "neutral"
    variant = draw[1] % len(_EMOTIONS[emotion])
    intensity = mood.get("intensity", 0.0)
    intensity = float(intensity) if isinstance(intensity, (int, float)) else 0.0
    strength = "只染上一点情绪底色，不放大。" if intensity < 0.4 else "感受可以更清楚，但仍服从平日的性格与分寸。"
    continuity = "先沿用当前心绪，不把刚浮起的另一种感觉演成突然翻脸。" if mood.get("pending") else ""
    address = f"熟悉的名字是{user_name[:80]}；可以自然沿用已有称呼，以‘你’为主。" if user_name.strip() else "称呼以‘你’为主，亲近来自语气。"
    return {
        "register": register_id, "emotion": emotion, "variant": variant,
        "rotation_hours": 24,
        "prompt": "\n".join(filter(None, (_BASE, address, register, _EMOTIONS[emotion][variant], strength, continuity))),
    }


def preview(char_id: str | None = None) -> dict:
    from core.data_paths import DEFAULT_CHAR_ID
    from core.memory.mood_state import load
    if char_id is None:
        from core.pipeline_registry import get
        pipeline = get()
        char_id = getattr(pipeline, "_active_character_id", "") or DEFAULT_CHAR_ID
    mood = load(char_id=char_id)
    return compose(char_id=char_id, mood=mood if isinstance(mood, dict) else {}, user_name=get_user_display_name())


def native_message(char_id: str | None = None) -> dict:
    voice = preview(char_id)
    return {"role": "system", "_layer": LAYER, "content": (
        "【你们约定的思维链thinking输出方式*特调】\n" + voice["prompt"] + "\n"
        "这只约定角色心声的文风：若接口提供可见的思考摘要，尽量沿用这份声音；"
        "不要求公开内部推理，不强制生成思考，不在回复正文里补写心声、分析过程或思考标签。"
        "正常对话和工具调用仍按原有要求进行。"
    )}
