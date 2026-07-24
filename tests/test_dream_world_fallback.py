"""
tests/test_dream_world_fallback.py — _default fallback tests for world_loader.

Covers:
  ① world missing ruleset.md → fallback _default/ruleset.md
  ② world missing mes_example.md → fallback _default/mes_example.md
  ③ world has own content → no fallback (own content used)
  ④ lorebook missing → no error, empty list returned, no default lore injected
  ⑤ vocab missing → fallback _default/vocab.json
"""

from pathlib import Path
from unittest.mock import patch

import pytest


def _build_worlds(tmp_path: Path) -> Path:
    """Return a tmp worlds base with a _default package already populated."""
    worlds = tmp_path / "characters" / "dream_worlds"
    default_dir = worlds / "_default"
    default_dir.mkdir(parents=True)
    (default_dir / "ruleset.md").write_text("DEFAULT RULESET", encoding="utf-8")
    (default_dir / "mes_example.md").write_text("DEFAULT MES", encoding="utf-8")
    (default_dir / "vocab.json").write_text('["default_term"]', encoding="utf-8")
    (default_dir / "lorebook.yaml").write_text("[]", encoding="utf-8")
    return worlds


def _make_world(worlds: Path, world_id: str, **files: str) -> Path:
    """Create world directory with only the given files written."""
    d = worlds / world_id
    d.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (d / name).write_text(content, encoding="utf-8")
    return d


# ─────────────────────────────────────────────────────────────────────────────
# ① ruleset fallback
# ─────────────────────────────────────────────────────────────────────────────

def test_fallback_ruleset_when_missing(tmp_path):
    """world missing ruleset.md → _default/ruleset.md used."""
    worlds = _build_worlds(tmp_path)
    _make_world(worlds, "reality_derived",
                **{"mes_example.md": "WORLD MES", "vocab.json": "[]"})

    import core.dream.world_loader as wl
    with patch("core.dream.world_loader._worlds_base", return_value=worlds):
        pkg = wl.load_world("reality_derived")

    assert pkg.ruleset == "DEFAULT RULESET"
    assert pkg.mes_example == "WORLD MES"


def test_fallback_ruleset_when_empty(tmp_path):
    """world ruleset.md exists but is blank after strip → _default/ruleset.md used."""
    worlds = _build_worlds(tmp_path)
    _make_world(worlds, "reality_derived",
                **{"ruleset.md": "   \n  ", "mes_example.md": "WORLD MES", "vocab.json": "[]"})

    import core.dream.world_loader as wl
    with patch("core.dream.world_loader._worlds_base", return_value=worlds):
        pkg = wl.load_world("reality_derived")

    assert pkg.ruleset == "DEFAULT RULESET"


# ─────────────────────────────────────────────────────────────────────────────
# ② mes_example fallback
# ─────────────────────────────────────────────────────────────────────────────

def test_fallback_mes_example_when_missing(tmp_path):
    """world missing mes_example.md → _default/mes_example.md used."""
    worlds = _build_worlds(tmp_path)
    _make_world(worlds, "reality_derived",
                **{"ruleset.md": "WORLD RULESET", "vocab.json": "[]"})

    import core.dream.world_loader as wl
    with patch("core.dream.world_loader._worlds_base", return_value=worlds):
        pkg = wl.load_world("reality_derived")

    assert pkg.mes_example == "DEFAULT MES"
    assert pkg.ruleset == "WORLD RULESET"


# ─────────────────────────────────────────────────────────────────────────────
# ③ no fallback when world has own content
# ─────────────────────────────────────────────────────────────────────────────

def test_no_fallback_when_world_has_content(tmp_path):
    """world with full content → own content used, _default not touched."""
    worlds = _build_worlds(tmp_path)
    _make_world(worlds, "reality_derived",
                **{"ruleset.md": "OWN RULESET",
                   "mes_example.md": "OWN MES",
                   "vocab.json": '["own_term"]'})

    import core.dream.world_loader as wl
    with patch("core.dream.world_loader._worlds_base", return_value=worlds):
        pkg = wl.load_world("reality_derived")

    assert pkg.ruleset == "OWN RULESET"
    assert pkg.mes_example == "OWN MES"
    assert pkg.vocab_terms == ["own_term"]


# ─────────────────────────────────────────────────────────────────────────────
# ④ lorebook missing → no error, no default lore injected
# ─────────────────────────────────────────────────────────────────────────────

def test_lorebook_missing_no_error_empty_list(tmp_path):
    """lorebook.yaml missing → returns [], no exception, no default lore injected."""
    worlds = _build_worlds(tmp_path)
    world_dir = worlds / "reality_derived"
    world_dir.mkdir(parents=True, exist_ok=True)
    # intentionally no lorebook.yaml

    import core.dream.world_loader as wl
    with patch("core.dream.world_loader._worlds_base", return_value=worlds):
        entries = wl.load_dream_lore_entries("reality_derived")

    assert entries == []


def test_lorebook_missing_does_not_inject_default_lore(tmp_path):
    """Even with _default/lorebook.yaml present, missing world lorebook → empty (no contamination)."""
    worlds = _build_worlds(tmp_path)
    # Put real content in default lorebook
    (worlds / "_default" / "lorebook.yaml").write_text(
        '[{"keywords": ["anything"], "content": "DEFAULT LORE", "insertion_order": 1}]',
        encoding="utf-8",
    )
    world_dir = worlds / "reality_derived"
    world_dir.mkdir(parents=True, exist_ok=True)
    # no lorebook.yaml for this world

    import core.dream.world_loader as wl
    with patch("core.dream.world_loader._worlds_base", return_value=worlds):
        entries = wl.load_dream_lore_entries("reality_derived")

    assert entries == []
    contents = [e.get("content", "") for e in entries]
    assert "DEFAULT LORE" not in contents


# ─────────────────────────────────────────────────────────────────────────────
# ⑤ vocab fallback
# ─────────────────────────────────────────────────────────────────────────────

def test_fallback_vocab_when_missing(tmp_path):
    """vocab.json missing → _default/vocab.json used."""
    worlds = _build_worlds(tmp_path)
    _make_world(worlds, "reality_derived",
                **{"ruleset.md": "WORLD RULESET", "mes_example.md": "WORLD MES"})
    # no vocab.json

    import core.dream.world_loader as wl
    with patch("core.dream.world_loader._worlds_base", return_value=worlds):
        pkg = wl.load_world("reality_derived")

    assert pkg.vocab_terms == ["default_term"]


def test_fallback_vocab_when_invalid_json(tmp_path):
    """vocab.json contains invalid JSON → _default/vocab.json used."""
    worlds = _build_worlds(tmp_path)
    _make_world(worlds, "reality_derived",
                **{"ruleset.md": "WORLD RULESET",
                   "mes_example.md": "WORLD MES",
                   "vocab.json": "not-valid-json{"})

    import core.dream.world_loader as wl
    with patch("core.dream.world_loader._worlds_base", return_value=worlds):
        pkg = wl.load_world("reality_derived")

    assert pkg.vocab_terms == ["default_term"]


def test_no_fallback_vocab_when_intentionally_empty(tmp_path):
    """vocab.json with [] → treated as intentionally empty, no fallback."""
    worlds = _build_worlds(tmp_path)
    _make_world(worlds, "reality_derived",
                **{"ruleset.md": "WORLD RULESET",
                   "mes_example.md": "WORLD MES",
                   "vocab.json": "[]"})

    import core.dream.world_loader as wl
    with patch("core.dream.world_loader._worlds_base", return_value=worlds):
        pkg = wl.load_world("reality_derived")

    assert pkg.vocab_terms == []
