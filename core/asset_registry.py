"""
Asset registry and resolver for authored prompt assets.

Unified model:
  id       — stable machine identifier (ASCII-safe, never the Chinese filename stem)
  label    — display name shown in UI (may be Chinese)
  filename — bare filename on disk (with extension, may be Chinese)
  kind     — "character" | "reality_lorebook" | "reality_jailbreak" | "dream_preset"
  hidden   — if True, excluded from UI asset lists

Rules:
- All external calls (PATCH, config) use id only.
- filename / label must never be stored as the active-asset key in any config.
- resolve() is fail-loud: raises ValueError for unknown ids.
- normalize_legacy() converts old filenames / Chinese labels to id for one-time migration.
- Dream presets with Chinese filenames must have an entry in _DREAM_PRESET_ID_MAP;
  otherwise they are hidden (no stable ASCII id can be derived automatically).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.authored_asset_resolver import resolve_layered_files

logger = logging.getLogger(__name__)

_LEGACY_CHARACTERS_DIR = Path("characters")
# Compatibility aliases retained for extensions and test fixtures that still
# refer to the pre-C1 names. Runtime discovery itself uses DataPaths.
_CHARACTERS_DIR = _LEGACY_CHARACTERS_DIR
_LOREBOOKS_DIR = _LEGACY_CHARACTERS_DIR / "reality" / "lorebooks"
_JAILBREAKS_DIR = _LEGACY_CHARACTERS_DIR / "reality" / "jailbreaks"
_DREAM_PRESETS_DIR = _LEGACY_CHARACTERS_DIR / "dream_presets"
_AVATARS_DIR = _LEGACY_CHARACTERS_DIR / "reality" / "avatars"

# Stem substrings that mark non-rolecard / scaffold files
_NON_CARD_KEYWORDS = frozenset({"template", "author_notes", "example"})

# Dream-preset ids must be alphanumeric (matches existing PATCH validation)
_ASCII_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Stable ASCII-id mapping for dream presets with non-ASCII filenames.
# key = file stem (Chinese), value = (ascii_id, display_label)
# Add new entries here when a new Chinese-named preset is authored.
_DREAM_PRESET_ID_MAP: dict[str, tuple[str, str]] = {
    "审讯":   ("interrogation", "审讯"),
    "多p":    ("multi",         "多p"),
    "触手巢穴": ("tentacle",      "触手巢穴"),
    "感官": ("ganguan",      "感官"),
}


def _dream_preset_logical_id(stem: str) -> str:
    if _ASCII_ID_RE.fullmatch(stem):
        return stem
    mapped = _DREAM_PRESET_ID_MAP.get(stem)
    return mapped[0] if mapped is not None else stem


@dataclass(frozen=True)
class AssetEntry:
    id: str
    label: str
    filename: str
    kind: str
    hidden: bool
    avatar_url: str | None = None
    has_runtime_avatar: bool = False
    source_path: Path | None = None

    def as_ui_dict(self) -> dict:
        d: dict = {"id": self.id, "label": self.label, "kind": self.kind, "avatar_url": self.avatar_url}
        if self.kind == "character":
            d["has_runtime_avatar"] = self.has_runtime_avatar
        return d

    def path(self) -> Path:
        if self.source_path is not None:
            return self.source_path
        # Compatibility for direct construction in older callers/tests.
        if self.kind == "character":
            return _LEGACY_CHARACTERS_DIR / self.filename
        if self.kind == "reality_lorebook":
            return _LEGACY_CHARACTERS_DIR / "reality" / "lorebooks" / self.filename
        if self.kind == "reality_jailbreak":
            return _LEGACY_CHARACTERS_DIR / "reality" / "jailbreaks" / self.filename
        if self.kind == "dream_preset":
            return _LEGACY_CHARACTERS_DIR / "dream_presets" / self.filename
        raise ValueError(f"unknown asset kind: {self.kind!r}")


def _paths():
    from core.sandbox import get_paths
    return get_paths()


# ── Per-kind scanners ─────────────────────────────────────────────────────────

_AVATAR_EXTS = ("png", "jpg", "jpeg", "webp")


def _avatar_info_for(char_id: str) -> tuple[str | None, bool]:
    """Return (avatar_url, has_runtime_avatar).

    Priority: runtime override (data/runtime/characters/{id}/avatar.*) >
              authored default (characters/reality/avatars/{id}.png).
    avatar_url includes ?v={mtime} for cache-busting.
    """
    from core.sandbox import get_paths
    runtime_dir = get_paths().runtime_character_dir(char_id=char_id)
    for ext in _AVATAR_EXTS:
        p = runtime_dir / f"avatar.{ext}"
        if p.exists():
            mtime = int(p.stat().st_mtime)
            return (f"/settings/character-avatar/{char_id}?v={mtime}", True)
    paths = _paths()
    for root in (paths.user_reality_dir() / "avatars", paths.legacy_reality_dir() / "avatars"):
        authored = root / f"{char_id}.png"
        if authored.exists():
            mtime = int(authored.stat().st_mtime)
            return (f"/settings/character-avatar/{char_id}?v={mtime}", False)
    return (None, False)

def _scan_characters() -> list[AssetEntry]:
    # Scan legacy first, then the packaged public default, then user overrides.
    result: dict[str, AssetEntry] = {}
    paths = _paths()
    for root in (_CHARACTERS_DIR,):
        if not root.exists():
            continue
        for p in sorted(root.glob("*.json")):
            stem_lower = p.stem.lower()
            hidden = any(kw in stem_lower for kw in _NON_CARD_KEYWORDS)
            label = p.stem
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                label = data.get("name") or p.stem
            except Exception:
                pass
            avatar_url, has_runtime = _avatar_info_for(p.stem)
            result[p.stem] = AssetEntry(id=p.stem, label=label, filename=p.name,
                                        kind="character", hidden=hidden,
                                        avatar_url=avatar_url,
                                        has_runtime_avatar=has_runtime,
                                        source_path=p)
        for ext in ("*.txt", "*.md"):
            for p in sorted(root.glob(ext)):
                stem_lower = p.stem.lower()
                hidden = any(kw in stem_lower for kw in _NON_CARD_KEYWORDS)
                avatar_url, has_runtime = _avatar_info_for(p.stem)
                result[p.stem] = AssetEntry(id=p.stem, label=p.stem, filename=p.name,
                                            kind="character", hidden=hidden,
                                            avatar_url=avatar_url,
                                            has_runtime_avatar=has_runtime,
                                            source_path=p)
    bundled_card = paths.bundled_default_character_card()
    if bundled_card.is_file():
        label = "default"
        try:
            data = json.loads(bundled_card.read_text(encoding="utf-8"))
            label = data.get("name") or label
        except Exception:
            pass
        avatar_url, has_runtime = _avatar_info_for("default")
        result["default"] = AssetEntry(
            id="default", label=label, filename="default.json", kind="character",
            hidden=False, avatar_url=avatar_url, has_runtime_avatar=has_runtime,
            source_path=bundled_card,
        )
    root = paths.user_character_cards_dir()
    if root.exists():
        for p in sorted(root.glob("*.json")):
            stem_lower = p.stem.lower()
            hidden = any(kw in stem_lower for kw in _NON_CARD_KEYWORDS)
            label = p.stem
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                label = data.get("name") or p.stem
            except Exception:
                pass
            avatar_url, has_runtime = _avatar_info_for(p.stem)
            result[p.stem] = AssetEntry(id=p.stem, label=label, filename=p.name,
                                        kind="character", hidden=hidden,
                                        avatar_url=avatar_url,
                                        has_runtime_avatar=has_runtime,
                                        source_path=p)
        for ext in ("*.txt", "*.md"):
            for p in sorted(root.glob(ext)):
                stem_lower = p.stem.lower()
                hidden = any(kw in stem_lower for kw in _NON_CARD_KEYWORDS)
                avatar_url, has_runtime = _avatar_info_for(p.stem)
                result[p.stem] = AssetEntry(id=p.stem, label=p.stem, filename=p.name,
                                            kind="character", hidden=hidden,
                                            avatar_url=avatar_url,
                                            has_runtime_avatar=has_runtime,
                                            source_path=p)
    return list(result.values())


def _first_nonempty_str(*values) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _keyword_list(entry: dict | None) -> list[str]:
    if not isinstance(entry, dict):
        return []
    raw = entry.get("keywords") or entry.get("keyword") or []
    if isinstance(raw, str):
        raw = [raw]
    return [item.strip() for item in raw if isinstance(item, str) and item.strip()]


def _lorebook_label(path: Path, stem: str) -> str:
    """UI label: file title/name, else entry keywords, else stem."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return stem
    if not isinstance(data, dict):
        return stem
    file_title = _first_nonempty_str(data.get("title"), data.get("name"))
    if file_title:
        return file_title
    keywords: list[str] = []
    seen: set[str] = set()
    for entry in data.get("entries") or []:
        for keyword in _keyword_list(entry):
            if keyword in seen:
                continue
            seen.add(keyword)
            keywords.append(keyword)
            if len(keywords) >= 3:
                break
        if len(keywords) >= 3:
            break
    return " / ".join(keywords) if keywords else stem


