"""R2: repeated prose belongs to its own turn; retry repairs either projection."""
from datetime import datetime, timedelta
from pathlib import Path
import builtins

import pytest

from core.memory import event_log
from tests.fixtures.public_assets import TEST_CHAR_ID


@pytest.mark.parametrize("char_id", [TEST_CHAR_ID, "second_character"])
def test_repeated_reply_is_not_deduplicated_against_another_turn(sandbox, char_id):
    for turn in ("turn-1", "turn-10", "turn-2"):
        assert event_log.append("owner", "user", "again", turn_id=turn, char_id=char_id)
        assert event_log.append("owner", "assistant", "same reply", turn_id=turn, char_id=char_id)
        assert event_log.append("owner", "assistant", "same reply", turn_id=turn, char_id=char_id)
    directory = event_log._event_log_write_dir("owner", char_id=char_id)
    for path in directory.glob("*.md"):
        assert path.read_text(encoding="utf-8").count("same reply") == 3


def test_same_content_from_different_speakers_and_renamed_character(sandbox, monkeypatch):
    monkeypatch.setattr("core.config_loader._char_name", lambda: "Before")
    assert event_log.append("owner", "user", "same", turn_id="t", char_id=TEST_CHAR_ID)
    assert event_log.append("owner", "assistant", "same", turn_id="t", char_id=TEST_CHAR_ID)
    monkeypatch.setattr("core.config_loader._char_name", lambda: "After")
    assert event_log.append("owner", "assistant", "same", turn_id="t", char_id=TEST_CHAR_ID)
    text = event_log._full_log_file_write("owner", char_id=TEST_CHAR_ID).read_text(encoding="utf-8")
    assert text.count("speaker:assistant turn_id:t") == 1
    assert text.count("speaker:user turn_id:t") == 1


@pytest.mark.parametrize("failed_file", ["day", "full"])
def test_retry_repairs_only_missing_projection(sandbox, monkeypatch, failed_file):
    original = builtins.open
    failed = False

    def fail_once(path, mode="r", *args, **kwargs):
        nonlocal failed
        path = Path(path)
        matches = (path.name == "full_log.md") == (failed_file == "full")
        if mode == "a" and matches and not failed:
            failed = True
            raise OSError("injected append failure")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fail_once)
    # Fault the actual filesystem boundary, not event_log.append itself.
    assert not event_log.append("owner", "assistant", "repair me", turn_id="repair", char_id=TEST_CHAR_ID)
    assert event_log.append("owner", "assistant", "repair me", turn_id="repair", char_id=TEST_CHAR_ID)
    directory = event_log._event_log_write_dir("owner", char_id=TEST_CHAR_ID)
    assert len(list(directory.glob("*.md"))) == 2
    for path in directory.glob("*.md"):
        assert path.read_text(encoding="utf-8").count("repair me") == 1


def test_same_reply_on_different_days(sandbox, monkeypatch):
    now = datetime.now()
    class Clock(datetime):
        @classmethod
        def now(cls):
            return now
    monkeypatch.setattr(event_log, "datetime", Clock)
    for turn in ("first", "second"):
        event_log.append("owner", "user", "again", turn_id=turn, char_id=TEST_CHAR_ID)
        event_log.append("owner", "assistant", "same", turn_id=turn, char_id=TEST_CHAR_ID)
        now += timedelta(days=1)
    text = event_log._full_log_file_write("owner", char_id=TEST_CHAR_ID).read_text(encoding="utf-8")
    assert text.count("speaker:assistant") == 2


def test_recovered_turn_stays_in_original_day_projection(sandbox):
    """A replay after midnight must not become a new event in today's file."""
    from core.memory.fixation_pipeline import capture_turn
    from core.write_envelope import stamp_user_chat
    occurred = (datetime.now() - timedelta(days=2)).replace(hour=23, minute=59)
    capture_turn("owner", "synthetic earlier message", "synthetic earlier reply",
        turn_id="fixture-old-turn", char_id=TEST_CHAR_ID, occurred_at=occurred.timestamp(), envelope=stamp_user_chat())
    directory=event_log._event_log_write_dir("owner",char_id=TEST_CHAR_ID)
    original=directory / (occurred.strftime("%Y-%m-%d")+".md")
    assert original.exists()
    assert "## 23:59" in original.read_text(encoding="utf-8")
    capture_turn("owner", "synthetic earlier message", "synthetic earlier reply",
        turn_id="fixture-old-turn", char_id=TEST_CHAR_ID, occurred_at=occurred.timestamp(), envelope=stamp_user_chat())
    assert original.read_text(encoding="utf-8").count("turn_id:fixture-old-turn")==2
    assert not (directory/(datetime.now().strftime("%Y-%m-%d")+".md")).exists()
