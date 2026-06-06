# Memory Isolation P1 Freeze Checkpoint

> **Audience**: next-window coding agent or reviewer picking up multi-character memory isolation work.
> **Status date**: 2026-06-06
> **Keyword for search**: `P1 freeze checkpoint`
> **P1-FINAL**: All P1 sub-phases (P0 through P1-3C) are complete. P1 is frozen.

---

## 1. Current State Overview

All P0–P1 isolation work is **complete and passing** (≈2096 collected tests, all green at time of P1-FINAL freeze).

| Phase | Scope | Status |
|-------|-------|--------|
| **P0** | Pipeline + slow_queue char_id透传; mood/impression/dream/hidden_state/afterglow isolation | ✅ Done |
| **P1-0** | Small bypass patches (tool reply reader, probe reader, short_term, post_process, episodic_sweep, admin/users, garden, hidden_state_decay, runtime yexuan fallback audit, prompt_builder period) | ✅ Done |
| **P1-1** | `MemoryScope` frozen dataclass (`core/memory/scope.py`) | ✅ Done |
| **P1-2** | `path_resolver.py` + all per-store migrations (see §2) + remaining path audit + artifact/domain guard | ✅ Done |
| **T-14A** | `require_character_id` fail-loud guard wired into all 8 migrated scoped stores | ✅ Done |
| **T-14B** | `test_memory_direct_path_lint.py` — direct-path lint guard; known violations cleared (see §5) | ✅ Done |
| **P1-3A** | slow_queue scope payload: `MemoryScope` serialized via `to_payload()` / `from_payload()`; `_get_scope_from_payload` replaces raw char_id helper | ✅ Done |
| **P1-3B** | Pipeline `_current_reality_scope()` + `fetch_context`/`post_process` pipeline MemoryScope scope-first refactor | ✅ Done |
| **P1-3C** | Fix 3 event_log known violations (`chat_log`, `loop`, `last_mentioned`); lint now at zero known violations | ✅ Done |

---

## 2. Migrated Artifacts (path_resolver REALITY_USER_ARTIFACTS)

All ten of the following artifacts resolve through `resolve_path(scope, artifact)` in
`core/memory/path_resolver.py`. Each has a matching integration test under `tests/`.

| Artifact key | Store / module | Integration test |
|---|---|---|
| `history` | `core/memory/short_term.py` | `test_short_term_resolver_integration.py` |
| `event_log` | `core/memory/event_log.py` | `test_event_log_resolver_integration.py` |
| `mid_term` | `core/memory/mid_term.py` | `test_mid_term_resolver_integration.py` |
| `episodic` | `core/memory/episodic_memory.py` | `test_episodic_resolver_integration.py` |
| `memory_index` | `core/memory/episodic_memory.py` | `test_episodic_resolver_integration.py` |
| `fixation_state` | `core/memory/fixation_state.py` | `test_fixation_state_resolver_integration.py` |
| `profile` | `core/memory/user_profile.py` | `test_user_profile_resolver_integration.py` |
| `identity` | `core/memory/user_identity.py` | `test_identity_resolver_integration.py` |
| `hidden_state` | `core/memory/user_hidden_state_store.py` | `test_hidden_state_store_resolver_integration.py` |
| `afterglow_residue` | `core/memory/user_hidden_state_store.py` | `test_hidden_state_store_resolver_integration.py` |

**Not migrated** (see §4):

- `character_growth` — legacy/dead registered tool; stays in `LEGACY_ARTIFACTS`.

**Additional resolver artifact sets** (not per-user, already correct before P1-2):

- `REALITY_CHARACTER_ARTIFACTS`: `mood_state`, `trait_state`, `author_note_state`, `observations`, `garden_plants`, `garden_storage`
- `GLOBAL_USER_ARTIFACTS`: `user_facts` *(path defined in resolver but store not migrated — see §4)*
- `DREAM_ARTIFACTS`: `dream_state`

---

## 3. Existing Guards

### 3.1 MemoryScope domain guard (`core/memory/scope.py`)

`MemoryScope.__post_init__` enforces:
- `global` scope: `character_id` and `world_id` must be `None`.
- `reality` scope: `character_id` must be a non-empty `str`; `world_id` must be `None`.
- `dream` scope: both `character_id` and `world_id` must be non-empty `str`.

