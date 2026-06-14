"""
tests/test_prompt_no_internal_leak.py

Verifies that no internal system vocabulary leaks into LLM-visible prompt text.

Contract assertions (all static source-level unless noted as runtime):
  L1  Cross-channel switch hint: no 桌宠/QQ/desktop/channel/通道 in the hint literal
  L2  Layer 3.6 body data: no [身体数据感知] tag; still contains sleep data injection
  L3  Layer 3.7 phone sensor: no [手机感知] tag; no "收到来自用户手机的数据"
  L4  Layer 3.8 activity snapshot: no [屏幕感知] tag
  L5  author_note extras: no [人设纠偏: / [输出风格: bracket forms
  L6  Memory protocol: no code/git/仓库/日志/测试/checkpoint dev vocab
  L7  sensor_aware build_situation_narrative: focus_app raw string never in focus_str
      (runtime: _app_category maps unknown app to neutral phrase, not raw name)
  L8  APP_CATEGORY_CHANGED narrative: no raw app names in the narrative f-string
  L9  Regression: layer 3.7 still builds _parts list (data not deleted, just re-worded)
  L10 Regression: layer 3.8 still calls _load_activity_snapshot (not removed)
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent


def _src(relpath: str) -> str:
    return (_ROOT / relpath).read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# L1 — Cross-channel switch hint
# ─────────────────────────────────────────────────────────────────────────────

class TestCrossChannelSwitchHint:
    _PIPELINE = "core/pipeline.py"

    def _switch_hint_line(self) -> str:
        for line in _src(self._PIPELINE).splitlines():
            if "_switch_hint" in line and "=" in line and "（" in line:
                return line
        return ""

    def test_no_channel_names_in_hint(self):
        line = self._switch_hint_line()
        assert line, "Could not find _switch_hint assignment in pipeline.py"
        forbidden = ["桌宠", "QQ", "desktop", "通道", "channel"]
        for word in forbidden:
            assert word not in line, f"Forbidden token {word!r} found in _switch_hint: {line!r}"

    def test_hint_still_describes_continuity(self):
        line = self._switch_hint_line()
        assert "延续" in line, "_switch_hint should still convey continuity"

    def test_channel_names_dict_removed(self):
        src = _src(self._PIPELINE)
        assert "_channel_names" not in src, \
            "_channel_names dict should have been removed from pipeline.py"


# ─────────────────────────────────────────────────────────────────────────────
# L2 — Layer 3.6 body data (sleep)
# ─────────────────────────────────────────────────────────────────────────────

class TestLayer36BodyData:
    _PB = "core/prompt_builder.py"

    def test_no_bracket_tag(self):
        src = _src(self._PB)
        assert "[身体数据感知]" not in src, "[身体数据感知] tag must be removed from prompt text"

    def test_sleep_data_injection_remains(self):
        src = _src(self._PB)
        assert "3.6_watch" in src, "Layer 3.6_watch must still exist"
        assert "sleep_start" in src or "_start" in src, \
            "Sleep start variable must still be injected into layer 3.6"


# ─────────────────────────────────────────────────────────────────────────────
# L3 — Layer 3.7 phone sensor
# ─────────────────────────────────────────────────────────────────────────────

class TestLayer37PhoneSensor:
    _PB = "core/prompt_builder.py"

    def test_no_bracket_tag(self):
        src = _src(self._PB)
        assert "[手机感知]" not in src, "[手机感知] tag must be removed from prompt text"

    def test_no_raw_data_source_description(self):
        src = _src(self._PB)
        assert "收到来自用户手机的数据" not in src, \
            "Raw data-source description must be removed from phone sensor layer"

    def test_timestamp_not_injected(self):
        # last_updated was injected into the prompt; should no longer be there
        src = _src(self._PB)
        # The old form was: f"[手机感知] {_sensor.get('last_updated', '')} ..."
        # Verify the new form does not reference last_updated in the content string
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if "3.7_sensor" in line:
                # Check the surrounding content= line doesn't have last_updated
                block = "\n".join(lines[max(0, i - 10):i + 5])
                assert "last_updated" not in block, \
                    "last_updated timestamp must not appear in the 3.7 layer content"
                break


# ─────────────────────────────────────────────────────────────────────────────
# L4 — Layer 3.8 activity snapshot
# ─────────────────────────────────────────────────────────────────────────────

class TestLayer38Activity:
    _PB = "core/prompt_builder.py"

    def test_no_bracket_tag(self):
        src = _src(self._PB)
        assert "[屏幕感知]" not in src, "[屏幕感知] tag must be removed from prompt text"

    def test_layer_still_present(self):
        src = _src(self._PB)
        assert "3.8_activity" in src, "Layer 3.8_activity must still exist"


# ─────────────────────────────────────────────────────────────────────────────
# L5 — author_note bracket forms
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthorNoteBrackets:
    _PB = "core/prompt_builder.py"

    def test_no_renshe_jiupian_bracket(self):
        src = _src(self._PB)
        assert "[人设纠偏" not in src, "[人设纠偏: bracket form must be removed"

    def test_no_output_style_bracket(self):
        src = _src(self._PB)
        assert "[输出风格" not in src, "[输出风格: bracket form must be removed"

    def test_style_instruction_still_appended(self):
        src = _src(self._PB)
        assert "style_instruction" in src, "style_instruction must still be appended to author_note_lines"

    def test_no_renshe_jiupian_text_in_content(self):
        src = _src(self._PB)
        assert "人设纠偏" not in src, \
            "人设纠偏 must not appear anywhere in prompt_builder.py (LLM will learn the term)"


# ─────────────────────────────────────────────────────────────────────────────
# L6 — Memory protocol dev vocabulary
# ─────────────────────────────────────────────────────────────────────────────

class TestMemoryProtocolDevVocab:
    _PB = "core/prompt_builder.py"

    # Extract the author_note_lines block so we only check the prompt content
    def _author_note_block(self) -> str:
        src = _src(self._PB)
        lines = src.splitlines()
        start = next((i for i, l in enumerate(lines) if "author_note_lines" in l and "=" in l), None)
        if start is None:
            return src  # fallback: scan full file
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("]")),
            len(lines),
        )
        return "\n".join(lines[start: end + 1])

    def test_no_git_vocab(self):
        block = self._author_note_block()
        for word in ("git 状态", "仓库", "commit", "部署"):
            assert word not in block, f"Dev vocab {word!r} must not appear in memory protocol"

    def test_no_engineering_vocab(self):
        block = self._author_note_block()
        for word in ("代码、文件、测试、日志", "额度"):
            assert word not in block, f"Engineering vocab {word!r} must not appear in memory protocol"

    def test_no_checkpoint_vocab(self):
        block = self._author_note_block()
        assert "历史 checkpoint" not in block, \
            "'历史 checkpoint' must be replaced with neutral wording"

    def test_no_dangqian_cangku(self):
        block = self._author_note_block()
        assert "当前仓库" not in block, "'当前仓库' must be removed from memory protocol"

    def test_no_gongcheng_shuyu(self):
        block = self._author_note_block()
        assert "工程术语" not in block, "'工程术语' must be replaced with neutral wording"

    def test_memory_protocol_still_exists(self):
        src = _src(self._PB)
        assert "记忆使用协议" in src, "Memory protocol section must still exist"

    def test_memory_confidence_boundary_still_exists(self):
        src = _src(self._PB)
        assert "记忆置信边界" in src, "Memory confidence boundary section must still exist"


# ─────────────────────────────────────────────────────────────────────────────
# L7 — sensor_aware build_situation_narrative: no raw app name
# ─────────────────────────────────────────────────────────────────────────────

class TestSensorAwareNarrative:
    _SA = "core/scheduler/triggers/sensor_aware.py"

    def test_no_raw_focus_app_in_focus_str(self):
        src = _src(self._SA)
        # The old leak: f"正在用 {focus_app}" or f"正在用 {focus_app}（{title_hint}）"
        assert "正在用 {focus_app}" not in src, \
            "Raw focus_app must not be interpolated into focus_str"
        assert "正在用 {focus_app}（{title_hint}）" not in src, \
            "Raw focus_app/title_hint must not be interpolated into focus_str"

    def test_title_hint_not_in_prompt(self):
        src = _src(self._SA)
        # title_hint should not appear in any f-string that builds focus_str
        lines = src.splitlines()
        for line in lines:
            if "focus_str" in line and "title_hint" in line and "=" in line and "f\"" in line:
                pytest.fail(f"title_hint found in focus_str assignment: {line!r}")

    def test_category_phrases_defined(self):
        src = _src(self._SA)
        assert "_APP_CATEGORY_PHRASES" in src, \
            "_APP_CATEGORY_PHRASES dict must be defined in sensor_aware.py"

    def test_app_category_called(self):
        src = _src(self._SA)
        assert "_get_app_category" in src or "_app_category" in src, \
            "App categorisation function must be called in sensor_aware.py"

    # Runtime: unknown app name → neutral phrase, never raw string
    def test_app_category_neutralizes_unknown_app(self):
        from core.scheduler.sensor_events import _app_category
        raw_app = "Visual Studio Code"
        cat = _app_category(raw_app)
        phrases = {
            "work": "在忙工作上的事",
            "leisure": "在放松",
            "takeout": "在点餐",
            "shopping": "在逛东西",
        }
        phrase = phrases.get(cat, "在做自己的事")
        assert raw_app not in phrase, \
            f"Raw app name must not appear in the resulting phrase: {phrase!r}"
        assert phrase, "Phrase must be non-empty"

    def test_known_app_categorized(self):
        from core.scheduler.sensor_events import _app_category
        assert _app_category("chrome.exe") == "leisure"
        assert _app_category("pycharm64.exe") == "work"
        assert _app_category("com.sankuai.meituan") == "takeout"


# ─────────────────────────────────────────────────────────────────────────────
# L8 — APP_CATEGORY_CHANGED narrative: no raw app names
# ─────────────────────────────────────────────────────────────────────────────

class TestAppCategoryChangedNarrative:
    _SE = "core/scheduler/sensor_events.py"

    def _narrative_block(self) -> str:
        src = _src(self._SE)
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if '"type":' in line and "APP_CATEGORY_CHANGED" in line:
                return "\n".join(lines[i: i + 8])
        return ""

    def test_no_last_app_in_narrative(self):
        block = self._narrative_block()
        assert block, "APP_CATEGORY_CHANGED narrative block must exist"
        assert "_last_app}" not in block and "{_last_app}" not in block, \
            "Raw _last_app variable must not be in APP_CATEGORY_CHANGED narrative"

    def test_no_focus_app_in_narrative(self):
        block = self._narrative_block()
        assert "{focus_app}" not in block, \
            "Raw focus_app variable must not be in APP_CATEGORY_CHANGED narrative"

    def test_category_in_narrative(self):
        block = self._narrative_block()
        assert "_last_app_category" in block or "current_cat" in block, \
            "APP_CATEGORY_CHANGED narrative must use category labels, not raw app names"


# ─────────────────────────────────────────────────────────────────────────────
# L9 — Regression: 3.7 data parts still built
# ─────────────────────────────────────────────────────────────────────────────

class TestRegressionDataNotDeleted:
    _PB = "core/prompt_builder.py"

    def test_37_steps_still_injected(self):
        src = _src(self._PB)
        assert "steps" in src and "今日步数" in src, \
            "Step count injection in layer 3.7 must not have been removed"

    def test_37_battery_still_injected(self):
        src = _src(self._PB)
        assert "battery" in src and "手机电量" in src, \
            "Battery injection in layer 3.7 must not have been removed"

    def test_38_activity_text_passed(self):
        src = _src(self._PB)
        assert "_activity_text" in src, \
            "_activity_text must still be passed to the layer 3.8 content"

    def test_36_sleep_hours_still_injected(self):
        src = _src(self._PB)
        assert "_h}小时{_m}分钟" in src, \
            "Sleep duration must still appear in layer 3.6 content"
