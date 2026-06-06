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

logger = logging.getLogger(__name__)

_CHARACTERS_DIR = Path("characters")
_LOREBOOKS_DIR = _CHARACTERS_DIR / "reality" / "lorebooks"
_JAILBREAKS_DIR = _CHARACTERS_DIR / "reality" / "jailbreaks"
_DREAM_PRESETS_DIR = _CHARACTERS_DIR / "dream_presets"
_AVATARS_DIR = _CHARACTERS_DIR / "reality" / "avatars"

# Stem substrings that mark non-rolecard / scaffold files
_NON_CARD_KEYWORDS = frozenset({"template", "author_notes", "example"})

# Dream-preset ids must be alphanumeric (matches existing PATCH validation)
_ASCII_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Stable ASCII-id mapping for dream presets with non-ASCII filenames.
# key = file stem (Chinese), value = (ascii_id, display_label)
# Add new entries here when a new Chinese-named preset is authored.
_DREAM_PRESET_ID_MAP: dict[str, tuple[str, str]] = {
    "审讯": ("interrogation", "审讯"),
    "多p":  ("multi",         "多p"),
}


@dataclass(frozen=True)
class AssetEntry:
    id: str
    label: str
    filename: str
    kind: str
    hidden: bool
    avatar_url: str | None = None
    has_runtime_avatar: bool = False

    def as_ui_dict(self) -> dict:
        d: dict = {"id": self.id, "label": self.label, "kind": self.kind, "avatar_url": self.avatar_url}
        if self.kind == "character":
            d["has_runtime_avatar"] = self.has_runtime_avatar
        return d

    def path(self) -> Path:
        if self.kind == "character":
            return _CHARACTERS_DIR / self.filename
        if self.kind == "reality_lorebook":
            return _LOREBOOKS_DIR / self.filename
        if self.kind == "reality_jailbreak":
            return _JAILBREAKS_DIR / self.filename
        if self.kind == "dream_preset":
            return _DREAM_PRESETS_DIR / self.filename
        raise ValueError(f"unknown asset kind: {self.kind!r}")


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
    authored = _AVATARS_DIR / f"{char_id}.png"
    if authored.exists():
        mtime = int(authored.stat().st_mtime)
        return (f"/settings/character-avatar/{char_id}?v={mtime}", False)
    return (None, False)


def _scan_characters() -> list[AssetEntry]:
    if not _CHARACTERS_DIR.exists():
        return []
    result = []
    for p in sorted(_CHARACTERS_DIR.glob("*.json")):
        stem_lower = p.stem.lower()
        hidden = any(kw in stem_lower for kw in _NON_CARD_KEYWORDS)
        label = p.stem
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            label = data.get("name") or p.stem
        except Exception:
            pass
        avatar_url, has_runtime = _avatar_info_for(p.stem)
        result.append(AssetEntry(id=p.stem, label=label, filename=p.name,
                                  kind="character", hidden=hidden,
                                  avatar_url=avatar_url,
                                  has_runtime_avatar=has_runtime))
    # Also scan .txt and .md character cards
    for ext in ("*.txt", "*.md"):
        for p in sorted(_CHARACTERS_DIR.glob(ext)):
            stem_lower = p.stem.lower()
            hidden = any(kw in stem_lower for kw in _NON_CARD_KEYWORDS)
            avatar_url, has_runtime = _avatar_info_for(p.stem)
            result.append(AssetEntry(id=p.stem, label=p.stem, filename=p.name,
                                      kind="character", hidden=hidden,
                                      avatar_url=avatar_url,
                                      has_runtime_avatar=has_runtime))
    return result


def _scan_lorebooks() -> list[AssetEntry]:
    if not _LOREBOOKS_DIR.exists():
        return []
    result = []
    for p in sorted(_LOREBOOKS_DIR.glob("*.yaml")):
        stem_lower = p.stem.lower()
        hidden = any(kw in stem_lower for kw in _NON_CARD_KEYWORDS)
        result.append(AssetEntry(id=p.stem, label=p.stem, filename=p.name,
                                  kind="reality_lorebook", hidden=hidden))
    return result


def _scan_jailbreaks() -> list[AssetEntry]:
    if not _JAILBREAKS_DIR.exists():
        return []
    result = []
    for p in sorted(_JAILBREAKS_DIR.glob("*.json")):
        stem_lower = p.stem.lower()
        hidden = any(kw in stem_lower for kw in _NON_CARD_KEYWORDS)
        result.append(AssetEntry(id=p.stem, label=p.stem, filename=p.name,
                                  kind="reality_jailbreak", hidden=hidden))
    return result


def _scan_dream_presets() -> list[AssetEntry]:
    if not _DREAM_PRESETS_DIR.exists():
        return []
    result = []
    seen_ids: set[str] = set()
    for p in sorted(_DREAM_PRESETS_DIR.glob("*.md")):
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
                                      kind="dream_preset", hidden=True))
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
                                  kind="dream_preset", hidden=hidden))
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
