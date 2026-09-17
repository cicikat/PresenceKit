"""Legacy uid-only event_log ownership: freeze at first compatible read.

Covers A3 of the 2026-09-17 backend audit work order:
  - non-default / unread characters cannot see uid-only logs
  - historical default (configured default at first compatible read) still can
  - dual-directory union stays available for that frozen owner
  - freeze does not reassign after character.default / active changes
  - different owners do not share a freeze record
  - historical API, list/count, and prompt-side search respect the same gate
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.memory import event_log
from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID, TEST_THIRD_CHAR_ID


_UID = "legacy_owner_uid"
_UID_B = "legacy_owner_uid_b"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _day_block(time_str: str, user_text: str, reply_text: str, turn_id: str, intensity: int = 0) -> str:
    return (
        f"## {time_str}\n"
        f"**用户**：{user_text}\n"
        f"> turn_id:{turn_id}\n"
        f"**Companion**：{reply_text}\n"
        f"> emotion:neutral intensity:{intensity} turn_id:{turn_id}\n"
        "---\n"
    )


def _seed_uid_only(sandbox, uid: str, date_str: str, marker: str, turn_id: str = "tid-old") -> None:
    old_dir = sandbox._p("event_log") / uid
    _write(old_dir / f"{date_str}.md", _day_block("10:00", marker, "已读到", turn_id, intensity=1))


def test_unread_character_does_not_see_uid_only_logs(sandbox):
    today = datetime.now().strftime("%Y-%m-%d")
    _seed_uid_only(sandbox, _UID, today, "UID_ONLY_SECRET")

    # Probing as an unread character must not freeze that character as owner.
    assert "UID_ONLY_SECRET" not in event_log.get_recent_days(_UID, days=1, char_id=TEST_PEER_CHAR_ID)
    assert today not in event_log.list_days(_UID, char_id=TEST_PEER_CHAR_ID)
    assert not event_log.may_read_legacy_event_log(_UID, TEST_PEER_CHAR_ID)
    # historical_legacy_event_log_char_id() freezes the configured default on first
    # compatible-owner lookup, even if a non-owner already probed the tree.
    assert event_log.historical_legacy_event_log_char_id(_UID) == TEST_CHAR_ID
    assert event_log.may_read_legacy_event_log(_UID, TEST_CHAR_ID)
    assert "UID_ONLY_SECRET" in event_log.get_recent_days(_UID, days=1, char_id=TEST_CHAR_ID)


def test_historical_default_can_union_uid_only_and_canonical(sandbox):
    today = datetime.now().strftime("%Y-%m-%d")
    _seed_uid_only(sandbox, _UID, today, "UID_ONLY_AM", turn_id="tid-am")
    new_dir = sandbox.user_memory_root(_UID, char_id=TEST_CHAR_ID) / "event_log"
    _write(new_dir / f"{today}.md", _day_block("15:00", "CANONICAL_PM", "下午好", "tid-pm"))

    text = event_log.get_recent_days(_UID, days=1, char_id=TEST_CHAR_ID)
    assert "UID_ONLY_AM" in text
    assert "CANONICAL_PM" in text
    assert event_log.historical_legacy_event_log_char_id(_UID) == TEST_CHAR_ID
    assert event_log.may_read_legacy_event_log(_UID, TEST_CHAR_ID)
    assert today in event_log.list_days(_UID, char_id=TEST_CHAR_ID)

    peer = event_log.get_recent_days(_UID, days=1, char_id=TEST_PEER_CHAR_ID)
    assert "UID_ONLY_AM" not in peer
    assert "CANONICAL_PM" not in peer


def test_freeze_does_not_reassign_after_configured_default_changes(sandbox, monkeypatch):
    today = datetime.now().strftime("%Y-%m-%d")
    _seed_uid_only(sandbox, _UID, today, "FROZEN_OWNER_MARKER")

    assert event_log.may_read_legacy_event_log(_UID, TEST_CHAR_ID)
    assert event_log.historical_legacy_event_log_char_id(_UID) == TEST_CHAR_ID

    monkeypatch.setattr(event_log, "_configured_default_char_id", lambda: TEST_PEER_CHAR_ID)
    assert event_log.historical_legacy_event_log_char_id(_UID) == TEST_CHAR_ID
    assert event_log.may_read_legacy_event_log(_UID, TEST_CHAR_ID)
    assert not event_log.may_read_legacy_event_log(_UID, TEST_PEER_CHAR_ID)
    assert "FROZEN_OWNER_MARKER" in event_log.get_recent_days(_UID, days=1, char_id=TEST_CHAR_ID)
    assert "FROZEN_OWNER_MARKER" not in event_log.get_recent_days(_UID, days=1, char_id=TEST_PEER_CHAR_ID)
    assert "FROZEN_OWNER_MARKER" not in event_log.get_recent_days(_UID, days=1, char_id=TEST_THIRD_CHAR_ID)


def test_different_owners_freeze_independently(sandbox, monkeypatch):
    today = datetime.now().strftime("%Y-%m-%d")
    _seed_uid_only(sandbox, _UID, today, "OWNER_A_SECRET")
    _seed_uid_only(sandbox, _UID_B, today, "OWNER_B_SECRET")

    assert event_log.may_read_legacy_event_log(_UID, TEST_CHAR_ID)
    monkeypatch.setattr(event_log, "_configured_default_char_id", lambda: TEST_PEER_CHAR_ID)
    assert event_log.may_read_legacy_event_log(_UID_B, TEST_PEER_CHAR_ID)
    assert event_log.historical_legacy_event_log_char_id(_UID) == TEST_CHAR_ID
    assert event_log.historical_legacy_event_log_char_id(_UID_B) == TEST_PEER_CHAR_ID
    assert "OWNER_A_SECRET" not in event_log.get_recent_days(_UID_B, days=1, char_id=TEST_PEER_CHAR_ID)
    assert "OWNER_B_SECRET" not in event_log.get_recent_days(_UID, days=1, char_id=TEST_CHAR_ID)


def test_count_and_day_file_respect_legacy_eligibility(sandbox):
    today = datetime.now()
    date_str = today.strftime("%Y-%m-%d")
    old_dir = sandbox._p("event_log") / _UID
    _write(
        old_dir / "full_log.md",
        "> speaker:user turn_id:full-1\n**用户**：旧 full_log\n",
    )
    _write(old_dir / f"{date_str}.md", _day_block("10:00", "DAY_FILE_SECRET", "ok", "tid-day"))

    assert event_log.count_real_turns(_UID, char_id=TEST_CHAR_ID) == 1
    assert event_log.count_real_turns(_UID, char_id=TEST_PEER_CHAR_ID) == 0

    default_path = event_log._day_file_read(_UID, today, char_id=TEST_CHAR_ID)
    peer_path = event_log._day_file_read(_UID, today, char_id=TEST_PEER_CHAR_ID)
    assert default_path.exists() and "DAY_FILE_SECRET" in default_path.read_text(encoding="utf-8")
    assert not peer_path.exists()


@pytest.mark.asyncio
async def test_prompt_search_and_highlights_respect_legacy_eligibility(sandbox):
    old_date = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    _seed_uid_only(sandbox, _UID, old_date, "我今天很难过想你了", turn_id="tid-search")

    found = await event_log.search(_UID, "难过", char_id=TEST_CHAR_ID)
    assert found
    leaked = await event_log.search(_UID, "难过", char_id=TEST_PEER_CHAR_ID)
    assert not leaked

    highlights = event_log.get_highlights(_UID, days=3, char_id=TEST_CHAR_ID)
    assert "难过" in highlights or "想你" in highlights
    assert not event_log.get_highlights(_UID, days=3, char_id=TEST_PEER_CHAR_ID)
