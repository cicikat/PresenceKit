"""Invalid legacy directory names must not hide valid admin users."""
from types import SimpleNamespace

from admin.routers.users import _get_known_users


def test_discovery_ignores_unsafe_legacy_names_without_deleting(tmp_path, monkeypatch):
    history = tmp_path / "history"
    profiles = tmp_path / "profiles"
    memory = tmp_path / "memory"
    for root in (history, profiles, memory):
        root.mkdir()
    (history / "owner-a.json").write_bytes(b"[]")
    (profiles / "owner-b.json").write_bytes(b"{}")
    (memory / "owner-c").mkdir()
    invalid = memory / "\u89d2\u8272\u663e\u793a\u540d"
    invalid.mkdir()
    invalid_file = profiles / "\u65e7\u663e\u793a\u540d.json"
    invalid_file.write_bytes(b"{}")
    monkeypatch.setattr("core.sandbox.get_paths", lambda: SimpleNamespace(
        history=lambda: history,
        profiles=lambda: profiles,
        memory_char_root=lambda: memory,
    ))

    assert _get_known_users() == ["owner-a", "owner-b", "owner-c"]
    assert invalid.is_dir() and invalid_file.is_file()


def test_discovery_missing_roots_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("core.sandbox.get_paths", lambda: SimpleNamespace(
        history=lambda: tmp_path / "absent-history",
        profiles=lambda: tmp_path / "absent-profiles",
        memory_char_root=lambda: tmp_path / "absent-memory",
    ))
    assert _get_known_users() == []