def _jailbreak_label(path: Path, stem: str) -> str:
    """UI label: file title/name, else first entry title, else stem."""
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return stem
    if not isinstance(data, dict):
        return stem
    file_title = _first_nonempty_str(data.get("title"), data.get("name"))
    if file_title:
        return file_title
    for entry in data.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        title = _first_nonempty_str(entry.get("title"), entry.get("name"))
        if title:
            return title
    return stem


def _scan_lorebooks() -> list[AssetEntry]:
    paths = _paths()
    result = []
    for item in resolve_layered_files(
        paths.user_reality_dir() / "lorebooks",
        _LOREBOOKS_DIR,
        logical_asset="reality_lorebook",
        suffixes=(".yaml",),
        logical_id=lambda relative: relative.stem,
    ):
        p = item.path
        stem_lower = p.stem.lower()
        hidden = any(kw in stem_lower for kw in _NON_CARD_KEYWORDS)
        result.append(AssetEntry(id=p.stem, label=_lorebook_label(p, p.stem), filename=p.name,
                                  kind="reality_lorebook", hidden=hidden, source_path=p))
    return result


def _scan_jailbreaks() -> list[AssetEntry]:
    paths = _paths()
    result = []
    for item in resolve_layered_files(
        paths.user_reality_dir() / "jailbreaks",
        _JAILBREAKS_DIR,
        logical_asset="reality_jailbreak",
        suffixes=(".json",),
        logical_id=lambda relative: relative.stem,
    ):
        p = item.path
        stem_lower = p.stem.lower()
        hidden = any(kw in stem_lower for kw in _NON_CARD_KEYWORDS)
        result.append(AssetEntry(id=p.stem, label=_jailbreak_label(p, p.stem), filename=p.name,
                                  kind="reality_jailbreak", hidden=hidden, source_path=p))
    return result


