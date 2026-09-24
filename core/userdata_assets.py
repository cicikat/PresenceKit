"""Safe discovery and storage for private ``userdata/`` assets.

The module intentionally has no arbitrary filesystem API.  Callers choose a
small logical category and the service derives the canonical destination from
``DataPaths``.  Live2D/3D entries are marked backend-only until the desktop
consumer contract exists; uploads must never claim to be renderable.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from core.data_paths import DEFAULT_CHAR_ID

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_PACKAGE_BYTES = 250 * 1024 * 1024
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class AssetSpec:
    category: str
    label: str
    extensions: frozenset[str]
    scope: str
    group: str
    upload_fields: tuple[str, ...]
    desktop_available: bool = True


ASSET_SPECS: dict[str, AssetSpec] = {
    "reference_audio": AssetSpec("reference_audio", "参考音频", frozenset({".wav", ".mp3", ".flac", ".ogg", ".mp4"}), "character", "voice", ("char_id", "logical_id", "file")),
    "gpt_model": AssetSpec("gpt_model", "GPT 模型", frozenset({".ckpt", ".pt", ".pth", ".safetensors"}), "character", "voice", ("char_id", "logical_id", "file")),
    "sovits_model": AssetSpec("sovits_model", "SoVITS 模型", frozenset({".pth", ".ckpt", ".safetensors"}), "character", "voice", ("char_id", "logical_id", "file")),
    "sticker": AssetSpec("sticker", "通用表情包", frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"}), "global", "stickers", ("logical_id", "file", "emotion")),
    "sticker_pack": AssetSpec("sticker_pack", "角色表情包", frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"}), "character", "stickers", ("char_id", "pack", "emotion", "logical_id", "file")),
    # These are intentionally backend-only in this brief.  The status field is
    # exposed so the UI cannot mistake an upload for desktop availability.
    "live2d": AssetSpec("live2d", "Live2D 模型包", frozenset({".zip"}), "character", "models", ("char_id", "logical_id", "file"), False),
    "model3d": AssetSpec("model3d", "3D 模型包", frozenset({".zip"}), "character", "models", ("char_id", "logical_id", "file"), False),
}


def _paths():
    from core.sandbox import get_paths
    return get_paths()


def validate_id(value: str, *, field: str = "logical_id") -> str:
    value = str(value or "").strip()
    if not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid {field}")
    return value


def validate_character_id(value: str) -> str:
    """Allow existing Unicode character IDs while rejecting path syntax."""
    value = str(value or "").strip()
    if not (value and len(value) <= 64 and value[0].isalnum()
            and all(char.isalnum() or char in "_-" for char in value)):
        raise ValueError("invalid char_id")
    return value


def validate_filename(filename: str, *, allowed: Iterable[str]) -> str:
    raw = str(filename or "")
    # Reject path syntax even when Path.name would normalize it away.
    if not raw or "\\" in raw or "/" in raw or ".." in raw or "//" in raw:
        raise ValueError("unsafe asset filename")
    name = Path(raw).name
    if name != raw or Path(name).suffix.lower() not in set(allowed):
        raise ValueError("unsupported asset filename")
    return name


def _category_root(category: str, *, char_id: str = DEFAULT_CHAR_ID, emotion: str = "", pack: str = "") -> tuple[Path, Path | None]:
    paths = _paths()
    spec = ASSET_SPECS[category]
    if spec.scope == "character":
        char_id = validate_character_id(char_id)
    if category in {"reference_audio", "gpt_model", "sovits_model"}:
        user, legacy = paths.character_voice_dirs(char_id=char_id)
        return user, legacy
    if category == "sticker":
        emotion = validate_id(emotion or "neutral", field="emotion")
        return paths.user_stickers_dir() / emotion, paths.legacy_stickers_dir() / emotion
    if category == "sticker_pack":
        pack = validate_id(pack, field="pack")
        emotion = validate_id(emotion or "neutral", field="emotion")
        return paths.sticker_pack_dir(pack) / emotion, None
    if category == "live2d":
        char_id = validate_character_id(char_id)
        return paths.user_live2d_root() / char_id, None
    if category == "model3d":
        char_id = validate_character_id(char_id)
        return paths.user_model3d_root() / char_id, None
    raise ValueError("unsupported asset category")


def _safe_relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("asset path escaped canonical root") from exc


def _iter_files(root: Path | None) -> Iterable[Path]:
    if not root or not root.is_dir():
        return ()
    return (p for p in root.rglob("*") if p.is_file() and not p.is_symlink())


def _listing_roots(category: str, *, char_id: str) -> tuple[tuple[Path, str], ...]:
    """Return broad, read-only roots for a category's asset inventory."""
    paths = _paths()
    if category == "sticker":
        return ((paths.user_stickers_dir(), "user"), (paths.legacy_stickers_dir(), "legacy"))
    if category == "sticker_pack":
        return ((paths.sticker_packs_root(), "user"),)
    user_root, legacy_root = _category_root(category, char_id=char_id)
    return ((user_root, "user"), (legacy_root, "legacy"))


