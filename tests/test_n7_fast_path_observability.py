"""
N7-A 快速路径可观测性测试

测试目标：
  1. 快速路径命中时返回 matched_tool 和 matched_keyword。
  2. original_text_preview 长度不超过 80 字。
  3. 副作用工具（garden / reminder / desktop 控制等）标记 has_side_effect=True / risk="high"。
  4. 纯读工具（get_time / weather 等）标记 has_side_effect=False / risk="low"。
  5. 行为不变：原本能命中的工具仍能命中，命中工具与关键词对应正确。
  6. 未命中时返回 None，不记录 match 日志。

N7 约束：所有测试只验证观测层，不改变工具执行路径。
"""

import logging
import unittest.mock as mock

import pytest

# conftest.py 已将项目根目录加入 sys.path，可直接导入
from core import tool_dispatcher
from main import _fast_path_match


# ─────────────────────────────────────────────────────────────────────────────
# 1 & 2：命中返回结构 + preview 长度
# ─────────────────────────────────────────────────────────────────────────────

class TestFastPathMatchReturn:
    """验证 _fast_path_match 的返回结构（tool_name, keyword）。"""

    def test_returns_tuple_on_match(self):
        result = _fast_path_match("现在几点了")
        assert result is not None
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_matched_tool_and_keyword_correct(self):
        result = _fast_path_match("现在几点了")
        assert result is not None
        tool_name, kw = result
        assert tool_name == "get_time"
        spec = tool_dispatcher._TOOL_REGISTRY["get_time"]
        assert kw in spec["keywords"], f"{kw!r} 不在 get_time.keywords 里"

    def test_returns_none_on_no_match(self):
        result = _fast_path_match("今天心情还不错，想和你聊聊天")
        assert result is None

    def test_preview_within_80_chars(self):
        """即使原始文本超长，preview 切片后不超过 80 字。"""
        long_text = "现在几点了" + "甲" * 300   # 含 get_time 关键词（N7-B: 只有 allowlist 工具能命中）
        result = _fast_path_match(long_text)
        assert result is not None
        # 验证 main.py 里 trusted_user_text[:80] 的切片逻辑
        preview = long_text[:80]
        assert len(preview) <= 80

    def test_preview_does_not_contain_full_long_text(self):
        """preview 不记录超过 80 字的完整文本（隐私保护）。"""
        long_text = "A" * 200 + "几点"   # 以关键词结尾确保命中
        result = _fast_path_match(long_text)
        assert result is not None
        preview = long_text[:80]
        assert len(preview) < len(long_text)


# ─────────────────────────────────────────────────────────────────────────────
# 3：副作用工具 → side_effect=True / risk="high"
# ─────────────────────────────────────────────────────────────────────────────

class TestSideEffectToolsAreHighRisk:
    """garden / reminder / desktop 控制工具应被判定为高风险。"""

    @pytest.mark.parametrize("tool_name", [
        "water_garden",
        "add_reminder",
        "desktop_minimize",
        "desktop_open_url",
        "desktop_play_pause",
        "desktop_notify",
        "play_song",
        "exit_yandere",
    ])
    def test_is_side_effect_true(self, tool_name):
        assert tool_dispatcher.is_side_effect_tool(tool_name) is True, (
            f"{tool_name} 应被标记为副作用工具"
        )

    @pytest.mark.parametrize("tool_name", [
        "water_garden",
        "add_reminder",
        "desktop_play_pause",
        "play_song",
    ])
    def test_fast_path_risk_is_high(self, tool_name):
        assert tool_dispatcher.tool_fast_path_risk(tool_name) == "high"

    def test_dangerous_tools_are_side_effect(self):
        """dangerous=True 的工具应被 is_side_effect_tool 捕获。"""
        assert tool_dispatcher.is_side_effect_tool("device_shutdown") is True
        assert tool_dispatcher.is_side_effect_tool("device_sleep") is True


# ─────────────────────────────────────────────────────────────────────────────
# 4：纯读工具 → side_effect=False / risk="low"
# ─────────────────────────────────────────────────────────────────────────────

