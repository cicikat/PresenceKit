"""
tests/test_dream_integration_fallback.py — Integration: _default fallback → build_dream_prompt

验证 _default fallback 不只是 load_world() 单测通过，而是真实进入 D2/D3 prompt layers。

所有 fallback 测试均经由 build_dream_prompt() 端到端组装，不绕过任何中间层。

测试覆盖：
  ① 全缺失 → D2/D3 均来自 _default（ruleset + mes_example）
  ② 逐字段 fallback：只有 mes_example.md 有内容 →
       D2 用 _default ruleset，D3 用 world 自己的 mes（不被 default 覆盖）
  ③ lorebook 缺失全链路：load_dream_lore_entries → match_dream_lore → build_dream_prompt
       → prompt 中无 '# 梦境世界书'，default lore 不注入
  ④ 现有 6 个 world 不触发 fallback：ruleset/mes_example 均非空，无 fallback 日志
"""

import logging
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Project root & real _default location ─────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent
_REAL_WORLDS_BASE = _PROJECT_ROOT / "characters" / "dream_worlds"

# ── Key phrases from _default files (see characters/dream_worlds/_default/) ──
# Changes to _default content must be reflected here.
_DEFAULT_RULESET_PHRASE = "梦境是现实的变体"
_DEFAULT_MES_TAG = "灰白色的光"   # opening line of _default/mes_example.md
_DEFAULT_MES_PHRASE = "你来了"

# ── Minimal fakes ─────────────────────────────────────────────────────────────
_FAKE_CHAR = MagicMock()
_FAKE_CHAR.name = "叶瑄"
_FAKE_CHAR.description = "叶瑄是圣塞西尔学院的老师"


def _call(world_id: str, lore_entries=None):
    """Call build_dream_prompt with minimal valid inputs."""
    from core.dream.dream_prompt import build_dream_prompt
    return build_dream_prompt(
        character=_FAKE_CHAR,
        user_id="integ_test_user",
        user_message="测试消息",
        context_snapshot={},
        dream_history=[],
        local_state={},
        lore_entries=lore_entries or [],
        world_id=world_id,
    )


def _system(msgs: list) -> str:
    return msgs[0]["content"]


def _extract_section(system: str, marker: str) -> str:
    """Return text from marker until the next '# D' section or end of string."""
    start = system.find(marker)
    if start == -1:
        return ""
    rest = system[start + len(marker):]
    import re
    nxt = re.search(r"\n# D", rest)
    return rest[: nxt.start()] if nxt else rest


# ── Fixture: temp worlds base with real _default copied in ────────────────────

@pytest.fixture
def tmp_worlds(tmp_path):
    """
    Temp worlds directory with _default/ copied from the real project location.
    Tests that patch _WORLDS_BASE to this dir use the actual _default content.
    """
    worlds = tmp_path / "worlds"
    worlds.mkdir()
    shutil.copytree(_REAL_WORLDS_BASE / "_default", worlds / "_default")
    return worlds


# ═══════════════════════════════════════════════════════════════════════════════
# ① 全缺失 → full fallback → D2/D3 均来自 _default
# ═══════════════════════════════════════════════════════════════════════════════

