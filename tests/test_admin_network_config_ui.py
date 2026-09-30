from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_network_config_exposes_model_connection_mode():
    page = (ROOT / "admin" / "static" / "pages" / "network-config.html").read_text(encoding="utf-8")
    settings = (ROOT / "admin" / "static" / "js" / "settings.js").read_text(encoding="utf-8")
    index = (ROOT / "admin" / "static" / "index.html").read_text(encoding="utf-8")
    core = (ROOT / "admin" / "static" / "js" / "core.js").read_text(encoding="utf-8")

    assert 'id="proxy-model-mode"' in page
    assert 'option value="follow_global"' in page
    assert 'option value="auto"' in page
    assert 'option value="direct"' in page
    assert 'option value="proxy"' in page
    assert 'id="proxy-model-hint"' in page
    assert "model_connection_mode" in settings
    assert "status.proxy.model_need_url" in settings
    assert "ADMIN_UI_FRAGMENT_VERSION = 'v1-local-runtime-1'" in core
    assert '<script src="/static/js/core.js?v=v1-local-runtime-1"></script>' in index
    assert '<script src="/static/js/settings.js?v=v1-local-runtime-1"></script>' in index
    assert '<script src="/static/i18n.js?v=v1-local-runtime-1"></script>' in index
    assert '<script src="/static/js/status-users.js?v=v1-local-runtime-1"></script>' in index


def test_status_summary_links_to_network_config_and_shows_model_mode():
    status = (ROOT / "admin" / "static" / "pages" / "status.html").read_text(encoding="utf-8")
    script = (ROOT / "admin" / "static" / "js" / "status-users.js").read_text(encoding="utf-8")
    assert 'id="s-proxy-model-mode"' in status
    assert 'data-action-args=\'["network-config"]\'' in status
    assert "status.open_network_config" in status
    assert "d.model_connection_mode" in script
