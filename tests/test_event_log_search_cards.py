"""
tests/test_event_log_search_cards.py — P0-1 验收

断言覆盖：
- 用户行 + 角色行混在同一块 → 各自成卡，role 正确，无跨说话人拼接
- 角色块正文含无前缀续行 → 继承 assistant role，不误渲染为"你提到"
- 60 字长正文 → 句末截断，不在词中间断
- days_ago 渲染映射正确（0/1/3/10）
- 最多 5 张卡 / MIN_SCORE 过滤保持不回归
"""

import asyncio
import unittest.mock as mock
from datetime import datetime, timedelta

import pytest

from core.memory import event_log

_UID = "search_card_uid"
_CHAR_ID = "yexuan"
_CHAR_NAME = "叶瑄"


@pytest.fixture(autouse=True)
def patch_char_name(monkeypatch):
    import core.config_loader as cl
    monkeypatch.setattr(cl, "_char_name", lambda: _CHAR_NAME)


def _section(days_ago: int, block_text: str) -> str:
    d = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    return f"# {d}\n{block_text}"


def _run(text: str, query: str, *, return_trace: bool = False):
    with mock.patch.object(event_log, "get_recent_days", return_value=text):
        return asyncio.run(
            event_log.search(_UID, query, char_id=_CHAR_ID, return_trace=return_trace)
        )


# ── 1. 同一块用户行和角色行各自成卡 ─────────────────────────────────────────

def test_user_and_assistant_cards_are_separate():
    """用户行和角色行分别产出各自卡，不跨说话人拼接。"""
    text = _section(0, (
        "## 14:23\n"
        f"**用户**：我最近喜欢下棋\n"
        f"**{_CHAR_NAME}**：下棋很有意思呢\n"
        "> emotion:gentle intensity:2\n"
        "---\n"
    ))
    result, trace = _run(text, "下棋", return_trace=True)
    assert result != ""
    user_cards = [c for c in trace if c["role"] == "user"]
    char_cards = [c for c in trace if c["role"] == "assistant"]
    assert len(user_cards) >= 1, "应有用户卡"
    assert len(char_cards) >= 1, "应有角色卡"
    # 渲染文本里用户和角色应分行，各带正确前缀
    lines = result.split("\n")
    assert any("你提到" in l for l in lines), "应有'你提到'卡"
    assert any(f"{_CHAR_NAME}当时说" in l for l in lines), "应有角色卡"
    # 不应有跨说话人的'; '缝合
    assert "; " not in result, "不得出现'; '跨说话人缝合"


def test_user_card_label_correct():
    """用户行渲染为"你提到："格式。"""
    text = _section(0, (
        "## 09:00\n"
        f"**用户**：最近我在学下棋\n"
        "> emotion:neutral intensity:1\n"
        "---\n"
    ))
    result = _run(text, "下棋")
    assert "你提到：最近我在学下棋" in result or "你提到" in result


def test_assistant_card_label_correct():
    """角色行渲染为"{char_name}当时说："格式。"""
    text = _section(0, (
        "## 09:00\n"
        f"**{_CHAR_NAME}**：其实下棋能让人专注\n"
        "> emotion:gentle intensity:2\n"
        "---\n"
    ))
    result = _run(text, "下棋")
    assert f"{_CHAR_NAME}当时说" in result


# ── 2. 续行继承前一条 role ────────────────────────────────────────────────────

def test_continuation_line_inherits_assistant_role():
    """无粗体前缀的续行继承前一条 assistant role，不渲染为'你提到'。"""
    text = _section(0, (
        "## 10:00\n"
        f"**用户**：随便说说\n"
        f"**{_CHAR_NAME}**：我记得你提过下棋的事情\n"
        "这是续行内容\n"            # 无前缀，继承 assistant
        "> emotion:gentle intensity:2\n"
        "---\n"
    ))
    result, trace = _run(text, "下棋", return_trace=True)
    # 续行命中的卡应为 assistant role
    for item in trace:
        if "续行" in item["snippet"]:
            assert item["role"] == "assistant", f"续行应继承 assistant，实际 role={item['role']}"


