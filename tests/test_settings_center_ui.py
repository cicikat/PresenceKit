"""Executable regression of migrated controls and stale response handling."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_settings_center_control_contracts():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the admin JavaScript regression")
    result = subprocess.run([node, "tests/settings_center_ui.cjs"], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
