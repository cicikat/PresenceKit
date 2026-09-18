"""
tests/test_no_hardcoded_character.py — Brief 25 §3 P3 守门测试

防回流：确保 §3 P0-P2 清理过的硬编码角色名/用户名/协议字段不再悄悄长回来。

三条规则：
  Rule A — 源码里不得出现字面 "叶瑄" 或 "风谕"，除非文件在 BARE_NAME_ALLOWLIST
           （已验证：这些位置要么是 char_name.replace() 补丁覆盖的模板常量，
           要么是纯文档性示例，均不会作为字面量到达用户/LLM）。
  Rule B — 协议兼容别名 "yexuan_ai" / "yexuan_tension" 的**带引号字符串字面量**
           （即作为 dict key / JSON 字段，而不是 Python 标识符）只允许出现在
           各自的白名单文件里（P2 归一化/双发代码及其直接协作模块）。
  Rule C — 白名单条目必须仍然存在且仍然命中，否则说明已经清理干净，白名单该删了
           （防止白名单本身腐烂成摆设）。

char_id=TEST_CHAR_ID 默认参数的守门已有独立测试：tests/test_r3_scope_lint.py。
"""
from __future__ import annotations
from tests.fixtures.public_assets import TEST_CHAR_ID

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
_SCAN_ROOTS = ("core", "admin", "channels")
_SCAN_FILES = ("main.py",)


def _iter_source_py():
    for root_name in _SCAN_ROOTS:
        root = PROJECT_ROOT / root_name
        if not root.exists():
            continue
        for p in root.rglob("*.py"):
            if any(seg in ("test", "tests", "__pycache__") for seg in p.parts):
                continue
            yield p
    for name in _SCAN_FILES:
        p = PROJECT_ROOT / name
        if p.exists():
            yield p


def _rel(p: Path) -> str:
    return p.relative_to(PROJECT_ROOT).as_posix()


# ---------------------------------------------------------------------------
# Rule A — bare "叶瑄" / "风谕"
# ---------------------------------------------------------------------------

# Each entry verified at migration time (Brief 25 §3 P0):
BARE_NAME_ALLOWLIST: frozenset[str] = frozenset({
    # char_name.replace("叶瑄", char_name) precedent — fully covers the literal
    # before it ever reaches the LLM (existing de-hardcode precedent §1).
    "core/activity/chess_companion.py",
    "core/activity/gomoku_companion.py",
    "core/activity/reading_companion.py",
    "core/dream/distill_impression.py",

    # Pure documentation examples for genuinely generic functions (label lookup,
    # docstring parameter samples) — the underlying code takes no character name
    # as a hardcoded default; the literal is illustrative only.
    "core/asset_registry.py",
    "core/character_loader.py",
    "core/memory/character_growth.py",
    "admin/routers/character.py",
    "admin/routers/settings_prompt_assets.py",
})

_BARE_NAME_RE = re.compile("叶瑄|风谕")


def _find_bare_name_hits(source: str) -> list[int]:
    return [i for i, line in enumerate(source.splitlines(), 1) if _BARE_NAME_RE.search(line)]


def test_no_bare_character_or_user_name_outside_allowlist():
    violations: dict[str, list[int]] = {}
    for path in _iter_source_py():
        rel = _rel(path)
        if rel in BARE_NAME_ALLOWLIST:
            continue
        hits = _find_bare_name_hits(path.read_text(encoding="utf-8"))
        if hits:
            violations[rel] = hits

    assert not violations, (
        "Found literal '叶瑄'/'风谕' outside BARE_NAME_ALLOWLIST.\n"
        "Prompt/user-facing text must interpolate char_name/user_name instead "
        "(see Brief 25 §3 P0). If this is a verified-safe illustrative example, "
        "add the file to BARE_NAME_ALLOWLIST with a reason.\n"
        f"Violations: {violations}"
    )


def test_bare_name_allowlist_entries_still_hit():
    """Allowlist entries must still contain the literal — else remove them (Rule C)."""
    already_clean: list[str] = []
    for rel in sorted(BARE_NAME_ALLOWLIST):
        path = PROJECT_ROOT / rel
        if not path.exists():
            continue
        if not _find_bare_name_hits(path.read_text(encoding="utf-8")):
            already_clean.append(rel)

    assert not already_clean, (
        "These BARE_NAME_ALLOWLIST files no longer contain '叶瑄'/'风谕' — "
        "remove them from the allowlist in tests/test_no_hardcoded_character.py:\n"
        + "\n".join(f"  {f}" for f in already_clean)
    )


