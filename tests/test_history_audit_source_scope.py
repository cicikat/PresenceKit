"""The production scope audit must read project sources, not ignored runtime data."""

from pathlib import Path

from test_short_term_history_scope import _collect_get_history_calls_missing_char_id


def test_history_audit_still_catches_project_calls_and_excludes_non_source_roots(tmp_path):
    for relative in ("core/unscoped.py", ".venv/vendor.py", ".tmp/archive.py",
                     "data/private.py", "userdata/private.py", "node_modules/vendor.py"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("get_history('fixture_owner')\n", encoding="utf-8", newline="\n")
    scoped = tmp_path / "core/scoped.py"
    scoped.write_text("get_history('fixture_owner', char_id='fixture_character')\n",
                      encoding="utf-8", newline="\n")
    assert _collect_get_history_calls_missing_char_id(tmp_path) == [
        (str(Path("core/unscoped.py")), 1),
    ]
