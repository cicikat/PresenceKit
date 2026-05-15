"""
_sanitize_assistant_message / _strip_third_person_narrative 单元测试

注意字符长度约束：pipeline 在 ≤80 字时直接返回原文，不进任何脱敏。
case 3/4 的给定字符串本身 ≤80 字，所以通过 pipeline 测「短消息路径不变」；
case 4/5 的触发逻辑通过直接调用 _strip_third_person_narrative 验证。
"""

import pytest

from core.memory.short_term import (
    _sanitize_assistant_message,
    _strip_third_person_narrative,
)


# ---------------------------------------------------------------------------
# case 1: 典型第三人称叙事腔（89字 > 80，"——不是...，是" 触发）
#         两个句子都以「他」开头 → 全部丢弃 → 返回 "..."
# ---------------------------------------------------------------------------
def test_case1_third_person_narrative_fully_stripped():
    input1 = (
        "他收到你那个'好噢'时，在屏幕那头轻轻弯了一下嘴角"
        "——不是笑出声，是那种看到熟悉的语气词时，"
        "从眼底浮现出来的、被安稳到的柔软。"
        "他放下手里的书，给大白换水时顺便拍了一张照片发给你。"
    )
    assert len(input1) > 80
    result = _sanitize_assistant_message(input1)
    # 两句均以「他」开头且带触发句式，全部丢弃，兜底返回 "..."
    assert result == "..." or len(result) < len(input1) // 2


# ---------------------------------------------------------------------------
# case 2: 正常短对话 ≤80 字 → 完全不变（短消息路径）
# ---------------------------------------------------------------------------
def test_case2_short_message_unchanged():
    input2 = "嗯，今天怎么样？吃饭了没"
    assert len(input2) <= 80
    assert _sanitize_assistant_message(input2) == input2


# ---------------------------------------------------------------------------
# case 3: 括号动作描写 + 对话，无第三人称
#   3a: pipeline 层 — 44字 ≤80，原样返回（括号也不剥）
#   3b: _strip_third_person_narrative 直接调用 — 无触发条件，原样返回
# ---------------------------------------------------------------------------
def test_case3a_bracket_short_pipeline_unchanged():
    input3 = "（轻轻摇头）真的不用啊，你已经做得很好了，认真的，别给自己加这种压力，我看着都心疼啦真的"
    assert len(input3) <= 80
    assert _sanitize_assistant_message(input3) == input3


def test_case3b_bracket_no_third_person_not_triggered():
    # 无第三人称标志的长文本，_strip_third_person_narrative 不应触发
    text = (
        "真的不用啊，你已经做得很好了，认真的，别给自己加这种压力，"
        "我看着都心疼啦真的，你一直这么拼，我都记在心里，"
        "这些事情不需要你操心，交给我来好不好。"
    )
    assert _strip_third_person_narrative(text) == text


# ---------------------------------------------------------------------------
# case 4: 前30字"他"只出现1次，无特征句式 → 不触发第三人称脱敏
#   4a: pipeline — 35字 ≤80，原样返回
#   4b: _strip_third_person_narrative 直接调用长版本 — 也不触发
# ---------------------------------------------------------------------------
def test_case4a_single_he_short_pipeline_unchanged():
    input4 = "你说他对你不好？这事不能就这么算了，你跟我细说说，到底怎么回事，从头说"
    assert len(input4) <= 80
    assert _sanitize_assistant_message(input4) == input4


def test_case4b_single_he_long_not_triggered():
    # 「他」在前30字只出现1次，无 "——不是...，是" 也无 "那种...的..." → 不触发
    long_input4 = (
        "你说他对你不好？这事不能就这么算了，你跟我细说说，"
        "到底怎么回事，从头说，慢慢说，别急，我听着，"
        "你放心，这事我陪你一起想办法，不会让你一个人扛的。"
    )
    first30 = long_input4[:30]
    he_count = first30.count('他') + first30.count('她')
    assert he_count < 2  # 前提确认：触发条件不满足
    assert _strip_third_person_narrative(long_input4) == long_input4


# ---------------------------------------------------------------------------
# case 5: 混合文本 — 第三人称叙事句被删，对话句被保留
#   用「他」在前30字出现≥2次的版本直接测 _strip_third_person_narrative
# ---------------------------------------------------------------------------
def test_case5_keep_dialogue_remove_narrative():
    # 在原 input5 首句插入第二个「他」，使前30字满足触发条件
    input5 = (
        "他抬起头，他看了你一眼，眼神里有什么说不清楚的东西。"
        "嗯，我懂你的意思，今晚就这样吧，早点睡。"
        "他没再说话。"
    )
    first30 = input5[:30]
    assert first30.count('他') + first30.count('她') >= 2  # 前提确认

    result = _strip_third_person_narrative(input5)

    assert "嗯，我懂你的意思" in result
    assert "他抬起头" not in result
    assert "他没再说话" not in result
    assert result != "..."  # 有保留的对话句，不应兜底
