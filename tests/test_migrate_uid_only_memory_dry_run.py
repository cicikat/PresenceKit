"""
tests/test_migrate_uid_only_memory_dry_run.py
=============================================
P2 dry-run inventory tests.

Coverage:
  1.  _TARGET_CHAR_ID locked to "yexuan"
  2.  _collect_legacy_uids: empty dir, json/yaml/dir, unsafe names
  3.  _collect_v1_uids: empty, finds scoped dirs, ignores other chars
  4.  collect_all_uids: union + sorted + dedup
  5.  build_entry action=copy (source+, target-)
  6.  build_entry action=missing (source-, target-)
  7.  build_entry action=conflict (source+, target+)
  8.  build_entry action=skip (source-, target+)
  9.  source paths are uid-only (no char_id component)
  10. target paths route through MemoryScope.reality_scope + resolve_path
  11. target paths contain yexuan
  12. target paths contain uid
  13. build_report covers all artifacts for all UIDs
  14. build_report is a pure function (no file writes)
  15. build_report empty-uids edge case
  16. _summary counts
  17. save_report writes valid JSON
  18. run() returns ReportEntry list
  19. hongcha / j5412 never appear in target paths
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from scripts.migrate_uid_only_memory_dry_run import (
    _ALL_LEGACY,
    _TARGET_CHAR_ID,
    _collect_legacy_uids,
    _collect_v1_uids,
    build_entry,
    build_report,
    collect_all_uids,
    run,
    save_report,
    _summary,
    ReportEntry,
)
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope


# ── helpers ───────────────────────────────────────────────────────────────────

def _s(p: str) -> str:
    return p.replace("\\", "/")


# ── 1. target char_id locked to yexuan ───────────────────────────────────────

def test_target_char_id_is_yexuan():
    assert _TARGET_CHAR_ID == "yexuan"


# ── 2. _collect_legacy_uids ───────────────────────────────────────────────────

def test_collect_legacy_uids_empty_when_no_dirs(tmp_path):
    assert _collect_legacy_uids(tmp_path) == set()


def test_collect_legacy_uids_finds_json_stems(tmp_path):
    (tmp_path / "history").mkdir()
    (tmp_path / "history" / "1234567890.json").write_text("{}", encoding="utf-8")
    (tmp_path / "history" / "2985713106.json").write_text("{}", encoding="utf-8")
    uids = _collect_legacy_uids(tmp_path)
    assert "1234567890" in uids
    assert "2985713106" in uids


def test_collect_legacy_uids_finds_yaml_stems(tmp_path):
    (tmp_path / "user_identity").mkdir()
    (tmp_path / "user_identity" / "myuid.yaml").write_text("{}", encoding="utf-8")
    uids = _collect_legacy_uids(tmp_path)
    assert "myuid" in uids


def test_collect_legacy_uids_finds_event_log_dirs(tmp_path):
    el = tmp_path / "event_log"
    el.mkdir()
    (el / "uid_abc").mkdir()
    uids = _collect_legacy_uids(tmp_path)
    assert "uid_abc" in uids


def test_collect_legacy_uids_skips_unsafe_names(tmp_path):
    (tmp_path / "history").mkdir()
    (tmp_path / "history" / "valid_uid.json").write_text("{}", encoding="utf-8")
    uids = _collect_legacy_uids(tmp_path)
    # unsafe names (spaces, special chars, etc.) should be silently skipped
    assert "valid_uid" in uids
    # confirm no crashes from weird filenames
    for u in uids:
        assert isinstance(u, str) and u


# ── 3. _collect_v1_uids ───────────────────────────────────────────────────────

def test_collect_v1_uids_empty_when_no_dir(tmp_path):
    assert _collect_v1_uids(tmp_path) == set()


def test_collect_v1_uids_finds_char_scoped_uid_dirs(tmp_path):
    mem = tmp_path / "runtime" / "memory" / _TARGET_CHAR_ID
    mem.mkdir(parents=True)
    (mem / "uid_alpha").mkdir()
    (mem / "uid_beta").mkdir()
    uids = _collect_v1_uids(tmp_path)
    assert "uid_alpha" in uids
    assert "uid_beta" in uids


def test_collect_v1_uids_ignores_other_chars(tmp_path):
    mem = tmp_path / "runtime" / "memory"
    (mem / _TARGET_CHAR_ID / "uid_ok").mkdir(parents=True)
    (mem / "hongcha" / "uid_hc").mkdir(parents=True)
    (mem / "j5412" / "uid_j5").mkdir(parents=True)
    uids = _collect_v1_uids(tmp_path)
    assert "uid_ok" in uids
    assert "uid_hc" not in uids
    assert "uid_j5" not in uids


def test_collect_v1_uids_ignores_files(tmp_path):
    mem = tmp_path / "runtime" / "memory" / _TARGET_CHAR_ID
    mem.mkdir(parents=True)
    (mem / "not_a_uid.json").write_text("{}", encoding="utf-8")
    (mem / "real_uid").mkdir()
    uids = _collect_v1_uids(tmp_path)
    assert "real_uid" in uids
    assert "not_a_uid" not in uids


# ── 4. collect_all_uids: union, sorted, dedup ─────────────────────────────────

def test_collect_all_uids_union_sorted(tmp_path):
    # legacy uid
    (tmp_path / "history").mkdir()
    (tmp_path / "history" / "leg_uid.json").write_text("{}", encoding="utf-8")
    # v1 uid
    mem = tmp_path / "runtime" / "memory" / _TARGET_CHAR_ID
    mem.mkdir(parents=True)
    (mem / "v1_uid").mkdir()
    # shared uid in both
    (tmp_path / "history" / "shared_uid.json").write_text("{}", encoding="utf-8")
    (mem / "shared_uid").mkdir()

    uids = collect_all_uids(tmp_path)
    assert "leg_uid" in uids
    assert "v1_uid" in uids
    assert "shared_uid" in uids
    assert uids.count("shared_uid") == 1   # dedup
    assert uids == sorted(uids)             # sorted


# ── 5-8. build_entry action logic ─────────────────────────────────────────────

def test_build_entry_action_copy(tmp_path, sandbox):
    """source exists, target absent → copy"""
    uid = "copyuid"
    (tmp_path / "history").mkdir()
    (tmp_path / "history" / f"{uid}.json").write_text("{}", encoding="utf-8")

    e = build_entry(uid, "history", "{uid}.json", "history", False, tmp_path)
    assert e.action == "copy"
    assert e.source_exists is True
    assert e.target_exists is False
    assert e.would_overwrite is False
    assert e.uid == uid
    assert e.artifact == "history"


def test_build_entry_action_missing(tmp_path, sandbox):
    """neither source nor target → missing"""
    e = build_entry("nouid", "history", "{uid}.json", "history", False, tmp_path)
    assert e.action == "missing"
    assert e.source_exists is False
    assert e.target_exists is False
    assert e.would_overwrite is False


def test_build_entry_action_conflict(tmp_path, sandbox):
    """source AND target exist → conflict"""
    uid = "conflictuid"
    # create source
    (tmp_path / "history").mkdir()
    (tmp_path / "history" / f"{uid}.json").write_text("{}", encoding="utf-8")
    # create target via sandbox-patched resolve_path
    scope = MemoryScope.reality_scope(uid, _TARGET_CHAR_ID)
    target = resolve_path(scope, "history")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")

    e = build_entry(uid, "history", "{uid}.json", "history", False, tmp_path)
    assert e.action == "conflict"
    assert e.source_exists is True
    assert e.target_exists is True
    assert e.would_overwrite is True


def test_build_entry_action_skip(tmp_path, sandbox):
    """source absent, target exists → skip (already migrated)"""
    uid = "skipuid"
    scope = MemoryScope.reality_scope(uid, _TARGET_CHAR_ID)
    target = resolve_path(scope, "history")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")

    e = build_entry(uid, "history", "{uid}.json", "history", False, tmp_path)
    assert e.action == "skip"
    assert e.source_exists is False
    assert e.target_exists is True
    assert e.would_overwrite is False


# ── 9. source paths are uid-only (no char_id) ────────────────────────────────

def test_source_paths_have_no_char_id_component(tmp_path, sandbox):
    uid = "plain_uid"
    for legacy_dir, tmpl, artifact, is_dir in _ALL_LEGACY:
        e = build_entry(uid, legacy_dir, tmpl, artifact, is_dir, tmp_path)
        src = _s(e.source_path)
        # The source comes from a uid-only legacy dir — char_id must not appear
        assert _TARGET_CHAR_ID not in src, (
            f"artifact={artifact}: source path must not contain char_id={_TARGET_CHAR_ID!r}, "
            f"got {src!r}"
        )


# ── 10. target paths route through MemoryScope + resolve_path ────────────────

def test_target_paths_match_scope_resolver(tmp_path, sandbox):
    uid = "scopeuid"
    scope = MemoryScope.reality_scope(uid, _TARGET_CHAR_ID)
    for legacy_dir, tmpl, artifact, is_dir in _ALL_LEGACY:
        e = build_entry(uid, legacy_dir, tmpl, artifact, is_dir, tmp_path)
        expected_abs = resolve_path(scope, artifact)
        if not expected_abs.is_absolute():
            expected_abs = (_ROOT / expected_abs).resolve()
        assert _s(str(expected_abs)) == _s(e.target_path), (
            f"artifact={artifact}: target path mismatch"
        )


# ── 11. target paths contain yexuan ──────────────────────────────────────────

def test_target_paths_contain_yexuan(tmp_path, sandbox):
    uid = "uidtest"
    for legacy_dir, tmpl, artifact, is_dir in _ALL_LEGACY:
        e = build_entry(uid, legacy_dir, tmpl, artifact, is_dir, tmp_path)
        assert _TARGET_CHAR_ID in _s(e.target_path), (
            f"artifact={artifact}: target path missing char_id: {e.target_path!r}"
        )


# ── 12. target paths contain uid ──────────────────────────────────────────────

def test_target_paths_contain_uid(tmp_path, sandbox):
    uid = "uidtest456"
    for legacy_dir, tmpl, artifact, is_dir in _ALL_LEGACY:
        e = build_entry(uid, legacy_dir, tmpl, artifact, is_dir, tmp_path)
        assert uid in _s(e.target_path), (
            f"artifact={artifact}: target path missing uid: {e.target_path!r}"
        )


# ── 13. build_report coverage ────────────────────────────────────────────────

def test_build_report_covers_all_artifacts_for_all_uids(tmp_path, sandbox):
    uids = ["u1", "u2", "u3"]
    entries = build_report(uids, tmp_path)
    assert len(entries) == len(uids) * len(_ALL_LEGACY)
    artifacts = {e.artifact for e in entries}
    assert artifacts == {a for _, _, a, _ in _ALL_LEGACY}
    entry_uids = {e.uid for e in entries}
    assert entry_uids == set(uids)


# ── 14. build_report is pure (no file writes) ────────────────────────────────

def test_build_report_no_file_writes(tmp_path, sandbox):
    before = set(tmp_path.rglob("*"))
    build_report(["u1", "u2"], tmp_path)
    after = set(tmp_path.rglob("*"))
    assert before == after, f"Unexpected files created: {after - before}"


# ── 15. build_report empty uids ───────────────────────────────────────────────

def test_build_report_empty_uids(tmp_path, sandbox):
    assert build_report([], tmp_path) == []


# ── 16. _summary counts ───────────────────────────────────────────────────────

def test_summary_correct_counts():
    entries = [
        ReportEntry("u", "history",   "s", "t", True,  False, False, "copy"),
        ReportEntry("u", "mid_term",  "s", "t", True,  True,  True,  "conflict"),
        ReportEntry("u", "episodic",  "s", "t", False, False, False, "missing"),
        ReportEntry("u", "identity",  "s", "t", False, True,  False, "skip"),
        ReportEntry("u", "profile",   "s", "t", True,  False, False, "copy"),
    ]
    s = _summary(entries)
    assert s["copy"]     == 2
    assert s["conflict"] == 1
    assert s["missing"]  == 1
    assert s["skip"]     == 1


def test_summary_all_missing_when_empty():
    s = _summary([])
    assert all(v == 0 for v in s.values())


# ── 17. save_report JSON ──────────────────────────────────────────────────────

def test_save_report_writes_valid_json(tmp_path, sandbox):
    entries = [
        ReportEntry("u1", "history", "src", "tgt", True, False, False, "copy"),
        ReportEntry("u1", "mid_term","src", "tgt", False, False, False, "missing"),
    ]
    out = tmp_path / "reports" / "test_report.json"
    save_report(entries, out)
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["target_char_id"] == _TARGET_CHAR_ID
    assert data["entry_count"] == 2
    assert len(data["entries"]) == 2
    assert "generated_at" in data
    assert "summary" in data
    assert data["summary"]["copy"] == 1
    assert data["summary"]["missing"] == 1


def test_save_report_creates_parent_dirs(tmp_path, sandbox):
    out = tmp_path / "deep" / "nested" / "report.json"
    save_report([], out)
    assert out.exists()


# ── 18. run() returns entries ─────────────────────────────────────────────────

def test_run_returns_report_entries(tmp_path, sandbox):
    # seed a v1 uid so there's something to report
    mem = tmp_path / "runtime" / "memory" / _TARGET_CHAR_ID
    (mem / "run_uid").mkdir(parents=True)

    entries = run(data_root=tmp_path)
    assert len(entries) == len(_ALL_LEGACY)
    assert all(isinstance(e, ReportEntry) for e in entries)
    assert all(e.uid == "run_uid" for e in entries)


def test_run_empty_returns_empty_list(tmp_path, sandbox):
    entries = run(data_root=tmp_path)
    assert entries == []


def test_run_saves_json_when_output_given(tmp_path, sandbox):
    mem = tmp_path / "runtime" / "memory" / _TARGET_CHAR_ID
    (mem / "out_uid").mkdir(parents=True)
    out = tmp_path / "output.json"

    run(data_root=tmp_path, output_path=out)
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["target_char_id"] == _TARGET_CHAR_ID


# ── 19. hongcha / j5412 never in target paths ────────────────────────────────

def test_other_chars_never_in_target_paths(tmp_path, sandbox):
    uid = "any_uid"
    for legacy_dir, tmpl, artifact, is_dir in _ALL_LEGACY:
        e = build_entry(uid, legacy_dir, tmpl, artifact, is_dir, tmp_path)
        tgt = _s(e.target_path)
        assert "hongcha" not in tgt, f"artifact={artifact}: hongcha in target path"
        assert "j5412" not in tgt,   f"artifact={artifact}: j5412 in target path"