def _scan_dream_presets() -> list[AssetEntry]:
    paths = _paths()
    result = []
    seen_ids: set[str] = set()
    # Keep the legacy relative-root seam for tests that build a fake authored
    # tree without replacing the process-wide sandbox singleton.
    if paths.mode == "test":
        user_dir, fallback_dir = paths.user_dream_presets_dir(), _DREAM_PRESETS_DIR
    else:
        user_dir, fallback_dir = paths.dream_preset_read_dirs()
    for item in resolve_layered_files(
        user_dir,
        fallback_dir,
        logical_asset="dream_preset",
        suffixes=(".md",),
        logical_id=lambda relative: _dream_preset_logical_id(relative.stem),
    ):
        p = item.path
        stem = p.stem
        is_ascii = bool(_ASCII_ID_RE.fullmatch(stem))

        if is_ascii:
            asset_id = stem
            label = stem
        elif stem in _DREAM_PRESET_ID_MAP:
            asset_id, label = _DREAM_PRESET_ID_MAP[stem]
        else:
            # Non-ASCII stem with no id mapping: hide it — no stable ASCII id.
            logger.warning(
                "[asset_registry] dream preset %r has no ASCII id mapping; hiding it "
                "(add an entry to _DREAM_PRESET_ID_MAP to make it visible)",
                p.name,
            )
            result.append(AssetEntry(id=stem, label=stem, filename=p.name,
                                      kind="dream_preset", hidden=True, source_path=p))
            continue

        if asset_id in seen_ids:
            logger.warning(
                "[asset_registry] duplicate dream preset id %r; skipping %r", asset_id, p.name
            )
            continue
        seen_ids.add(asset_id)

        stem_lower = stem.lower()
        hidden = any(kw in stem_lower for kw in _NON_CARD_KEYWORDS)
        result.append(AssetEntry(id=asset_id, label=label, filename=p.name,
                                  kind="dream_preset", hidden=hidden, source_path=p))
    return result


