#!/usr/bin/env python3
"""
scripts/migrate_uid_only_memory_dry_run.py
==========================================
P2 dry-run: scan uid-only legacy memory locations and plan migration to
target_char_id="yexuan".  Never writes, moves, or deletes any data.

Usage:
  # stdout only
  python scripts/migrate_uid_only_memory_dry_run.py

  # stdout + JSON report file
  python scripts/migrate_uid_only_memory_dry_run.py --output report.json

Report actions per uid x artifact:
  copy     : source exists, target doesn't → safe to copy
  conflict : source exists AND target exists → don't overwrite, needs review
  skip     : source doesn't exist, target exists → already migrated / fresh start
  missing  : neither source nor target exists → nothing to do

Excluded from scan (per P2 contract):
  - character_growth (legacy/dead)
  - dream session structure
  - active_prompt_assets source of truth
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from core.data_paths import DataPaths, safe_user_id
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope

# Only valid migration target.  Other chars keep empty histories.
_TARGET_CHAR_ID = "yexuan"

# ── Legacy uid-only source path definitions ──────────────────────────────────
# All paths listed here have uid in filename/dirname but NO char_id in the
# directory tree.  Defined centrally here; never re-imported into core stores.

# (legacy_dir_under_data_root, filename_template, artifact_key, is_dir)
_LEGACY_FILE_SOURCES: list[tuple[str, str, str]] = [
    ("history",            "{uid}.json",  "history"),
    ("mid_term",           "{uid}.json",  "mid_term"),
    ("episodic_memory",    "{uid}.json",  "episodic"),
    ("memory_index",       "{uid}.json",  "memory_index"),
    ("profiles",           "{uid}.json",  "profile"),
    ("user_identity",      "{uid}.yaml",  "identity"),
    ("fixation_state",     "{uid}.json",  "fixation_state"),
    ("user_hidden_state",  "{uid}.json",  "hidden_state"),
]

_LEGACY_DIR_SOURCES: list[tuple[str, str, str]] = [
    ("event_log", "{uid}", "event_log"),
]

# Flat registry consumed by build_entry / collect helpers.
_ALL_LEGACY: list[tuple[str, str, str, bool]] = (
    [(d, t, a, False) for d, t, a in _LEGACY_FILE_SOURCES]
    + [(d, t, a, True) for d, t, a in _LEGACY_DIR_SOURCES]
)


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class ReportEntry:
    uid: str
    artifact: str
    source_path: str
    target_path: str
    source_exists: bool
    target_exists: bool
    would_overwrite: bool
    action: str          # copy | conflict | skip | missing


# ── Path helpers ──────────────────────────────────────────────────────────────

def _abs(p: Path) -> Path:
    """Resolve relative paths against _ROOT; absolute paths pass through."""
    if p.is_absolute():
        return p
    return (_ROOT / p).resolve()


def _get_data_root() -> Path:
    """Return the absolute data root for production mode."""
    dp = DataPaths(mode="production")
    return _abs(dp._base)


# ── UID collection ────────────────────────────────────────────────────────────

def _collect_legacy_uids(data_root: Path) -> set[str]:
    """Scan all legacy uid-only directories and collect uid stems."""
    uids: set[str] = set()
    _EXTENSIONS = (".json", ".yaml", ".txt")
    for legacy_dir, _tmpl, _artifact, _is_dir in _ALL_LEGACY:
        d = data_root / legacy_dir
        if not d.exists():
            continue
        for entry in d.iterdir():
            stem = entry.name
            for ext in _EXTENSIONS:
                if stem.endswith(ext):
                    stem = stem[: -len(ext)]
                    break
            try:
                uids.add(safe_user_id(stem))
            except ValueError:
                pass
    return uids


def _collect_v1_uids(data_root: Path) -> set[str]:
    """Collect UIDs from existing v1 runtime/memory/{target_char_id}/ dirs."""
    uids: set[str] = set()
    mem_root = data_root / "runtime" / "memory" / _TARGET_CHAR_ID
    if not mem_root.exists():
        return uids
    for entry in mem_root.iterdir():
        if entry.is_dir():
            try:
                uids.add(safe_user_id(entry.name))
            except ValueError:
                pass
    return uids


def collect_all_uids(data_root: Path) -> list[str]:
    """Return sorted union of legacy uid-only UIDs and v1-scoped UIDs."""
    return sorted(_collect_legacy_uids(data_root) | _collect_v1_uids(data_root))


# ── Entry builder ─────────────────────────────────────────────────────────────

def _determine_action(source_exists: bool, target_exists: bool) -> str:
    if source_exists and not target_exists:
        return "copy"
    if source_exists and target_exists:
        return "conflict"
    if not source_exists and target_exists:
        return "skip"
    return "missing"


def build_entry(
    uid: str,
    legacy_dir: str,
    tmpl: str,
    artifact: str,
    is_dir: bool,
    data_root: Path,
) -> ReportEntry:
    """Build a dry-run report entry for one uid x artifact pair.

    source_path: derived from legacy uid-only location (no char_id in path).
    target_path: derived via MemoryScope.reality_scope(uid, yexuan) + resolve_path.
    Never reads or writes any file.
    """
    source_abs = data_root / legacy_dir / tmpl.format(uid=uid)

    scope = MemoryScope.reality_scope(uid, _TARGET_CHAR_ID)
    target_abs = _abs(resolve_path(scope, artifact))

    source_exists = source_abs.exists()
    target_exists = target_abs.exists()
    would_overwrite = source_exists and target_exists
    action = _determine_action(source_exists, target_exists)

    return ReportEntry(
        uid=uid,
        artifact=artifact,
        source_path=str(source_abs),
        target_path=str(target_abs),
        source_exists=source_exists,
        target_exists=target_exists,
        would_overwrite=would_overwrite,
        action=action,
    )


def build_report(uids: list[str], data_root: Path) -> list[ReportEntry]:
    """Build full dry-run report for all uids across all legacy artifacts."""
    entries: list[ReportEntry] = []
    for uid in uids:
        for legacy_dir, tmpl, artifact, is_dir in _ALL_LEGACY:
            entries.append(build_entry(uid, legacy_dir, tmpl, artifact, is_dir, data_root))
    return entries


# ── Summary + output ──────────────────────────────────────────────────────────

def _summary(entries: list[ReportEntry]) -> dict[str, int]:
    counts: dict[str, int] = {"copy": 0, "conflict": 0, "skip": 0, "missing": 0}
    for e in entries:
        counts[e.action] = counts.get(e.action, 0) + 1
    return counts


def print_report(entries: list[ReportEntry]) -> None:
    """Print human-readable report to stdout.  Never touches data/."""
    uids = sorted({e.uid for e in entries})
    print(
        f"[dry-run] uid-only memory migration inventory"
        f"  target_char_id={_TARGET_CHAR_ID!r}"
        f"  uids={len(uids)}"
        f"  entries={len(entries)}"
    )
    print()

    current_uid: str | None = None
    for e in entries:
        if e.uid != current_uid:
            current_uid = e.uid
            print(f"  uid={e.uid}")
        src_tag = "+" if e.source_exists else "-"
        tgt_tag = "+" if e.target_exists else "-"
        print(
            f"    [{e.action:8s}] {e.artifact:20s}"
            f"  src[{src_tag}] {e.source_path}"
            f"  →  tgt[{tgt_tag}] {e.target_path}"
        )

    summary = _summary(entries)
    print()
    print(f"[dry-run] summary: {summary}")


def save_report(entries: list[ReportEntry], output_path: Path) -> None:
    """Serialize report to JSON.  Creates parent dirs; never touches data/."""
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_char_id": _TARGET_CHAR_ID,
        "entry_count": len(entries),
        "summary": _summary(entries),
        "entries": [asdict(e) for e in entries],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[dry-run] report saved → {output_path}")


def run(
    output_path: Path | None = None,
    data_root: Path | None = None,
) -> list[ReportEntry]:
    """Run the dry-run scan.  Never writes to data/.

    data_root defaults to the production data/ directory.
    output_path, if given, receives a JSON report file.
    Returns the list of ReportEntry for programmatic use / tests.
    """
    if data_root is None:
        data_root = _get_data_root()

    uids = collect_all_uids(data_root)
    if not uids:
        print("[dry-run] no UIDs found — nothing to report")
        return []

    entries = build_report(uids, data_root)
    print_report(entries)

    if output_path is not None:
        save_report(entries, output_path)

    return entries


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Dry-run inventory: scan uid-only legacy memory locations "
            "and plan migration to yexuan scope.  Never writes data."
        )
    )
    ap.add_argument(
        "--output",
        metavar="PATH",
        help="optional path to save JSON report (e.g. report.json)",
    )
    args = ap.parse_args()
    output_path = Path(args.output) if args.output else None
    run(output_path=output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