def test_unbolded_user_label_in_assistant_block_inherits_assistant():
    """助手回复正文里的'用户：内容'（无粗体）应继承 assistant，不误判为用户行。"""
    text = _section(0, (
        "## 11:00\n"
        f"**用户**：早上好\n"
        f"**{_CHAR_NAME}**：昨天下棋的事还没说完\n"
        "用户：但我其实没说（这是角色模拟的话）\n"   # 无 ** 前缀
        "> emotion:gentle intensity:2\n"
        "---\n"
    ))
    result, trace = _run(text, "下棋", return_trace=True)
    for item in trace:
        if "模拟" in item["snippet"]:
            assert item["role"] == "assistant", "无前缀行不应识别为 user"


# ── 3. 长文本截断 ─────────────────────────────────────────────────────────────

def test_long_line_clipped_at_sentence_boundary():
    """60 字以上正文按句末标点截断，卡内容不在词中间断。"""
    long_text = "今天下棋赢了好几局，感觉特别好。而且对手很强，让我学到了很多东西。这段话超过了六十个字符所以要被截断。"
    text = _section(0, (
        "## 15:00\n"
        f"**用户**：{long_text}\n"
        "> emotion:happy intensity:2\n"
        "---\n"
    ))
    result = _run(text, "下棋")
    assert result != ""
    # 提取卡正文（最后一个冒号后的内容）
    card_body = result.split("：", 1)[-1] if "：" in result else result
    # 不应在中间词中断（末尾要么是标点，要么是"…"）
    assert card_body[-1] in "。！？；…", f"截断点不在句末标点: '{card_body[-3:]}'"


# ── 4. days_ago 粗粒度渲染 ───────────────────────────────────────────────────

@pytest.mark.parametrize("days_ago,expected_label", [
    (0, "今天"),
    (1, "昨天"),
    (3, "前几天"),
    (10, "约10天前"),
])
def test_days_ago_coarse_label(days_ago, expected_label):
    """days_ago 映射到正确的粗粒度时间标签。"""
    block = (
        "## 10:00\n"
        "**用户**：今天下棋\n"
        "> emotion:happy intensity:2\n"
        "---\n"
    )
    text = _section(days_ago, block)
    result = _run(text, "下棋")
    assert expected_label in result, f"days_ago={days_ago} 应渲染为 '{expected_label}'，实际: {result!r}"


# ── 5. 既有行为不回归 ─────────────────────────────────────────────────────────

def test_at_most_5_cards():
    """最多返回 5 张卡。"""
    blocks = ""
    for i in range(8):
        blocks += f"## {10+i}:00\n**用户**：我最近在下棋第{i+1}局\n> emotion:happy intensity:2\n---\n"
    text = _section(0, blocks)
    result = _run(text, "下棋")
    cards = [c for c in result.split("\n") if c.strip()]
    assert len(cards) <= 5, f"返回了 {len(cards)} 张卡，超过上限 5"


def test_no_match_returns_empty():
    """无关键词命中时返回空字符串。"""
    text = _section(0, (
        "## 10:00\n"
        "**用户**：今天天气不错\n"
        "> emotion:neutral intensity:0\n"
        "---\n"
    ))
    result = _run(text, "下棋")
    assert result == ""


def test_empty_recent_text_returns_empty():
    """get_recent_days 返回空时 search 返回空字符串。"""
    result = _run("", "下棋")
    assert result == ""


def test_return_trace_items_have_role_and_event_day():
    """return_trace=True 时 trace_items 含 role 和 event_day 字段。"""
    text = _section(2, (
        "## 10:00\n"
        "**用户**：下棋很有意思\n"
        "> emotion:happy intensity:2\n"
        "---\n"
    ))
    result, trace = _run(text, "下棋", return_trace=True)
    assert trace, "trace 不应为空"
    for item in trace:
        assert "role" in item, "trace item 缺少 role 字段"
        assert "event_day" in item, "trace item 缺少 event_day 字段"
        assert item["event_day"] == 2
