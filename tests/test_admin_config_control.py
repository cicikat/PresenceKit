import asyncio
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from admin import config_control


def _read(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_write_config_file_is_atomic_and_persists(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("practice:\n  enabled: false\n", encoding="utf-8")

    config_control.write_config_file(path, {"practice": {"enabled": True}})

    assert _read(path) == {"practice": {"enabled": True}}
    assert not path.with_suffix(".yaml.tmp").exists()


def test_write_failure_keeps_original_config(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("practice:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.setattr(config_control, "safe_write_text", lambda *_args, **_kwargs: False)

    with pytest.raises(HTTPException) as exc:
        config_control.write_config_file(path, {"practice": {"enabled": True}})

    assert exc.value.status_code == 500
    assert _read(path)["practice"]["enabled"] is False


def test_config_document_three_way_merge_preserves_concurrent_change(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("practice:\n  enabled: false\nproxy:\n  enabled: false\n", encoding="utf-8")
    first = config_control.read_config_file(path)
    second = config_control.read_config_file(path)
    first["practice"]["enabled"] = True
    second["proxy"]["enabled"] = True

    config_control.write_config_file(path, first)
    config_control.write_config_file(path, second)

    assert _read(path) == {
        "practice": {"enabled": True},
        "proxy": {"enabled": True},
    }


def test_settings_routers_do_not_write_config_directly():
    root = Path(__file__).parent.parent / "admin" / "routers"
    violations = []
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        manages_base_config = "config.yaml" in text and (
            'open(CONFIG_FILE, "w"' in text
            or 'CONFIG_FILE.open("w"' in text
            or 'open(path, "w"' in text and "scheduler" in path.name
        )
        if manages_base_config:
            violations.append(path.name)
    assert violations == []


def test_config_codec_rejects_python_object_tags(tmp_path):
    """The accelerated safe loader must never construct Python objects."""
    path = tmp_path / "config.yaml"
    path.write_bytes(b"value: !!python/tuple [1, 2]\n")
    with pytest.raises(HTTPException) as exc:
        config_control.read_config_file(path)
    assert exc.value.status_code == 500
    assert path.read_bytes() == b"value: !!python/tuple [1, 2]\n"

