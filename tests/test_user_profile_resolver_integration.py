"""
tests/test_user_profile_resolver_integration.py — P1-2C

Verifies that user_profile now routes ALL path computation through
MemoryScope + resolve_path, not get_paths() directly.

Covers:
1.  load() reads from resolve_path(reality_scope, "profile")
2.  save() writes to resolve_path(reality_scope, "profile")
3.  clear() resets the resolver path file
4.  get_period_info() reads from resolve_path(reality_scope, "profile")
5.  Physical path identical to legacy user_memory_root / profile.json (P0 parity)
6.  char_id=None → ValueError (fail-loud, no fallback yexuan)
7.  char_id="" → ValueError (fail-loud, no fallback yexuan)
8.  yexuan / hongcha profile buckets are isolated
9.  period info reads hongcha bucket, not yexuan bucket
"""
from __future__ import annotations

import json

import pytest

# Pre-import at collection time so _CHAR = _char_name() runs before any chdir.
import core.memory.user_profile as _up_preimport  # noqa: F401

from core.memory.scope import MemoryScope
from core.memory.path_resolver import resolve_path

_UID = "p1_2c_integ_u1"


# ---------------------------------------------------------------------------
# 1. load() reads from resolve_path("profile")
# ---------------------------------------------------------------------------

def test_load_profile_reads_from_resolver_path(sandbox):
    import core.memory.user_profile as _up

    scope = MemoryScope.reality_scope(_UID, "hongcha")
    expected_path = resolve_path(scope, "profile")
    expected_path.parent.mkdir(parents=True, exist_ok=True)
    expected_path.write_text(
        json.dumps({"name": "红茶-sentinel", "occupation": "数媒艺设"}),
        encoding="utf-8",
    )

    result = _up.load(_UID, char_id="hongcha")
    assert result["name"] == "红茶-sentinel"
    assert result["occupation"] == "数媒艺设"


# ---------------------------------------------------------------------------
# 2. save() writes to resolve_path("profile")
# ---------------------------------------------------------------------------

def test_save_profile_writes_to_resolver_path(sandbox):
    import core.memory.user_profile as _up

    scope = MemoryScope.reality_scope(_UID, "hongcha")
    expected_path = resolve_path(scope, "profile")

    _up.save(_UID, {"name": "save-test", "location": "杭州"}, char_id="hongcha")

    assert expected_path.exists(), "save() must write to resolver path"
    data = json.loads(expected_path.read_text(encoding="utf-8"))
    assert data["name"] == "save-test"
    assert data["location"] == "杭州"


# ---------------------------------------------------------------------------
# 3. clear() resets the resolver path file
# ---------------------------------------------------------------------------

def test_clear_profile_resets_resolver_path(sandbox):
    import core.memory.user_profile as _up

    _up.save(_UID, {"name": "before-clear", "location": "诸暨"}, char_id="hongcha")

    scope = MemoryScope.reality_scope(_UID, "hongcha")
    expected_path = resolve_path(scope, "profile")
    assert expected_path.exists()

    _up.clear(_UID, char_id="hongcha")

    reloaded = _up.load(_UID, char_id="hongcha")
    assert reloaded["name"] is None
    assert reloaded["location"] is None


# ---------------------------------------------------------------------------
# 4. get_period_info() reads from resolve_path("profile")
# ---------------------------------------------------------------------------

def test_get_period_info_reads_from_resolver_path(sandbox):
    import core.memory.user_profile as _up

    scope = MemoryScope.reality_scope(_UID, "hongcha")
    expected_path = resolve_path(scope, "profile")
    expected_path.parent.mkdir(parents=True, exist_ok=True)
    expected_path.write_text(
        json.dumps({"last_period_date": "2026-06-01"}),
        encoding="utf-8",
    )

    info = _up.get_period_info(_UID, char_id="hongcha")
    assert info["last_period_date"] == "2026-06-01"


# ---------------------------------------------------------------------------
# 5. Physical path identity: resolver == sandbox.user_memory_root / profile.json
# ---------------------------------------------------------------------------

def test_profile_path_equals_legacy_sandbox_path(sandbox):
    scope = MemoryScope.reality_scope(_UID, "hongcha")
    resolver_path = resolve_path(scope, "profile")
    legacy_path = sandbox.user_memory_root(_UID, char_id="hongcha") / "profile.json"
    assert resolver_path == legacy_path, (
        f"Resolver path diverged from legacy:\n  resolver: {resolver_path}\n  legacy:   {legacy_path}"
    )


