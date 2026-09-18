"""Diary presentation API exposes only the feeling layer."""

import asyncio

import pytest
from fastapi import HTTPException

from admin.routers import diary


def _register_char(sandbox, char_id: str) -> None:
    from core.sandbox import get_paths

    path = get_paths().character_card_write_path(char_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"name": "Diary Fixture"}', encoding="utf-8")


def _write_diary(sandbox, char_id: str, date: str, content: str) -> None:
    directory = sandbox.yexuan_inner_diary(char_id=char_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{date}.md").write_text(content, encoding="utf-8")


def test_structured_diary_api_hides_facts_and_uses_feeling_for_title(sandbox):
    _register_char(sandbox, "char_a")
    _write_diary(
        sandbox,
        "char_a",
        "2026-08-06",
        "# 2026-08-06\n\n## 今日事件\n- 09:00 用户完成了会议。\n\n## 今日感受\n她笑起来的时候，我忽然松了口气。\n",
    )

    listed = asyncio.run(diary.list_diary(char_id="char_a", auth=None))
    detail = asyncio.run(diary.get_diary("2026-08-06", char_id="char_a", auth=None))

    assert listed["entries"] == [{
        "date": "2026-08-06",
        "title": "她笑起来的时候，我忽然松了口气。",
        "emotion": None,
        "feeling": "她笑起来的时候，我忽然松了口气。",
    }]
    assert detail["title"] == "她笑起来的时候，我忽然松了口气。"
    assert detail["feeling"] == "她笑起来的时候，我忽然松了口气。"
    assert detail["body"] == detail["feeling"]
    assert "用户完成了会议" not in str(detail)


def test_legacy_unheaded_diary_remains_readable_as_feeling(sandbox):
    _register_char(sandbox, "char_a")
    _write_diary(sandbox, "char_a", "2026-08-05", "# 2026-08-05\n旧格式的日记内容。\n")

    detail = asyncio.run(diary.get_diary("2026-08-05", char_id="char_a", auth=None))

    assert detail["title"] == "旧格式的日记内容。"
    assert detail["feeling"] == "旧格式的日记内容。"


def test_event_only_structured_diary_has_explicit_empty_feeling(sandbox):
    _register_char(sandbox, "char_a")
    _write_diary(sandbox, "char_a", "2026-08-04", "# 2026-08-04\n\n## 今日事件\n- 完成了一件事。\n")

    detail = asyncio.run(diary.get_diary("2026-08-04", char_id="char_a", auth=None))

    assert detail["feeling"] == ""
    assert detail["body"] == ""


def test_unknown_diary_char_id_is_character_unavailable(sandbox):
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(diary.list_diary(char_id="ghost_char_xyz", auth=None))
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "character_unavailable"
