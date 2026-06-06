"""
tests/test_memory_direct_path_lint.py — T-14B

Static lint guard: prevents new direct-path constructions of scoped memory artifacts
in production code (core/ and admin/).  All such paths must go through
core/memory/path_resolver.resolve_path() rather than calling
get_paths().user_memory_root() / artifact_filename directly.

Detection rule
--------------
A line is flagged if it simultaneously contains:
  1.  "user_memory_root(" — direct call to the DataPaths helper, bypassing the resolver
  2.  / "managed_artifact" or / 'managed_artifact' — Path division to a known artifact name

Allowlisted files (exempt from all checks):
  core/memory/path_resolver.py   — resolver itself, intentionally contains direct calls
  core/sandbox.py                — singleton re-exporter; defines no artifact paths
  core/data_paths.py             — defines user_memory_root / memory_char_root APIs
  core/memory/character_growth.py — legacy/dead tool, not in active pipeline (P1-2J)

Known violations (P1-3C): all three event_log violations fixed — table now empty.

A test fails only if violations appear in files NOT covered by the known-violations table,
or if a known file gains a new artifact beyond what is listed.
"""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import Iterator

_ROOT = Path(__file__).parent.parent

# ── Allowlist (files entirely exempt from the lint check) ────────────────────

_ALLOWLIST_REL: frozenset[str] = frozenset({
    # resolver — intentionally constructs user_memory_root(...) / artifact paths
    "core/memory/path_resolver.py",
    # get_paths() singleton; no artifact paths
    "core/sandbox.py",
    # DataPaths class defines user_memory_root / memory_char_root; not a caller
    "core/data_paths.py",
    # legacy/dead tool — not in active pipeline, kept for compat (P1-2J decision)
    "core/memory/character_growth.py",
})

# ── Managed scoped artifact names ─────────────────────────────────────────────
# These are the filenames (or directory names) that the resolver owns.
# Any direct path construction for these is a violation.

_MANAGED_ARTIFACT_NAMES: frozenset[str] = frozenset({
    "history.json",
    "mid_term.json",
    "episodic.json",
    "memory_index.json",
    "fixation_state.json",
    "profile.json",
    "identity.yaml",
    "hidden_state.json",
    "afterglow_residue.json",
    "event_log",          # directory artifact; resolver returns the dir Path
})

# ── Known violations (existing, cannot be fixed per T-14B hard rules) ────────
# Maps relative path (forward-slash) → frozenset of artifact names allowed to appear.
# Tests pass if each known file's violations are a subset of the listed artifacts.
# Tests FAIL if a file not listed here has violations, OR if a listed file gains
# a new artifact beyond those recorded here.

_KNOWN_VIOLATIONS: dict[str, frozenset[str]] = {
    # P1-3C: all three event_log violations fixed.
}

# ── Migrated stores (must be violation-free) ──────────────────────────────────

_MIGRATED_STORES: tuple[str, ...] = (
    "core/memory/short_term.py",
    "core/memory/mid_term.py",
    "core/memory/episodic_memory.py",
    "core/memory/user_profile.py",
    "core/memory/user_identity.py",
    "core/memory/user_hidden_state_store.py",
    "core/memory/event_log.py",
    "core/memory/fixation_pipeline.py",
)


# ── Core detection function ───────────────────────────────────────────────────

def _scan_text(source: str) -> list[tuple[int, str, str]]:
    """Scan source text; return (lineno, artifact, stripped_line) for each violation.

    A line is a violation when it contains BOTH:
      - "user_memory_root(" (direct DataPaths call, bypasses resolver)
      - / "artifact" or / 'artifact' where artifact is in _MANAGED_ARTIFACT_NAMES

    Comment-only lines (starting with #) are skipped.
    Docstring lines that mention "user_memory_root(...)/ artifact" without Python
    path-division quotes are NOT caught (docstrings use unquoted names in this codebase),
    so no docstring stripping is needed.
    """
    results: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "user_memory_root(" not in line:
            continue
        for artifact in _MANAGED_ARTIFACT_NAMES:
            if f'/ "{artifact}"' in line or f"/ '{artifact}'" in line:
                results.append((lineno, artifact, stripped))
                break  # one flag per line is enough
    return results


