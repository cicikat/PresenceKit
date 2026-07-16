from pathlib import Path


ROOT = Path(__file__).parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_presence_layer_replaces_ambiguous_activity_name():
    source = _read("core/prompt_builder.py")
    assert '"_layer": "2.6_presence"' in source
    assert '"2.6_presence", "角色此刻在做什么（ambient presence）"' in source
    assert "2.6_activity" not in source


def test_activity_modules_cross_identify_their_distinct_roles():
    manager = _read("core/activity_manager.py")
    session = _read("core/activity/__init__.py")
    assert "ambient presence" in manager and "ActivitySession" in manager
    assert "ActivitySession" in session and "activity_manager" in session


def test_activity_docs_include_naming_distinction():
    docs = _read("docs/activity-session.md")
    assert "命名辨析" in docs
    assert "2.6_presence" in docs