def test_full_fallback_reaches_d2_and_d3(tmp_worlds):
    """
    World ruleset.md + mes_example.md 均缺失 →
    build_dream_prompt D2 包含 _default/ruleset.md 关键句，
    D3 包含 _default/mes_example.md 的 <env> 标签和关键对话。
    走真实 build_dream_prompt，不绕过组装层。
    """
    # Create world dir with NO files
    (tmp_worlds / "reality_derived").mkdir()

    with patch("core.dream.world_loader._WORLDS_BASE", tmp_worlds):
        msgs = _call("reality_derived")

    sys = _system(msgs)
    d2 = _extract_section(sys, "# D2·今晚梦的世界规则")
    d3 = _extract_section(sys, "# D3·梦境示例对话")

    assert _DEFAULT_RULESET_PHRASE in d2, (
        f"D2 应包含 _default/ruleset.md 关键句 '{_DEFAULT_RULESET_PHRASE}'\n"
        f"实际 D2: {d2[:400]}"
    )
    assert _DEFAULT_MES_TAG in d3, (
        f"D3 应包含 _default/mes_example.md <env> 标签\n"
        f"实际 D3: {d3[:400]}"
    )
    assert _DEFAULT_MES_PHRASE in d3, (
        f"D3 应包含 _default/mes_example.md 关键对话 '{_DEFAULT_MES_PHRASE}'\n"
        f"实际 D3: {d3[:400]}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ② 逐字段 fallback：只有 mes_example 有内容，ruleset 缺失
# ═══════════════════════════════════════════════════════════════════════════════

def test_per_field_fallback_ruleset_default_mes_own(tmp_worlds):
    """
    World 只有 mes_example.md（自定义内容），ruleset.md 缺失 →
    D2 用 _default ruleset（逐字段 fallback），
    D3 用 world 自定义 mes（不被 _default 覆盖）。
    证明是字段级 fallback，不是整个 world 被 _default 替代。
    """
    world_dir = tmp_worlds / "abo"
    world_dir.mkdir()
    custom_mes = "自定义梦境示例：叶瑄轻声说，这是只属于我们的空间。"
    (world_dir / "mes_example.md").write_text(custom_mes, encoding="utf-8")
    (world_dir / "vocab.json").write_text("[]", encoding="utf-8")
    # intentionally NO ruleset.md

    with patch("core.dream.world_loader._WORLDS_BASE", tmp_worlds):
        msgs = _call("abo")

    sys = _system(msgs)
    d2 = _extract_section(sys, "# D2·今晚梦的世界规则")
    d3 = _extract_section(sys, "# D3·梦境示例对话")

    # D2 must use _default (ruleset missing in world)
    assert _DEFAULT_RULESET_PHRASE in d2, (
        f"D2 应 fallback 到 _default/ruleset.md，含 '{_DEFAULT_RULESET_PHRASE}'\n"
        f"实际 D2: {d2[:400]}"
    )

    # D3 must use world's own mes_example (not _default)
    assert custom_mes in d3, (
        f"D3 应使用 world 自定义 mes_example，含 '{custom_mes}'\n"
        f"实际 D3: {d3[:400]}"
    )
    assert _DEFAULT_MES_TAG not in d3, (
        f"D3 不应出现 _default mes 标签 '{_DEFAULT_MES_TAG}'（world 有自己的 mes）\n"
        f"实际 D3: {d3[:400]}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ③ lorebook 全链路：缺失 → 空 → build_dream_prompt 不注入
# ═══════════════════════════════════════════════════════════════════════════════

def test_lorebook_missing_full_pipeline_no_injection(tmp_worlds):
    """
    lorebook.yaml 缺失全链路验证：
      load_dream_lore_entries() → []
      match_dream_lore([]) → []
      build_dream_prompt(lore_entries=[]) → prompt 无 '# 梦境世界书'
    _default/lorebook.yaml（空列表）也不注入任何 lore。
    """
    world_dir = tmp_worlds / "reality_derived"
    world_dir.mkdir()
    (world_dir / "ruleset.md").write_text("WORLD RULESET 世界规则", encoding="utf-8")
    (world_dir / "mes_example.md").write_text("WORLD MES 示例对话", encoding="utf-8")
    # NO lorebook.yaml

    import core.dream.world_loader as wl
    from core.dream.dream_prompt import build_dream_prompt

    with patch("core.dream.world_loader._WORLDS_BASE", tmp_worlds):
        raw_entries = wl.load_dream_lore_entries("reality_derived")
        matched_lore = wl.match_dream_lore(raw_entries, "测试消息")
        msgs = build_dream_prompt(
            character=_FAKE_CHAR,
            user_id="integ_test_user",
            user_message="测试消息",
            context_snapshot={},
            dream_history=[],
            local_state={},
            lore_entries=matched_lore,
            world_id="reality_derived",
        )

    sys = _system(msgs)

    assert raw_entries == [], "load_dream_lore_entries 对缺失 lorebook 应返回 []"
    assert matched_lore == [], "match_dream_lore 对空 entries 应返回 []"
    assert "# 梦境世界书" not in sys, (
        "prompt 中不应出现 '# 梦境世界书'（lore_entries 为空）"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ④ 现有 6 个 world：ruleset/mes_example 均非空，不触发 _default fallback
# ═══════════════════════════════════════════════════════════════════════════════

_ALL_WORLDS = ["reality_derived", "abo", "vampire", "cat", "flower_bud", "custom"]


@pytest.mark.parametrize("world_id", _ALL_WORLDS)
def test_existing_worlds_no_fallback(world_id, caplog):
    """
    现有 6 个 world 文件完整 →
    - load_world 后 ruleset 和 mes_example 均非空
    - 无 'source=_default' fallback 日志（不触发 fallback）
    使用真实 _WORLDS_BASE，不 patch，证明现有 world_layer 不受影响。
    """
    from core.dream.world_loader import load_world

    with caplog.at_level(logging.INFO, logger="core.dream.world_loader"):
        pkg = load_world(world_id)

    assert pkg.ruleset, f"[world={world_id}] ruleset 应非空（无需 fallback）"
    assert pkg.mes_example, f"[world={world_id}] mes_example 应非空（无需 fallback）"

    fallback_logs = [
        r for r in caplog.records
        if "source=_default" in r.getMessage()
    ]
    assert not fallback_logs, (
        f"[world={world_id}] 不应触发 _default fallback，但发现:\n"
        + "\n".join(r.getMessage() for r in fallback_logs)
    )
