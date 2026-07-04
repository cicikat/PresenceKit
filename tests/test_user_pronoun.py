"""
tests/test_user_pronoun.py
==========================
P0.6-2: User pronoun system.

Verifies:
  U1  get_user_pronoun defaults to '她' when unset
  U2  get_user_pronoun returns set valid value
  U3  get_user_pronoun falls back to '她' for invalid value
  U4  format_for_prompt does NOT output pronoun field
  U5  update_user_facts accepts pronoun field
  U6  event_log search renders '{pronoun}提到' instead of '你提到'
  U7  episodic format_for_prompt replaces '用户' with user_pronoun
  U8  episodic format_for_prompt default user_pronoun is '她'
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest.mock as mock

import pytest

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

UID = "test_pronoun_uid_9988"


def _path_for(uid: str) -> pathlib.Path:
    from core.memory.scope import MemoryScope
    from core.memory.path_resolver import resolve_path
    return resolve_path(MemoryScope.global_scope(uid), "user_facts")


def _write_facts(uid: str, facts: dict):
    p = _path_for(uid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")


def _delete_facts(uid: str):
    p = _path_for(uid)
    if p.exists():
        p.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# U1-U3: get_user_pronoun
# ─────────────────────────────────────────────────────────────────────────────

class TestGetUserPronoun:
    def setup_method(self):
        _delete_facts(UID)

    def teardown_method(self):
        _delete_facts(UID)

    def test_default_returns_她(self):
        from core.memory.user_facts import get_user_pronoun
        assert get_user_pronoun(UID) == "她"

    def test_returns_set_pronoun_他(self):
        _write_facts(UID, {"pronoun": "他"})
        from core.memory.user_facts import get_user_pronoun
        assert get_user_pronoun(UID) == "他"

    def test_returns_set_pronoun_TA(self):
        _write_facts(UID, {"pronoun": "TA"})
        from core.memory.user_facts import get_user_pronoun
        assert get_user_pronoun(UID) == "TA"

    def test_returns_set_pronoun_它(self):
        _write_facts(UID, {"pronoun": "它"})
        from core.memory.user_facts import get_user_pronoun
        assert get_user_pronoun(UID) == "它"

    def test_invalid_pronoun_falls_back_to_她(self):
        _write_facts(UID, {"pronoun": "they"})
        from core.memory.user_facts import get_user_pronoun
        assert get_user_pronoun(UID) == "她"

    def test_empty_pronoun_falls_back_to_她(self):
        _write_facts(UID, {"pronoun": ""})
        from core.memory.user_facts import get_user_pronoun
        assert get_user_pronoun(UID) == "她"


# ─────────────────────────────────────────────────────────────────────────────
# U4: format_for_prompt excludes pronoun field
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatForPromptExcludesPronoun:
    def setup_method(self):
        _delete_facts(UID)

    def teardown_method(self):
        _delete_facts(UID)

    def test_pronoun_not_in_output(self):
        _write_facts(UID, {"pronoun": "他", "preferred_language": "Chinese"})
        from core.memory.user_facts import format_for_prompt
        result = format_for_prompt(UID)
        assert "pronoun" not in result
        assert "他" not in result
        assert "preferred_language" in result

    def test_pronoun_only_returns_empty(self):
        _write_facts(UID, {"pronoun": "她"})
        from core.memory.user_facts import format_for_prompt
        result = format_for_prompt(UID)
        assert result == "", f"Expected empty string, got: {result!r}"


# ─────────────────────────────────────────────────────────────────────────────
# U5: update_user_facts accepts pronoun
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateUserFactsPronoun:
    def setup_method(self):
        _delete_facts(UID)

    def teardown_method(self):
        _delete_facts(UID)

    def test_pronoun_accepted_by_update(self):
        from core.memory.user_facts import update_user_facts, load_user_facts
        updated, rejected = update_user_facts(UID, {"pronoun": "他"})
        assert "pronoun" not in rejected
        assert load_user_facts(UID).get("pronoun") == "他"

    def test_invalid_pronoun_still_written_to_store(self):
        """update_user_facts doesn't validate pronoun values — admin route does."""
        from core.memory.user_facts import update_user_facts, load_user_facts
        updated, rejected = update_user_facts(UID, {"pronoun": "weird"})
        assert "pronoun" not in rejected
        # get_user_pronoun will return default, but raw value is stored
        assert load_user_facts(UID).get("pronoun") == "weird"