def _asset_scope(category: str, relative: str) -> dict[str, str]:
    parts = PurePosixPath(relative).parts[:-1]
    if category == "sticker" and parts:
        return {"emotion": parts[0]}
    if category == "sticker_pack" and len(parts) >= 2:
        return {"pack": parts[0], "emotion": parts[1]}
    return {}


def _is_valid_package(path: Path, category: str) -> tuple[bool, str]:
    if category not in {"live2d", "model3d"}:
        return True, "supported"
    try:
        with zipfile.ZipFile(path) as archive:
            names = [PurePosixPath(i.filename) for i in archive.infolist()]
            if any(i.is_absolute() or ".." in i.parts for i in names):
                return False, "package_path_escape"
            if any((i.external_attr >> 16) & 0o170000 == 0o120000 for i in archive.infolist()):
                return False, "package_symlink"
            if category == "live2d":
                entries = [i for i in names if i.name.lower().endswith(".model3.json")]
                if len(entries) != 1:
                    return False, "live2d_entrypoint_required"
                payload = json.loads(archive.read(str(entries[0])))
                refs = []
                for key in ("FileReferences", "fileReferences"):
                    block = payload.get(key) if isinstance(payload, dict) else None
                    if isinstance(block, dict):
                        refs.extend(v for v in block.values() if isinstance(v, str))
                parent = entries[0].parent
                for ref in refs:
                    target = PurePosixPath(ref)
                    if target.is_absolute() or ".." in target.parts or (parent / target).as_posix() not in {n.as_posix() for n in names}:
                        return False, "live2d_reference_escape"
            return True, "backend_only"
    except (zipfile.BadZipFile, json.JSONDecodeError, OSError):
        return False, "invalid_package"


def _same_logical_asset(reference: object, logical_id: str) -> bool:
    value = Path(str(reference or "")).name
    return value == logical_id or Path(value).stem == logical_id


def _configured_tts_bindings(logical_id: str) -> list[dict[str, str]]:
    """Return non-sensitive binding references without returning configured paths."""
    try:
        from core.config_loader import get_config

        tts = get_config().get("tts", {})
        if not isinstance(tts, dict):
            return []
    except Exception:
        return []

    bindings: list[dict[str, str]] = []
    fields = ("ref_audio", "gpt_model_path", "sovits_model_path")
    for field in fields:
        if _same_logical_asset(tts.get(field), logical_id):
            bindings.append({"type": "tts_global", "id": "global", "field": field})
    presets = tts.get("presets") or {}
    if isinstance(presets, dict):
        for preset_name, preset in presets.items():
            if not isinstance(preset, dict):
                continue
            for field in fields:
                if _same_logical_asset(preset.get(field), logical_id):
                    bindings.append({"type": "tts_preset", "id": str(preset_name), "field": field})
    return bindings


def category_contract() -> list[dict]:
    """Expose the upload/display contract without exposing any filesystem path."""
    return [
        {
            "id": spec.category,
            "label": spec.label,
            "group": spec.group,
            "scope": spec.scope,
            "desktop_available": spec.desktop_available,
            "extensions": sorted(spec.extensions),
            "accept": ",".join(sorted(spec.extensions)),
            "max_bytes": MAX_PACKAGE_BYTES if spec.category in {"live2d", "model3d"} else MAX_FILE_BYTES,
            "upload_fields": list(spec.upload_fields),
        }
        for spec in ASSET_SPECS.values()
    ]


def list_assets(*, category: str | None = None, char_id: str = DEFAULT_CHAR_ID) -> list[dict]:
    if category is not None and category not in ASSET_SPECS:
        raise ValueError("unsupported asset category")
    categories = [category] if category else list(ASSET_SPECS)
    rows: list[dict] = []
    for cat in categories:
        spec = ASSET_SPECS[cat]
        seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        roots = _listing_roots(cat, char_id=char_id)
        for root, source in roots:
            for path in _iter_files(root):
                try:
                    rel = _safe_relative(root, path)
                except ValueError:
                    continue
                logical_id = Path(rel).stem
                scope = _asset_scope(cat, rel)
                identity = (logical_id, tuple(sorted(scope.items())))
                if identity in seen:
                    continue
                seen.add(identity)
                stat = path.stat()
                rows.append({
                    "logical_id": logical_id,
                    "name": path.name,
                    "category": cat,
                    "label": spec.label,
                    "char_id": char_id if spec.scope == "character" else None,
                    "scope": scope,
                    "source": source,
                    "size": stat.st_size,
                    "updated_at": stat.st_mtime,
                    "valid": path.suffix.lower() in spec.extensions,
                    "availability": "available" if source == "user" and spec.desktop_available else ("legacy_read_only" if source == "legacy" else "partial"),
                    "desktop_available": bool(spec.desktop_available and source == "user"),
                    "bindings": _configured_tts_bindings(logical_id) if cat in {"reference_audio", "gpt_model", "sovits_model"} else [],
                })
    return sorted(rows, key=lambda row: (row["category"], row["logical_id"]))