def test_profile_path_equals_legacy_sandbox_path_yexuan(sandbox):
    scope = MemoryScope.reality_scope(_UID, "yexuan")
    resolver_path = resolve_path(scope, "profile")
    legacy_path = sandbox.user_memory_root(_UID, char_id="yexuan") / "profile.json"
    assert resolver_path == legacy_path, (
        f"Resolver path diverged from legacy:\n  resolver: {resolver_path}\n  legacy:   {legacy_path}"
    )


# ---------------------------------------------------------------------------
# 6. char_id=None → fail-loud, no yexuan fallback
# ---------------------------------------------------------------------------

def test_load_profile_char_id_none_raises(sandbox):
    import core.memory.user_profile as _up
    with pytest.raises((ValueError, TypeError)):
        _up.load(_UID, char_id=None)  # type: ignore[arg-type]


def test_save_profile_char_id_none_raises(sandbox):
    import core.memory.user_profile as _up
    with pytest.raises((ValueError, TypeError)):
        _up.save(_UID, {"name": "x"}, char_id=None)  # type: ignore[arg-type]


def test_clear_profile_char_id_none_raises(sandbox):
    import core.memory.user_profile as _up
    with pytest.raises((ValueError, TypeError)):
        _up.clear(_UID, char_id=None)  # type: ignore[arg-type]


def test_get_period_info_char_id_none_raises(sandbox):
    import core.memory.user_profile as _up
    with pytest.raises((ValueError, TypeError)):
        _up.get_period_info(_UID, char_id=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 7. char_id="" → fail-loud, no yexuan fallback
# ---------------------------------------------------------------------------

def test_load_profile_empty_char_id_raises(sandbox):
    import core.memory.user_profile as _up
    with pytest.raises(ValueError):
        _up.load(_UID, char_id="")


def test_save_profile_empty_char_id_raises(sandbox):
    import core.memory.user_profile as _up
    with pytest.raises(ValueError):
        _up.save(_UID, {"name": "x"}, char_id="")


def test_clear_profile_empty_char_id_raises(sandbox):
    import core.memory.user_profile as _up
    with pytest.raises(ValueError):
        _up.clear(_UID, char_id="")


# ---------------------------------------------------------------------------
# 8. yexuan / hongcha profile buckets are isolated
# ---------------------------------------------------------------------------

def test_yexuan_hongcha_profile_isolated(sandbox):
    import core.memory.user_profile as _up

    _up.save(_UID, {"name": "叶瑄专属"}, char_id="yexuan")
    _up.save(_UID, {"name": "红茶专属"}, char_id="hongcha")

    y = _up.load(_UID, char_id="yexuan")
    h = _up.load(_UID, char_id="hongcha")

    assert y["name"] == "叶瑄专属"
    assert h["name"] == "红茶专属"

    y_path = sandbox.user_memory_root(_UID, char_id="yexuan") / "profile.json"
    h_path = sandbox.user_memory_root(_UID, char_id="hongcha") / "profile.json"
    assert y_path.exists()
    assert h_path.exists()
    assert y_path != h_path


def test_clear_hongcha_does_not_affect_yexuan(sandbox):
    import core.memory.user_profile as _up

    _up.save(_UID, {"name": "叶瑄不动"}, char_id="yexuan")
    _up.save(_UID, {"name": "红茶清除"}, char_id="hongcha")

    _up.clear(_UID, char_id="hongcha")

    y = _up.load(_UID, char_id="yexuan")
    assert y["name"] == "叶瑄不动", "yexuan bucket must survive hongcha clear"


# ---------------------------------------------------------------------------
# 9. period info reads hongcha bucket, not yexuan bucket
# ---------------------------------------------------------------------------

def test_period_info_isolation(sandbox):
    import core.memory.user_profile as _up

    _up.save(_UID, {"last_period_date": "2026-01-01"}, char_id="yexuan")
    _up.save(_UID, {"last_period_date": "2026-06-01"}, char_id="hongcha")

    y_info = _up.get_period_info(_UID, char_id="yexuan")
    h_info = _up.get_period_info(_UID, char_id="hongcha")

    assert y_info["last_period_date"] == "2026-01-01"
    assert h_info["last_period_date"] == "2026-06-01"
    assert y_info["last_period_date"] != h_info["last_period_date"], (
        "period info must be isolated between yexuan and hongcha buckets"
    )
