"""tests/test_diary_reader_has_any_entry.py — Brief 97 追加修复

diary_reminder 依赖 has_any_diary_entry() 区分"从没配置/从没写过日记"与
"配置了但漏了一天"；这里只测 has_any_diary_entry() 本身，不涉及 scheduler。
"""
from core.tools.diary_reader import has_any_diary_entry


def test_has_any_diary_entry_false_when_root_missing(tmp_path, monkeypatch):
    missing = tmp_path / "does-not-exist"
    monkeypatch.setattr("core.tools.diary_reader._diary_root", lambda **kwargs: missing)
    assert has_any_diary_entry() is False


def test_has_any_diary_entry_false_when_root_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("core.tools.diary_reader._diary_root", lambda **kwargs: tmp_path)
    assert has_any_diary_entry() is False


def test_has_any_diary_entry_false_when_only_unrelated_files(tmp_path, monkeypatch):
    (tmp_path / "README.md").write_text("not a diary entry", encoding="utf-8")
    monkeypatch.setattr("core.tools.diary_reader._diary_root", lambda **kwargs: tmp_path)
    assert has_any_diary_entry() is False


def test_has_any_diary_entry_true_when_one_entry_exists(tmp_path, monkeypatch):
    (tmp_path / "2026-05-01.md").write_text("今天写了点东西", encoding="utf-8")
    monkeypatch.setattr("core.tools.diary_reader._diary_root", lambda **kwargs: tmp_path)
    assert has_any_diary_entry() is True


def test_has_any_diary_entry_true_when_nested_entry_exists(tmp_path, monkeypatch):
    nested = tmp_path / "2026" / "05"
    nested.mkdir(parents=True)
    (nested / "2026-05-01.md").write_text("嵌套目录里的日记", encoding="utf-8")
    monkeypatch.setattr("core.tools.diary_reader._diary_root", lambda **kwargs: tmp_path)
    assert has_any_diary_entry() is True
