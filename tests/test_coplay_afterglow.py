"""
tests/test_coplay_afterglow.py — Brief 42: 陪玩 afterglow 软提示（fail-closed, TTL）。
"""

import time
from unittest.mock import patch

from core.coplay import afterglow

UID = "u1"
CHAR = "yexuan"


def test_no_afterglow_by_default(sandbox):
    assert afterglow.load_afterglow_text(UID, char_id=CHAR) == ""


def test_save_and_load_within_ttl(sandbox):
    afterglow.save_afterglow(UID, game_name="黑暗之魂", char_id=CHAR)
    text = afterglow.load_afterglow_text(UID, char_id=CHAR)
    assert "黑暗之魂" in text
    assert "意犹未尽" in text


def test_afterglow_expires_after_ttl(sandbox):
    afterglow.save_afterglow(UID, game_name="黑暗之魂", char_id=CHAR)
    future = time.time() + afterglow.AFTERGLOW_TTL_SECONDS + 60
    with patch("time.time", return_value=future):
        assert afterglow.load_afterglow_text(UID, char_id=CHAR) == ""


def test_save_afterglow_rejects_empty_game_name(sandbox):
    assert afterglow.save_afterglow(UID, game_name="  ", char_id=CHAR) is False
    assert afterglow.load_afterglow_text(UID, char_id=CHAR) == ""


def test_load_fail_closed_on_corrupt_file(sandbox):
    from core.sandbox import get_paths
    p = get_paths().coplay_afterglow_path(UID, char_id=CHAR)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json{{{", encoding="utf-8")
    assert afterglow.load_afterglow_text(UID, char_id=CHAR) == ""


def test_char_id_isolation(sandbox):
    afterglow.save_afterglow(UID, game_name="黑暗之魂", char_id="char_a")
    assert afterglow.load_afterglow_text(UID, char_id="char_a") != ""
    assert afterglow.load_afterglow_text(UID, char_id="char_b") == ""
