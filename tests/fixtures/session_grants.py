"""Explicit synthetic session setup for route regressions (no auth bypass)."""
from core import session_scope
from tests.fixtures.public_assets import TEST_CHAR_ID


def issue(monkeypatch, *, owner=None, char=TEST_CHAR_ID, label="legacy-admin"):
    if owner is None:
        from core.config_loader import get_config
        owner = str(get_config().get("scheduler", {}).get("owner_id", "test_owner"))
    monkeypatch.setattr(session_scope, "_owner_id", lambda: owner)
    # Character loading is the asset boundary; these tests supply synthetic cards.
    monkeypatch.setattr(session_scope, "_validate_character", lambda value: str(value))
    return session_scope.create_session(token_label=label, char_id=char)