def _iter_production_py(subdir: str) -> Iterator[Path]:
    base = _ROOT / subdir
    if not base.exists():
        return
    for p in base.rglob("*.py"):
        rel = p.relative_to(_ROOT).as_posix()
        if rel not in _ALLOWLIST_REL:
            yield p


def _scan_production() -> dict[str, list[tuple[int, str, str]]]:
    """Return {rel_path: [(lineno, artifact, line), ...]} for all violations found."""
    found: dict[str, list[tuple[int, str, str]]] = {}
    for subdir in ("core", "admin"):
        for path in _iter_production_py(subdir):
            rel = path.relative_to(_ROOT).as_posix()
            hits = _scan_text(path.read_text(encoding="utf-8"))
            if hits:
                found[rel] = hits
    return found


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Detector unit tests (fake snippets)
# ═══════════════════════════════════════════════════════════════════════════════

class TestLintDetector:
    def test_catches_profile_json_violation(self):
        src = 'p = get_paths().user_memory_root(uid, char_id=char_id) / "profile.json"\n'
        hits = _scan_text(src)
        assert hits, "detector must flag user_memory_root(...) / \"profile.json\""
        assert hits[0][1] == "profile.json"

    def test_catches_history_json_violation(self):
        src = 'f = get_paths().user_memory_root(uid, char_id=c) / "history.json"\n'
        hits = _scan_text(src)
        assert hits
        assert hits[0][1] == "history.json"

    def test_catches_event_log_violation(self):
        src = 'new = get_paths().user_memory_root(uid) / "event_log"\n'
        hits = _scan_text(src)
        assert hits
        assert hits[0][1] == "event_log"

    def test_catches_event_log_with_date_suffix(self):
        src = 'p = get_paths().user_memory_root(uid) / "event_log" / f"{today}.md"\n'
        hits = _scan_text(src)
        assert hits
        assert hits[0][1] == "event_log"

    def test_catches_single_quoted_artifact(self):
        src = "p = get_paths().user_memory_root(uid) / 'hidden_state.json'\n"
        hits = _scan_text(src)
        assert hits
        assert hits[0][1] == "hidden_state.json"

    def test_does_not_flag_resolve_path(self):
        """resolve_path(scope, "profile") is the correct pattern — must not be flagged."""
        src = 'p = resolve_path(scope, "profile")\n'
        hits = _scan_text(src)
        assert not hits, "resolve_path must not be flagged"

    def test_does_not_flag_non_managed_artifact(self):
        """diary_context.txt and reminders.json are not managed artifacts."""
        src1 = 'p = get_paths().user_memory_root(uid, char_id=c) / "diary_context.txt"\n'
        src2 = 'p = get_paths().user_memory_root(uid, char_id=c) / "reminders.json"\n'
        assert not _scan_text(src1), "diary_context.txt is not a managed artifact"
        assert not _scan_text(src2), "reminders.json is not a managed artifact"

    def test_does_not_flag_comment_line(self):
        src = '# p = get_paths().user_memory_root(uid) / "profile.json"\n'
        hits = _scan_text(src)
        assert not hits, "comment lines must not be flagged"

    def test_does_not_flag_docstring_without_quotes(self):
        """Docstrings in this codebase use unquoted artifact names — must not be flagged."""
        src = '    Path: user_memory_root(uid, char_id=char_id) / hidden_state.json\n'
        hits = _scan_text(src)
        assert not hits, "docstring-style reference without Python quotes must not be flagged"

    def test_does_not_flag_path_resolver_content(self):
        """The resolver itself has many user_memory_root(...) / "artifact" lines — allowlisted."""
        resolver = (_ROOT / "core/memory/path_resolver.py").read_text(encoding="utf-8")
        # Without allowlist the file has many violations — that's expected.
        hits = _scan_text(resolver)
        assert hits, "path_resolver.py must have violations in isolation (proves detector works)"
        # But it is in the allowlist, so _scan_production won't include it.


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Allowlist verification
# ═══════════════════════════════════════════════════════════════════════════════

class TestAllowlist:
    def test_path_resolver_excluded_from_scan(self):
        """path_resolver.py must not appear in production scan results."""
        found = _scan_production()
        assert "core/memory/path_resolver.py" not in found

    def test_character_growth_excluded_from_scan(self):
        found = _scan_production()
        assert "core/memory/character_growth.py" not in found

    def test_sandbox_excluded_from_scan(self):
        found = _scan_production()
        assert "core/sandbox.py" not in found

    def test_data_paths_excluded_from_scan(self):
        found = _scan_production()
        assert "core/data_paths.py" not in found


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Migrated stores must be violation-free
# ═══════════════════════════════════════════════════════════════════════════════