def resolve_asset_path(*, category: str, logical_id: str, char_id: str = DEFAULT_CHAR_ID) -> Path | None:
    """Resolve a logical asset id internally without exposing it to clients."""
    if category not in ASSET_SPECS:
        return None
    # Existing authored files can have Chinese stems. Keep upload IDs stricter,
    # but resolve a listed filename without accepting path syntax from callers.
    logical_id = str(logical_id or "")
    if (not logical_id or len(logical_id) > 255 or logical_id in {".", ".."}
            or "/" in logical_id or "\\" in logical_id or "\x00" in logical_id):
        return None
    user_root, legacy_root = _category_root(category, char_id=char_id)
    for root in (user_root, legacy_root):
        for path in _iter_files(root):
            if path.stem == logical_id or path.name == logical_id:
                return path
    return None


def store_upload(*, category: str, logical_id: str, filename: str, content: bytes, char_id: str = DEFAULT_CHAR_ID, emotion: str = "", pack: str = "", replace: bool = False) -> dict:
    if category not in ASSET_SPECS:
        raise ValueError("unsupported asset category")
    spec = ASSET_SPECS[category]
    if len(content) > (MAX_PACKAGE_BYTES if category in {"live2d", "model3d"} else MAX_FILE_BYTES):
        raise ValueError("asset exceeds size limit")
    logical_id = validate_id(logical_id)
    filename = validate_filename(filename, allowed=spec.extensions)
    root, _ = _category_root(category, char_id=char_id, emotion=emotion, pack=pack)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{logical_id}{Path(filename).suffix.lower()}"
    same_id = [path for path in _iter_files(root) if path.stem == logical_id]
    if same_id and not replace:
        raise FileExistsError(logical_id)
    tmp_name = f".{logical_id}.{os.getpid()}.{time.time_ns()}.tmp"
    tmp = root / tmp_name
    try:
        tmp.write_bytes(content)
        if category in {"live2d", "model3d"}:
            ok, reason = _is_valid_package(tmp, category)
            if not ok:
                raise ValueError(reason)
        os.replace(tmp, target)
        for previous in same_id:
            if previous != target and previous.exists():
                previous.unlink()
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return next(
        row for row in list_assets(category=category, char_id=char_id)
        if row["logical_id"] == logical_id and row["source"] == "user"
    ) | {"sha256": hashlib.sha256(content).hexdigest()}


def deletion_impact(*, category: str, logical_id: str, char_id: str = DEFAULT_CHAR_ID,
                    emotion: str = "", pack: str = "") -> dict:
    if category not in ASSET_SPECS:
        raise ValueError("unsupported asset category")
    logical_id = validate_id(logical_id)
    expected_scope = {
        key: value for key, value in {"emotion": emotion, "pack": pack}.items() if value
    }
    rows = [
        row for row in list_assets(category=category, char_id=char_id)
        if row["logical_id"] == logical_id and (not expected_scope or row.get("scope") == expected_scope)
    ]
    bindings = _configured_tts_bindings(logical_id) if category in {"reference_audio", "gpt_model", "sovits_model"} else []
    has_user_asset = any(row.get("source") == "user" for row in rows)
    return {"logical_id": logical_id, "category": category, "assets": rows, "bindings": bindings, "can_delete": has_user_asset and not bindings}


def delete_asset(*, category: str, logical_id: str, char_id: str = DEFAULT_CHAR_ID,
                 emotion: str = "", pack: str = "") -> dict:
    impact = deletion_impact(category=category, logical_id=logical_id, char_id=char_id,
                             emotion=emotion, pack=pack)
    if not any(row.get("source") == "user" for row in impact["assets"]):
        raise PermissionError("asset is read-only")
    if impact["bindings"]:
        raise PermissionError("asset is bound")
    root, _ = _category_root(category, char_id=char_id, emotion=emotion, pack=pack)
    candidates = [p for p in _iter_files(root) if p.stem == logical_id]
    if not candidates:
        raise FileNotFoundError(logical_id)
    deleted = []
    for path in candidates:
        path.unlink()
        deleted.append(path.name)
    return {"deleted": deleted, "impact": impact}