# ---------------------------------------------------------------------------
# Rule B — protocol compat literals: "yexuan_ai" / "yexuan_tension"
# ---------------------------------------------------------------------------

# Quoted-string form only (dict key / JSON field) — deliberately does NOT match
# bare Python identifiers like `yexuan_tension` used as internal variable/param
# names in core/dream/{body_projection,dream_pipeline,dream_prompt}.py; renaming
# those is out of scope for Brief 25 §3 P2 (see plan: protocol rename covers the
# API response surface only, not internal plumbing between dream_pipeline and
# body_projection).
YEXUAN_AI_ALLOWLIST: frozenset[str] = frozenset({
    # opponent enum back-compat: accepts legacy "yexuan_ai" input, normalizes to
    # "character_ai". Expiry: once no client sends the legacy value (client Brief
    # 15 §G tracks this), delete _LEGACY_OPPONENT_ALIASES and this allowlist entry.
    "core/activity/chess.py",
    "core/activity/gomoku.py",
})

YEXUAN_TENSION_ALLOWLIST: frozenset[str] = frozenset({
    # Internal dream_pipeline<->body_projection plumbing dict key — not a protocol
    # field, but the literal is quoted so it matches the same regex; whitelisted
    # rather than renamed (see plan: protocol rename covers GET /dream/state only).
    "core/dream/body_projection.py",
    "core/dream/dream_pipeline.py",
    # Brief 100: Dream Stage's post-round tension coupling reads the same
    # project_body_for_yexuan() plumbing dict key, one call per replying
    # character instead of once per solo turn — same internal, non-protocol key.
    "core/stage/dream_runtime.py",
})

_YEXUAN_AI_RE = re.compile(r'"yexuan_ai"|\'yexuan_ai\'')
_YEXUAN_TENSION_RE = re.compile(r'"yexuan_tension"|\'yexuan_tension\'')


def _find_matches(source: str, pattern: re.Pattern) -> list[int]:
    return [i for i, line in enumerate(source.splitlines(), 1) if pattern.search(line)]


def test_no_quoted_yexuan_ai_outside_allowlist():
    violations: dict[str, list[int]] = {}
    for path in _iter_source_py():
        rel = _rel(path)
        if rel in YEXUAN_AI_ALLOWLIST:
            continue
        hits = _find_matches(path.read_text(encoding="utf-8"), _YEXUAN_AI_RE)
        if hits:
            violations[rel] = hits

    assert not violations, (
        "Found quoted 'yexuan_ai' string literal outside YEXUAN_AI_ALLOWLIST.\n"
        "The gomoku/chess opponent enum canonical value is 'character_ai' "
        "(Brief 25 §3 P2); legacy input must go through _normalize_opponent().\n"
        f"Violations: {violations}"
    )


def test_no_quoted_yexuan_tension_outside_allowlist():
    violations: dict[str, list[int]] = {}
    for path in _iter_source_py():
        rel = _rel(path)
        if rel in YEXUAN_TENSION_ALLOWLIST:
            continue
        hits = _find_matches(path.read_text(encoding="utf-8"), _YEXUAN_TENSION_RE)
        if hits:
            violations[rel] = hits

    assert not violations, (
        "Found quoted 'yexuan_tension' string literal outside YEXUAN_TENSION_ALLOWLIST.\n"
        "The dream-state protocol field is 'char_tension' (Brief 25 §3 P2); "
        "'yexuan_tension' is an internal plumbing key only, not a GET /dream/state alias.\n"
        f"Violations: {violations}"
    )


# ---------------------------------------------------------------------------
# Detector sanity checks
# ---------------------------------------------------------------------------

def test_detector_catches_bare_name_in_string():
    assert _find_bare_name_hits('SYSTEM = "你是叶瑄"\n') == [1]
    assert _find_bare_name_hits('note = "用户叫风谕"\n') == [1]


def test_detector_ignores_unrelated_text():
    assert _find_bare_name_hits('x = "hello world"\n') == []


def test_detector_catches_quoted_yexuan_ai_not_bare_identifier():
    assert _find_matches('opponent = "yexuan_ai"\n', _YEXUAN_AI_RE) == [1]
    assert _find_matches("yexuan_ai_flag = True\n", _YEXUAN_AI_RE) == []