# ── Registry class ────────────────────────────────────────────────────────────

class AssetRegistry:
    def __init__(self) -> None:
        self._by_id_kind: dict[tuple[str, str], AssetEntry] = {}
        self._reload()

    def _reload(self) -> None:
        entries = (
            _scan_characters()
            + _scan_lorebooks()
            + _scan_jailbreaks()
            + _scan_dream_presets()
        )
        self._by_id_kind = {(e.id, e.kind): e for e in entries}

    def resolve(self, asset_id: str, kind: str) -> AssetEntry:
        """Return AssetEntry for (id, kind). Raises ValueError if unknown."""
        entry = self._by_id_kind.get((asset_id, kind))
        if entry is None:
            valid = sorted(
                aid for aid, akind in self._by_id_kind if akind == kind
            )
            raise ValueError(
                f"unknown {kind} asset id {asset_id!r} "
                f"(available: {valid})"
            )
        return entry

    def list_all(self, kind: str) -> list[AssetEntry]:
        """Return all entries for kind (including hidden)."""
        return [e for e in self._by_id_kind.values() if e.kind == kind]

    def list_ui(self, kind: str) -> list[AssetEntry]:
        """Return non-hidden entries for UI listing."""
        return [e for e in self._by_id_kind.values()
                if e.kind == kind and not e.hidden]

    def id_exists(self, asset_id: str, kind: str) -> bool:
        return (asset_id, kind) in self._by_id_kind

    def normalize_legacy(self, value: str, kind: str) -> str:
        """Normalize a legacy config value to its canonical id.

        Handles:
          "yexuan"      -> "yexuan"        (already id)
          "yexuan.json" -> "yexuan"        (filename with extension)
          "叶瑄"         -> "yexuan"        (Chinese label lookup)
          "叶瑄.json"    -> "yexuan"        (Chinese label + extension)

        Returns the stem unchanged if no match is found (caller decides).
        """
        if not value:
            return value

        # Strip extension if present
        stem = Path(value).stem if "." in value else value

        # Fast path: already a known id
        if (stem, kind) in self._by_id_kind:
            return stem

        # Slow path: search by label (handles Chinese names)
        for (aid, akind), entry in self._by_id_kind.items():
            if akind == kind and (entry.label == value or entry.label == stem):
                return aid

        # Return stem as-is; caller gets a non-matching string and can error
        logger.warning(
            "[asset_registry] normalize_legacy: no match for "
            "value=%r kind=%r — returning stem %r as-is",
            value, kind, stem,
        )
        return stem


# ── Module-level singleton ────────────────────────────────────────────────────

_registry: AssetRegistry | None = None


def get_registry() -> AssetRegistry:
    global _registry
    if _registry is None:
        _registry = AssetRegistry()
    return _registry


def reload_registry() -> AssetRegistry:
    global _registry
    _registry = AssetRegistry()
    return _registry
