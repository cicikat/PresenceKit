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
    assert '<script src="/static/js/settings-center.js?v=admin-prompt-followups-2"></script>' in index


def test_settings_center_control_contracts():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the admin JavaScript regression")
    result = subprocess.run([node, "tests/settings_center_ui.cjs"], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
