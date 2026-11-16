"""Single write boundary for admin-managed config.yaml changes."""
from __future__ import annotations

import threading
from copy import deepcopy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from core.safe_write import safe_write_text
from core.config_yaml import load_config_yaml, dump_config_yaml

_WRITE_LOCK = threading.RLock()
_MISSING = object()


class ConfigDocument(dict):
    """Editable snapshot retaining its baseline for a three-way merge."""

    def __init__(self, value: Mapping[str, Any]):
        super().__init__(deepcopy(dict(value)))
        self.original = deepcopy(dict(value))


def _load_mapping(path: Path) -> dict[str, Any]:
    try:
        value = load_config_yaml(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {exc}") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=500, detail=f"配置文件顶层必须是 mapping: {path.name}")
    return value


def read_config_file(path: Path) -> ConfigDocument:
    return ConfigDocument(_load_mapping(Path(path)))


def _changed_paths(before: object, after: object, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        paths: list[tuple[str, ...]] = []
        for key in sorted(set(before) | set(after), key=str):
            old = before.get(key, _MISSING)
            new = after.get(key, _MISSING)
            child = prefix + (str(key),)
            if old is _MISSING or new is _MISSING:
                paths.append(child)
            else:
                paths.extend(_changed_paths(old, new, child))
        return paths
    return [prefix] if before != after else []


def _value_at(value: Mapping[str, Any], path: tuple[str, ...]) -> object:
    current: object = value
    for part in path:
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _apply_path(target: dict[str, Any], source: Mapping[str, Any], path: tuple[str, ...]) -> None:
    if not path:
        target.clear()
        target.update(deepcopy(dict(source)))
        return
    parent = target
    for part in path[:-1]:
        child = parent.get(part)
        if not isinstance(child, dict):
            child = {}
            parent[part] = child
        parent = child
    value = _value_at(source, path)
    if value is _MISSING:
        parent.pop(path[-1], None)
    else:
        parent[path[-1]] = deepcopy(value)


def write_config_file(path: Path, updated: dict[str, Any]) -> None:
    """Atomically persist an admin edit to the single runtime config."""
    if not isinstance(updated, dict):
        raise HTTPException(status_code=500, detail="待写入配置顶层必须是 mapping")

    path = Path(path)
    with _WRITE_LOCK:
        current = _load_mapping(path)
        baseline = updated.original if isinstance(updated, ConfigDocument) else current
        changed = _changed_paths(baseline, updated)
        merged = deepcopy(current)
        for changed_path in changed:
            _apply_path(merged, updated, changed_path)

        payload = dump_config_yaml(
            merged,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )
        if not safe_write_text(path, payload):
            raise HTTPException(status_code=500, detail="原子写入配置文件失败，原配置保持不变")

        persisted = _load_mapping(path)
        if persisted != merged:
            raise HTTPException(status_code=500, detail="配置写入后校验失败")
        if isinstance(updated, ConfigDocument):
            updated.clear()
            updated.update(deepcopy(merged))
            updated.original = deepcopy(merged)
