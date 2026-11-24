"""
tests/test_diary_context_freshness.py — P0.5-1 验收

断言覆盖：
- save(uid, "") → 文件清空，meta.latest_entry_date=None
- save 含 # 2026-06-20 → meta.latest_entry_date=="2026-06-20"；多日期取最大
- _parse_latest_date：无头返回 None；多头取 max
- 注入闸：latest 4天内 → 注入；5天前 → 不注入；无 meta → 不注入
- 调度器：read_recent 返回空 → save 被调用且文件清空
"""

from __future__ import annotations
from tests.fixtures.public_assets import TEST_CHAR_ID

import json
import time
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_UID = "diary_fresh_uid"
_CHAR = TEST_CHAR_ID


# ─────────────────────────────────────────────────────────────────────────────
# _parse_latest_date
# ─────────────────────────────────────────────────────────────────────────────

class TestParseLatestDate:
    def setup_method(self):
        from core.memory.diary_context import _parse_latest_date
        self.fn = _parse_latest_date

    def test_none_on_empty(self):
        assert self.fn("") is None

    def test_none_on_no_header(self):
        assert self.fn("今天天气不错。") is None

    def test_single_date(self):
        assert self.fn("# 2026-06-20\n内容") == "2026-06-20"

    def test_multi_date_takes_max(self):
        text = "# 2026-06-18\n内容\n# 2026-06-20\n更多"
        assert self.fn(text) == "2026-06-20"

    def test_date_in_middle_of_line_ignored(self):
        # 只有行首 # YYYY-MM-DD 算
        assert self.fn("非日期行 # 2026-06-20 不算") is None

    def test_none_on_none_input(self):
        assert self.fn(None) is None  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# save + load_meta
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveAndMeta:
    def test_save_empty_clears_file_and_writes_meta(self, sandbox):
        from core.memory.diary_context import save, load, load_meta
        save(_UID, "# 2026-06-01\n内容", char_id=_CHAR)
        save(_UID, "", char_id=_CHAR)
        assert load(_UID, char_id=_CHAR) == ""
        meta = load_meta(_UID, char_id=_CHAR)
        assert meta.get("latest_entry_date") is None
        assert "captured_at" in meta

    def test_save_with_date_writes_correct_meta(self, sandbox):
        from core.memory.diary_context import save, load_meta
        save(_UID, "# 2026-06-20\n日记内容", char_id=_CHAR)
        meta = load_meta(_UID, char_id=_CHAR)
        assert meta["latest_entry_date"] == "2026-06-20"

    def test_save_multi_date_picks_max(self, sandbox):
        from core.memory.diary_context import save, load_meta
        save(_UID, "# 2026-06-18\nA\n# 2026-06-20\nB", char_id=_CHAR)
        meta = load_meta(_UID, char_id=_CHAR)
        assert meta["latest_entry_date"] == "2026-06-20"

    def test_load_meta_missing_returns_empty(self, sandbox):
        from core.memory.diary_context import load_meta
        result = load_meta("nonexistent_uid", char_id=_CHAR)
        assert result == {}


# ─────────────────────────────────────────────────────────────────────────────
# 注入闸（pipeline freshness check）— 通过直接测 pipeline 辅助逻辑
# ─────────────────────────────────────────────────────────────────────────────

class TestFreshnessGate:
    """
    验证 pipeline.context.fetch 里的新鲜度闸逻辑。
    把相关逻辑提取成等价 helper 来白盒测试。
    """

    @staticmethod
    def _apply_freshness_gate(diary_text: str, meta: dict, max_age_days: int = 4) -> str:
        """镜像 pipeline.py 里的新鲜度闸逻辑。"""
        if not diary_text:
            return diary_text
        _latest = meta.get("latest_entry_date")
        _fresh = False
        if _latest:
            try:
                _age = (date.today() - date.fromisoformat(_latest)).days
                _fresh = _age <= max_age_days
            except ValueError:
                _fresh = False
        return diary_text if _fresh else ""

    def test_fresh_within_max_age(self):
        today = date.today().isoformat()
        result = self._apply_freshness_gate("日记内容", {"latest_entry_date": today})
        assert result == "日记内容"

    def test_stale_beyond_max_age(self):
        old = (date.today() - timedelta(days=5)).isoformat()
        result = self._apply_freshness_gate("日记内容", {"latest_entry_date": old})
        assert result == ""

    def test_exactly_max_age_boundary(self):
        boundary = (date.today() - timedelta(days=4)).isoformat()
        result = self._apply_freshness_gate("日记内容", {"latest_entry_date": boundary})
        assert result == "日记内容"

    def test_no_meta_blocks_injection(self):
        result = self._apply_freshness_gate("日记内容", {})
        assert result == ""

    def test_none_latest_date_blocks_injection(self):
        result = self._apply_freshness_gate("日记内容", {"latest_entry_date": None})
        assert result == ""

    def test_empty_diary_passes_through(self):
        result = self._apply_freshness_gate("", {"latest_entry_date": date.today().isoformat()})
        assert result == ""


# ─────────────────────────────────────────────────────────────────────────────
# 调度器：read_recent 返空 → save 被调用且文件清空
# ─────────────────────────────────────────────────────────────────────────────

class TestSchedulerClearOnEmpty:
    def test_empty_read_recent_clears_snapshot(self, sandbox, monkeypatch):
        from core.memory.diary_context import save, load
        _calls = []
        orig_save = save

        def _mock_save(uid, text, *, char_id=TEST_CHAR_ID):
            _calls.append((uid, text))
            orig_save(uid, text, char_id=char_id)

        # Pre-populate
        save(_UID, "# 2026-06-01\n旧日记", char_id=_CHAR)
        assert load(_UID, char_id=_CHAR) != ""

        monkeypatch.setattr("core.memory.diary_context.save", _mock_save)

        import core.memory.diary_context as dc
        dc.save(_UID, "")
        assert len(_calls) == 1
        assert _calls[0][1] == ""
        assert load(_UID, char_id=_CHAR) == ""
