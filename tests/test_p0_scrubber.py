"""
tests/test_p0_scrubber.py — P0-2 验收

断言覆盖：
- 回复含整行 '用户：…' → 该行被删，其余正文保留
- 回复含整行 '叶瑄：…' → 该行被删
- 带 **...** 粗体的说话人标签整行 → 被删
- '用户名叫小明' / '我喜欢你'（无冒号紧跟标签）→ 不误删
- 二次 scrub 结果幂等
"""

import pytest


@pytest.fixture(autouse=True)
def patch_char_name(monkeypatch):
    import core.config_loader as cl
    monkeypatch.setattr(cl, "_char_name", lambda: "叶瑄")


def _scrub(text: str):
    from core.reality_output_scrubber import scrub_reality_output_text
    return scrub_reality_output_text(text)


# ── 拦截内嵌说话人标签 ────────────────────────────────────────────────────────

def test_drops_user_speaker_label_line():
    """整行以 '用户：' 开头 → 该行被删，其余正文保留。"""
    text = "我知道了。\n用户：我没有这么写\n但我真的想过。"
    result = _scrub(text)
    assert result is not None
    assert "用户：我没有这么写" not in result
    assert "我知道了" in result
    assert "真的想过" in result


def test_drops_char_name_speaker_label_line():
    """整行以 '{char_name}：' 开头 → 该行被删。"""
    text = "嗯。\n叶瑄：这不是真实台词\n好的。"
    result = _scrub(text)
    assert result is not None
    assert "叶瑄：这不是真实台词" not in result
    assert "嗯" in result
    assert "好的" in result


def test_drops_bold_user_label_line():
    """带 **用户** 粗体的说话人标签行也被删。"""
    text = "继续说。\n**用户**：这是内嵌角色扮演\n然后我说。"
    result = _scrub(text)
    assert result is not None
    assert "内嵌角色扮演" not in result
    assert "继续说" in result
    assert "然后我说" in result


def test_drops_bold_char_name_label_line():
    """带 **叶瑄** 粗体的说话人标签行也被删。"""
    text = "先这样。\n**叶瑄**：其实我早就知道了\n是这样的。"
    result = _scrub(text)
    assert result is not None
    assert "早就知道了" not in result
    assert "先这样" in result
    assert "是这样的" in result


def test_drops_label_with_fullwidth_colon():
    """全角冒号 '：' 同样被拦截。"""
    text = "嗯嗯。\n用户：你好吗\n没事。"
    result = _scrub(text)
    assert result is not None
    assert "你好吗" not in result


# ── 不误删正常台词 ─────────────────────────────────────────────────────────────

def test_does_not_drop_user_mention_without_colon():
    """'用户名叫小明'（无冒号紧跟标签）不误删。"""
    text = "用户名叫小明。\n我喜欢你。"
    result = _scrub(text)
    assert result is not None
    assert "用户名叫小明" in result
    assert "我喜欢你" in result


def test_does_not_drop_sentence_containing_user():
    """句子中间出现"用户"二字，但非行首标签格式，不误删。"""
    text = "今天有一个用户问了我一个问题。"
    result = _scrub(text)
    assert result is not None
    assert "用户问了我" in result


def test_does_not_drop_normal_content():
    """普通对话无任何误删。"""
    text = "那先放着。\n休息一下换个脑子再回来。"
    result = _scrub(text)
    assert result is not None
    assert "那先放着" in result
    assert "休息一下" in result


# ── 幂等 ──────────────────────────────────────────────────────────────────────

def test_idempotent():
    """对 scrub 结果再次 scrub，输出不变。"""
    text = "我知道了。\n用户：你好\n再见。"
    r1 = _scrub(text)
    r2 = _scrub(r1) if r1 else r1
    assert r1 == r2


def test_all_dropped_returns_none():
    """如果所有行都被删，返回 None（调用方负责 fallback）。"""
    text = "用户：我没有这么说"
    result = _scrub(text)
    assert result is None
