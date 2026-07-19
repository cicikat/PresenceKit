from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_legacy_active_sessions_card_is_not_rendered_but_api_contract_remains():
    index = (ROOT / "admin" / "static" / "index.html").read_text(encoding="utf-8")
    system_router = (ROOT / "admin" / "routers" / "system.py").read_text(encoding="utf-8")

    assert 'id="s-sessions"' not in index
    assert 'id="session-list"' not in index
    assert "活跃会话列表" not in index
    assert '"active_sessions"' in system_router
    assert '"active_session_count"' in system_router
