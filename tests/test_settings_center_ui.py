"""Executable regression of migrated controls and stale response handling."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_creation_assets_are_split_setting_rows():
    page = (ROOT / "admin/static/pages/creation-center.html").read_text(encoding="utf-8")
    source = (ROOT / "admin/static/js/settings-center.js").read_text(encoding="utf-8")
    index = (ROOT / "admin/static/index.html").read_text(encoding="utf-8")
    assert 'class="admin-page-header"' in page
    assert 'id="creation-assets"' in page
    assert "function creationAssetRow" in source
    assert "admin-setting-row" in source
    assert "id=\"creation-avatar\"" in source
    assert "data-action=\"saveCreationAssets\"" in source
    assert "data-action=\"uploadCreationAvatar\"" in source
    assert "PATCH','/settings/prompt-assets'" in source
    assert '<script src="/static/js/settings-center.js?v=brief-253-file-path-1"></script>' in index


def test_thinking_controls_live_on_model_routing_not_conversation_page():
    routing = (ROOT / "admin/static/pages/model-routing.html").read_text(encoding="utf-8")
    conversation = (ROOT / "admin/static/pages/conversation-settings.html").read_text(encoding="utf-8")
    source = (ROOT / "admin/static/js/settings-center.js").read_text(encoding="utf-8")
    settings = (ROOT / "admin/static/js/settings.js").read_text(encoding="utf-8")
    assert 'id="mr-thinking-card"' in routing
    assert 'data-action="saveThinkingSettings"' in routing
    assert 'data-action="saveThinkingSettings"' not in conversation
    assert "conversation-thinking-jump" in source
    assert "thinking_moved" in source
    assert "function loadThinkingSettings()" in source
    assert "function saveThinkingSettings()" in source
    assert "if (typeof loadThinkingSettings === 'function') loadThinkingSettings();" in settings
    assert "['enabled'" in source
    assert "['mode'" in source
    assert 'class="admin-toolbar"' in source
    assert 'class="checkbox-row"' in source
    assert 'type === \'boolean\'' in source or 'type === "boolean"' in source


def test_feature_center_groups_category_switches():
    page = (ROOT / "admin/static/pages/feature-center.html").read_text(encoding="utf-8")
    source = (ROOT / "admin/static/js/settings-center.js").read_text(encoding="utf-8")
    device = (ROOT / "admin/static/pages/device-policy.html").read_text(encoding="utf-8")
    assert "CENTER_GROUPED_FLAGS" in source
    assert "screen_peek" in source
    assert "PATCH','/system/meta-mode'" in source
    assert "PUT','/settings/agent-runtime-browser'" in source
    assert "PUT','/sticker-config'" in source
    assert "settings_center.perception_and_computer_actions" in source
    assert "settings_center.output_and_interaction" in source
    assert "settings_center.external_capabilities" in source
    assert 'id="metamode-ttl"' not in device
    assert "status.dangermode.ttl" not in device
    assert 'data-action-args=\'["output-settings"]\'' not in page
    assert 'data-i18n="settings_center.other_detailed_settings"' in page


def test_settings_center_control_contracts():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the admin JavaScript regression")
    result = subprocess.run([node, "tests/settings_center_ui.cjs"], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