class TestMigratedStoresClean:
    """Each migrated store routes all scoped paths through resolve_path.

    A violation here means the store was partially reverted or a direct call
    was added in a refactor — the test catches it immediately.
    """

    def _check_store(self, rel_path: str) -> None:
        path = _ROOT / rel_path
        assert path.exists(), f"migrated store not found: {rel_path}"
        hits = _scan_text(path.read_text(encoding="utf-8"))
        assert not hits, (
            f"{rel_path} has direct user_memory_root(...)/artifact path constructions:\n"
            + "\n".join(f"  line {ln}: {line}" for ln, _, line in hits)
        )

    def test_short_term_clean(self):
        self._check_store("core/memory/short_term.py")

    def test_mid_term_clean(self):
        self._check_store("core/memory/mid_term.py")

    def test_episodic_memory_clean(self):
        self._check_store("core/memory/episodic_memory.py")

    def test_user_profile_clean(self):
        self._check_store("core/memory/user_profile.py")

    def test_user_identity_clean(self):
        self._check_store("core/memory/user_identity.py")

    def test_user_hidden_state_store_clean(self):
        # user_hidden_state_store.py has "user_memory_root(...)" in docstrings but
        # without Python path-division quotes, so _scan_text does not flag them.
        self._check_store("core/memory/user_hidden_state_store.py")

    def test_event_log_clean(self):
        # event_log.py uses resolve_path(scope, "event_log") then appends date strings —
        # that is the correct pattern and must not show user_memory_root + artifact.
        self._check_store("core/memory/event_log.py")

    def test_fixation_pipeline_clean(self):
        self._check_store("core/memory/fixation_pipeline.py")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Production-wide regression guard
# ═══════════════════════════════════════════════════════════════════════════════

class TestProductionLint:
    """Scans all core/ and admin/ .py files (excluding allowlist).

    Each test passes if violations match the known-violations table exactly.
    A failure means a NEW violation was introduced — fix it before merging.
    """

    def test_no_unknown_file_violations(self):
        """No production file outside _KNOWN_VIOLATIONS may have direct-path violations."""
        found = _scan_production()
        unknown_files = {f: hits for f, hits in found.items() if f not in _KNOWN_VIOLATIONS}
        assert not unknown_files, (
            "New direct-path violations found (must use resolve_path instead):\n"
            + "\n".join(
                f"  {path}:{ln}: {line}"
                for path, hits in unknown_files.items()
                for ln, _, line in hits
            )
        )

    def test_known_files_have_no_new_artifacts(self):
        """Known-violation files must not gain new managed artifacts beyond those listed."""
        found = _scan_production()
        regressions: dict[str, set[str]] = {}
        for rel_path, allowed_artifacts in _KNOWN_VIOLATIONS.items():
            if rel_path not in found:
                continue  # file was cleaned up — great, nothing to check
            actual_artifacts = {artifact for _, artifact, _ in found[rel_path]}
            new_artifacts = actual_artifacts - allowed_artifacts
            if new_artifacts:
                regressions[rel_path] = new_artifacts
        assert not regressions, (
            "Known-violation files gained new artifact violations:\n"
            + "\n".join(
                f"  {path}: new artifacts = {arts}"
                for path, arts in regressions.items()
            )
        )

    def test_known_violations_still_present(self):
        """Sanity-check: known violations must still exist so this table stays accurate.

        If a known-violation file is cleaned up (removed from found), remove it from
        _KNOWN_VIOLATIONS too so the table stays truthful.
        """
        found = _scan_production()
        stale: list[str] = []
        for rel_path in _KNOWN_VIOLATIONS:
            if rel_path not in found:
                stale.append(rel_path)
        if stale:
            # Emit a soft warning via assertion message rather than hard failure —
            # a cleaned-up violation is a good thing, just needs the table updated.
            import warnings
            warnings.warn(
                f"These files are in _KNOWN_VIOLATIONS but now have no violations "
                f"(remove them from _KNOWN_VIOLATIONS): {stale}",
                stacklevel=2,
            )