Tests: `tests/test_memory_scope.py` (34 tests)

### 3.2 path_resolver artifact/domain allowlist (`core/memory/path_resolver.py`)

`resolve_path()` raises `ValueError` for:
- Unknown artifact keys (not in any allowlist frozenset).
- Scope domain mismatch (e.g., passing `global` scope for a `reality` artifact).

Tests: `tests/test_memory_path_resolver_guard.py` (37 tests)

### 3.3 `require_character_id` fail-loud guard (`core/memory/scope.py`)

Raises `ValueError` immediately if `char_id` is `None`, `""`, or non-`str`.
Wired into all 8 migrated scoped-store path helpers.

Tests: `tests/test_scoped_store_char_id_guard.py` (58 tests)

### 3.4 Direct-path lint guard

`tests/test_memory_direct_path_lint.py` scans source for calls to
`user_memory_root(` or `_p("` **without** a `char_id=` keyword argument.
As of P1-3C, known violations cleared — all three previously-pinned call sites
(`chat_log`, `loop`, `last_mentioned`) were resolved. The lint test now pins
**zero** exemptions. Any new direct-path call will fail the lint test.

Tests: `tests/test_memory_direct_path_lint.py` (25 tests)

### 3.5 slow_queue scope payload (P1-3A)

`_get_scope_from_payload()` deserializes a `MemoryScope` from every slow_queue payload.
All enqueue callers now send a `scope` field. Old payloads missing `char_id` still
trigger a `WARN` log and fall back to `"yexuan"` (legacy compat — see §4.2).

Tests: `tests/test_slow_queue_scope_payload.py`

### 3.6 pipeline MemoryScope (P1-3B)

`pipeline._current_reality_scope()` constructs a `MemoryScope` from the resolved
`char_id` + `uid` at pipeline entry. `fetch_context()` and `post_process()` use this
scope object for all store reads/writes.

Tests: `tests/test_pipeline_memory_scope_integration.py`

---

## 4. Legacy / Unmigrated Items

### 4.1 `character_growth` — character_growth legacy/dead registered tool

`character_growth` is in `LEGACY_ARTIFACTS` in `path_resolver.py`. Its path still
resolves for audit/compat, but:
- It is a dead registered tool — no active production write path.
- **Do not migrate to `REALITY_USER_ARTIFACTS`.**
- **Do not add a scoped store or integration test for it.**

### 4.2 DLQ payload missing char_id — legacy compatibility

When a slow_queue payload lacks `char_id`, the handler falls back to `"yexuan"` with a
`WARN` log. This is a legacy compat shim for old serialized payloads that pre-date P1-3A.
It is intentional and must remain `WARN` (not silent). Do not remove the fallback; it
will be retired in a future cleanup once the queue is confirmed drained of pre-P1-3A payloads.

### 4.3 API default `char_id="yexuan"`

Several admin/pipeline entry points default `char_id` to `"yexuan"` when not supplied.
This is the single-character compatibility default. **Do not delete these defaults** until
a follow-up explicitly replaces them with scope payload propagation. The default is not a
runtime source of truth for the resolver.

### 4.4 `user_facts` — not yet migrated

`user_facts` path is defined in `GLOBAL_USER_ARTIFACTS` in the resolver, but the store
itself is not migrated. This is tracked as **P1-4** work. Do not migrate it in this
phase.

---

## 5. Known Violations

**As of P1-3C: known violations cleared — zero remaining.**

All three previously-pinned call sites have been resolved:

| # | File | Fix applied in |
|---|------|---------------|
| 1 | `admin/routers/chat_log.py` ~31 | P1-3C |
| 2 | `core/scheduler/loop.py` ~295 | P1-3C |
| 3 | `core/scheduler/last_mentioned.py` ~387 | P1-3C |

The lint test (`tests/test_memory_direct_path_lint.py`) now pins **zero** exemptions.
Any new direct-path call will immediately fail the lint.

---

## 6. P1-FINAL: Remaining TODO (non-blocking, enters P1+ / P2)

P1 is frozen. The items below are tracked for future phases and **do not block shipping**.

### P1-4 — user_facts global split

