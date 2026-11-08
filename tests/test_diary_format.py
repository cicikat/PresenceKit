from core.diary_format import normalize_diary_prose
from admin.routers.diary import _split_layers


def test_diary_word_wrap_is_not_a_paragraph_boundary():
    text = "窗边的灯光很\n暖，雨声落在\n屋檐。\n\n在\n以前，我会把纸\n收起来。"
    assert normalize_diary_prose(text) == "窗边的灯光很暖，雨声落在屋檐。\n\n在以前，我会把纸收起来。"


def test_diary_markdown_and_english_are_preserved():
    text = "## A heading\nA quiet\nnight, with rain.\n\n- one\n- two\n\n```text\na\nb\n```"
    result = normalize_diary_prose(text)
    assert "A quiet night, with rain." in result
    assert "- one\n- two" in result and "```text\na\nb\n```" in result


def test_old_diary_read_projection_repairs_only_feeling():
    raw = "# 2026-11-08\n\n## 今日事件\n- 12:00 读书\n\n## 今日感受\n今天的\n风很轻。\n\n纸页还\n没合上。"
    facts, feeling = _split_layers(raw)
    assert "- 12:00 读书" in facts
    assert feeling == "今天的风很轻。\n\n纸页还没合上。"
    assert "今天的\n风很轻" in raw
