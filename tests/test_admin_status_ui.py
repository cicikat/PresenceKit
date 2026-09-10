from pathlib import Path
import re


ROOT = Path(__file__).parents[1]


def test_legacy_active_sessions_card_is_not_rendered_but_api_contract_remains():
    index = (ROOT / "admin" / "static" / "index.html").read_text(encoding="utf-8")
    system_router = (ROOT / "admin" / "routers" / "system.py").read_text(encoding="utf-8")

    assert 'id="s-sessions"' not in index
    assert 'id="session-list"' not in index
    assert "活跃会话列表" not in index
    assert '"active_sessions"' in system_router
    assert '"active_session_count"' in system_router


def test_status_page_is_read_only_summary_with_explicit_configuration_entries():
    static = ROOT / "admin" / "static"
    status = (static / "pages" / "status.html").read_text(encoding="utf-8")
    runtime = (static / "pages" / "runtime-config.html").read_text(encoding="utf-8")
    tts = (static / "pages" / "tts-config.html").read_text(encoding="utf-8")

    assert 'id="data-safety-card"' in status
    assert 'id="s-data-root"' in status
    assert 'id="s-test-users"' in status
    assert 'data-action-args=' in status and '"tts-config"' in status
    assert not re.search(r'data-action="(?:save|toggle|reloadConfig)|<input\b|<textarea\b', status)
    assert 'data-action="saveTtsConfig"' not in status

    assert 'onchange="saveCenterSwitch(this)"' in (static / 'js' / 'settings-center.js').read_text(encoding='utf-8')
    assert 'data-action="saveProxy"' in (static / 'pages' / 'network-config.html').read_text(encoding='utf-8')
    assert 'data-action="saveTtsConfig"' in tts
    assert '<details class="card tts-advanced"' in tts
    assert 'id="tts-provider-api-key"' in tts
    assert 'id="tts-ref-audio"' in tts


def test_structured_provider_values_use_a_boolean_selector_and_keep_secrets_write_only():
    core = (ROOT / "admin" / "static" / "js" / "core.js").read_text(encoding="utf-8")
    character = (ROOT / "admin" / "static" / "js" / "character.js").read_text(encoding="utf-8")

    assert 'data-kv-boolean' in core
    assert "row.querySelector('[data-kv-boolean]').value === 'true'" in core
    assert "exclude: ['api_key'" in character
    assert "tts-provider-api-key" in character