Migrate `user_facts` store to `GLOBAL_USER_ARTIFACTS` path via resolver. Decide whether
per-character fact isolation is needed or whether global is correct.

### P2 migration — Legacy data migration

Rename on-disk files from old uid-only paths to the new `{char_id}/{uid}/` layout for
any users who have data under the legacy tree. Provide a migration script; test with
dry-run mode.

### Optional / future

- Scheduler entry points: more thorough `MemoryScope` propagation (P1-3A/B covers the
  critical path; deeper scheduler scope threading is an optional improvement).
- API default `char_id="yexuan"` final removal — deferred to a later major version once
  all callers are confirmed to send a fully-formed scope.

---

## 7. Prohibited Actions (do not do in any follow-up PR)

- **Do not migrate `character_growth`** — it is a legacy/dead tool.
- **Do not delete `char_id` API defaults** before scope payload propagation is in place.
- **Do not migrate existing on-disk data** outside of a dedicated P2 migration script.
- **Do not alter the Dream session structure** — dream scope is frozen; char_id + world_id
  are already enforced by `MemoryScope`.
- **Do not migrate `user_facts`** before P1-4 design is agreed.
- **Do not silently swallow the DLQ `"yexuan"` fallback** — it must remain a `WARN` log.
- **Do not read `config.default` as runtime source of truth** for character identity.
- **Do not re-read `active` char during Dream close/summary/impression/afterglow** — scope
  is fixed at Dream open.
- **Do not copy yexuan memories to hongcha or other characters** — isolation is
  per-character; backfill requires an explicit P2 migration script.

---

## 8. Recommended Regression Commands

Run all suites before and after any isolation-related change:

```bash
# MemoryScope + path_resolver + guards
pytest tests/test_memory_scope.py tests/test_memory_path_resolver.py \
       tests/test_memory_path_resolver_guard.py tests/test_scoped_store_char_id_guard.py \
       tests/test_memory_direct_path_lint.py -v

# All migrated store integration tests
pytest tests/test_hidden_state_store_resolver_integration.py \
       tests/test_user_profile_resolver_integration.py \
       tests/test_identity_resolver_integration.py \
       tests/test_mid_term_resolver_integration.py \
       tests/test_episodic_resolver_integration.py \
       tests/test_short_term_resolver_integration.py \
       tests/test_event_log_resolver_integration.py \
       tests/test_fixation_state_resolver_integration.py \
       tests/test_memory_resolver_remaining_paths_audit.py -v

# P1-3A/B slow_queue scope payload + pipeline MemoryScope
pytest tests/test_slow_queue_scope_payload.py \
       tests/test_pipeline_memory_scope_integration.py -v

# Memory isolation final gate (P0 + P1-0 scope tests)
pytest tests/test_memory_isolation_p0_final.py \
       tests/test_memory_isolation_no_runtime_yexuan_fallback.py \
       tests/test_pipeline_read_scope.py tests/test_pipeline_write_scope.py \
       tests/test_slow_queue_char_scope.py -v

# Direct path lint
pytest tests/test_memory_direct_path_lint.py -v
```

Full suite: `pytest` (approximately 2096 tests as of P1-FINAL).

---

## 9. Key Files Reference

| File | Purpose |
|------|---------|
| `core/memory/scope.py` | `MemoryScope` dataclass + `require_character_id` |
| `core/memory/path_resolver.py` | Artifact allowlists + `resolve_path()` |
| `tests/test_memory_scope.py` | 34 MemoryScope tests |
| `tests/test_memory_path_resolver.py` | path_resolver basic tests |
| `tests/test_memory_path_resolver_guard.py` | 37 allowlist/domain guard tests |
| `tests/test_scoped_store_char_id_guard.py` | 58 char_id fail-loud tests (T-14A) |
| `tests/test_memory_direct_path_lint.py` | 25 direct-path lint tests (T-14B) |
| `tests/test_slow_queue_scope_payload.py` | P1-3A scope payload tests |
| `tests/test_pipeline_memory_scope_integration.py` | P1-3B pipeline MemoryScope tests |
| `tests/test_memory_isolation_p0_final.py` | P0 final gate |
| `docs/memory.md` | General memory architecture |
| `docs/memory-isolation-p1-checkpoint.md` | **This file** |