# ─────────────────────────────────────────────────────────────────────────────
# U6: event_log search renders pronoun instead of '你'
# ─────────────────────────────────────────────────────────────────────────────

class TestEventLogRenderCard:
    def setup_method(self):
        _delete_facts(UID)

    def teardown_method(self):
        _delete_facts(UID)

    def _call_search_with_mock_log(self, pronoun: str | None = None):
        """
        Call event_log.search with a minimal fake log that has a matching entry,
        asserting what the rendered card says.
        """
        if pronoun is not None:
            _write_facts(UID, {"pronoun": pronoun})

        from datetime import datetime
        fake_log = f"# {datetime.now().strftime('%Y-%m-%d')}\n**用户**：天气好好\n"

        from core.memory.event_log import search
        import asyncio

        with (
            mock.patch("core.memory.event_log.get_recent_days", return_value=fake_log),
            mock.patch("core.config_loader._char_name", return_value="叶瑄"),
        ):
            result = asyncio.run(search(UID, query="天气", char_id="yexuan"))
        return result

    def test_default_renders_她提到(self):
        result = self._call_search_with_mock_log(pronoun=None)
        assert "她提到" in result, f"Expected '她提到' in: {result!r}"
        assert "你提到" not in result

    def test_他_renders_他提到(self):
        result = self._call_search_with_mock_log(pronoun="他")
        assert "他提到" in result, f"Expected '他提到' in: {result!r}"

    def test_TA_renders_TA提到(self):
        result = self._call_search_with_mock_log(pronoun="TA")
        assert "TA提到" in result, f"Expected 'TA提到' in: {result!r}"


# ─────────────────────────────────────────────────────────────────────────────
# U7-U8: episodic format_for_prompt user_pronoun param
# ─────────────────────────────────────────────────────────────────────────────

class TestEpisodicFormatForPrompt:
    def _make_memory(self, summary: str) -> dict:
        import time
        return {
            "narrative_summary": summary,
            "timestamp": time.time(),
            "occurred_at": time.time(),
            "emotion_peak": "neutral",
            "strength": 0.8,
        }

    def test_default_user_pronoun_replaces_用户_with_她(self):
        from core.memory.episodic_memory import format_for_prompt
        mem = self._make_memory("用户聊起了最近的工作")
        result = format_for_prompt([mem], char_name="叶瑄")
        assert "她聊起了最近的工作" in result, f"Got: {result!r}"
        assert "用户" not in result

    def test_explicit_他_replaces_用户(self):
        from core.memory.episodic_memory import format_for_prompt
        mem = self._make_memory("用户说很累")
        result = format_for_prompt([mem], char_name="叶瑄", user_pronoun="他")
        assert "他说很累" in result, f"Got: {result!r}"
        assert "用户" not in result

    def test_TA_pronoun(self):
        from core.memory.episodic_memory import format_for_prompt
        mem = self._make_memory("用户提到喜欢猫")
        result = format_for_prompt([mem], char_name="叶瑄", user_pronoun="TA")
        assert "TA提到喜欢猫" in result, f"Got: {result!r}"

    def test_no_用户_in_output_with_她(self):
        from core.memory.episodic_memory import format_for_prompt
        mem = self._make_memory("用户昨天用户提到用户回来了")
        result = format_for_prompt([mem], char_name="叶瑄", user_pronoun="她")
        assert "用户" not in result, f"Leftover '用户' in: {result!r}"

    def test_empty_memories_returns_empty(self):
        from core.memory.episodic_memory import format_for_prompt
        result = format_for_prompt([], char_name="叶瑄", user_pronoun="他")
        assert result == ""