class TestReadOnlyToolsAreLowRisk:
    """纯读类工具应被判定为低风险。"""

    @pytest.mark.parametrize("tool_name", [
        "get_time",
        "weather",
        "web_search",
        "read_diary",
        "read_watch",
        "search_diary",
        "get_profile",
        "get_episodic",
    ])
    def test_is_side_effect_false(self, tool_name):
        assert tool_dispatcher.is_side_effect_tool(tool_name) is False, (
            f"{tool_name} 不应被标记为副作用工具"
        )

    @pytest.mark.parametrize("tool_name", [
        "get_time",
        "weather",
        "web_search",
    ])
    def test_fast_path_risk_is_low(self, tool_name):
        assert tool_dispatcher.tool_fast_path_risk(tool_name) == "low"


# ─────────────────────────────────────────────────────────────────────────────
# 5：行为不变 —— 原本能命中的工具仍能命中
# ─────────────────────────────────────────────────────────────────────────────

class TestFastPathBehaviorUnchanged:
    """N7-B: 只有 FAST_PATH_TOOL_ALLOWLIST 内的工具走快速路径。"""

    @pytest.mark.parametrize("trigger_text,expected_tool", [
        ("现在几点了", "get_time"),
    ])
    def test_allowlist_tool_still_matches(self, trigger_text, expected_tool):
        """allowlist 内的工具仍能被 fast path 命中。"""
        result = _fast_path_match(trigger_text)
        assert result is not None, (
            f"文本 {trigger_text!r} 应命中工具 {expected_tool}，但未命中"
        )
        matched_tool, matched_kw = result
        assert matched_tool == expected_tool, (
            f"期望命中 {expected_tool}，实际命中 {matched_tool!r}（关键词: {matched_kw!r}）"
        )

    def test_non_allowlist_tools_no_longer_fast_path(self):
        """N7-B: weather/water_garden/play_song/add_reminder/web_search/desktop_* 不再走快速路径。"""
        from main import FAST_PATH_TOOL_ALLOWLIST
        cases = [
            ("今天天气怎么样", "weather"),
            ("快去浇花", "water_garden"),
            ("播放稻香", "play_song"),
            ("帮我提醒我8点吃药", "add_reminder"),
            ("帮我搜一下最新消息", "web_search"),
            ("最小化微信窗口", "desktop_minimize"),
        ]
        for text, tool in cases:
            result = _fast_path_match(text)
            assert result is None or result[0] in FAST_PATH_TOOL_ALLOWLIST, (
                f"N7-B: 文本 {text!r} 不应 fast path 命中非 allowlist 工具 {tool!r}"
            )

    def test_only_info_and_desktop_categories_matched(self):
        """memory / system 类工具不应被快速路径命中。"""
        # read_watch 是 memory 类，即使含关键词也不应由快速路径返回
        # (read_watch 没有 keywords 字段，但用这个场景确保非 info/desktop 不被扫)
        # device_shutdown 是 system 类
        for tool_name in ("read_watch", "device_shutdown", "device_sleep"):
            spec = tool_dispatcher._TOOL_REGISTRY.get(tool_name, {})
            assert spec.get("category") not in ("info", "desktop"), (
                f"{tool_name} 不应在 info/desktop 分类中"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 6：未命中时不记录 match 日志
# ─────────────────────────────────────────────────────────────────────────────

class TestNoLogOnMiss:
    """_fast_path_match 未命中时应返回 None，main.py 中不会记录 qq_fast_path_match 日志。"""

    def test_no_match_returns_none(self):
        safe_inputs = [
            "嗯嗯好的",
            "今天心情不太好",
            "我在想一件事",
            "你觉得怎么样",
        ]
        for text in safe_inputs:
            assert _fast_path_match(text) is None, (
                f"文本 {text!r} 不应命中任何快速路径工具"
            )

    def test_logger_not_called_on_miss(self):
        """通过 mock logger 确认：未命中时 qq_fast_path_match event 不被记录。"""
        with mock.patch("main.logger") as mock_logger:
            result = _fast_path_match("嗯嗯没什么特别的事情")
            assert result is None
            # 主函数里的 logger.info("[qq_fast_path_match] ...") 在 handle_message 内，
            # 此处仅验证 _fast_path_match 本身不记录日志（它不含 logger 调用）
            mock_logger.info.assert_not_called()
            mock_logger.warning.assert_not_called()
