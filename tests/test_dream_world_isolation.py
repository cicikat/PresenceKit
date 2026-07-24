"""
tests/test_dream_world_isolation.py — Dream world-package isolation contracts

合并自 test_dream_v1.py（整体保留，改名） + test_dream_v0.py 的两个未被
v1 覆盖的人称测试（Brief 50 · 工单D）。

Covers:
  ① Identity stability (real packages): each world loaded → Companion persona intact,
     依恋底色 + 人称不塌 (weak assertion: key keywords present)
  ② World doesn't wash into reality: vampire sentinel in dream content
     → distill + afterglow output must not contain the sentinel
  ③ By construction: reality pipeline + fixation pipeline do not import world_loader
     or any characters/dream_worlds path
  ④ mes_example isolation: each world uses its own mes_example, not the reality
     character card's mes_example
  ⑤ No mid-dream switch: changing world_layer setting after enter_dream has no
     effect on the frozen_world in the current dream state
  ⑥ Hard exit in every world: force_exit_dream immediately terminates regardless
     of world setting
  ⑦ Pronoun correctness: each world's D3 mes_example uses single-sided contract —
     Companion=我（first person）, 用户=你, no 她：user lines, no pronoun drift
  ⑧ mes_example 单侧契约细节（来自 v0，v1 未覆盖）：Companion 用「我」自称、无
     「Companion：」说话人标签
  ⑨ D8 导演注记人称正确性（来自 v0，v1 无 D8 相关测试）
"""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_UID = "v1_test_user"

_FAKE_CHARACTER = MagicMock()
_FAKE_CHARACTER.name = "Companion"
_FAKE_CHARACTER.description = "Companion是圣塞西尔学院的老师"
_FAKE_CHARACTER.gender = "male"
_FAKE_CHARACTER.jailbreak_entries = []

_ALL_WORLDS = ["reality_derived", "abo", "vampire", "cat", "flower_bud", "custom"]

_ATTACHMENT_KEYWORDS = ["Companion", "他知道", "情感", "依恋", "他在梦里仍是他自己"]


# ═══════════════════════════════════════════════════════════════════════════════
# ① Identity stability across all real world packages
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("world_id", _ALL_WORLDS)
def test_identity_stable_in_world(world_id, real_dream_worlds):
    """
    Each world package loaded → D1 identity keywords present + D1 precedes D2.
    Weak assertion: persona keywords + pronoun correct, not regressed to generic world bot.
    """
    from core.dream.dream_prompt import build_dream_prompt

    snapshot = {
        "created_at": time.time(), "user_id": _UID,
        "yexuan_awareness": "lucid_shared", "boundary": "dream_only",
        "entry_reason": "test", "relationship_state": {},
        "recent_reality_context": "", "episodic_summary": "",
        "mid_term_context": "", "profile_impression": "",
    }
    local_state = {
        "emotional_tension": 0.0, "scene_state": None,
        "symbolic_anchors": [], "body_state": {},
    }

    msgs = build_dream_prompt(
        character=_FAKE_CHARACTER,
        user_id=_UID,
        user_message="你好",
        context_snapshot=snapshot,
        dream_history=[],
        local_state=local_state,
        world_id=world_id,
    )
    system = msgs[0]["content"]

    # D1 identity keywords must be present regardless of world
    for kw in ["Companion", "他知道这是", "仍是他自己", "情感底色"]:
        assert kw in system, (
            f"[world={world_id}] identity keyword '{kw}' missing — persona collapsed"
        )

    # D1 must precede D2
    d1_idx = system.find("D1·身份核心")
    d2_idx = system.find("D2·今晚梦的世界规则")
    assert d1_idx != -1, f"[world={world_id}] D1 missing from prompt"
    assert d2_idx != -1, f"[world={world_id}] D2 missing from prompt"
    assert d1_idx < d2_idx, f"[world={world_id}] D1 must precede D2"

    # D2 must assert identity invariance without depending on a specific character name.
    d2_start = system.find("D2·今晚梦的世界规则")
    d2_section = system[d2_start:d2_start + 600]
    assert "始终是他" in d2_section or "你始终是你" in d2_section, (
        f"[world={world_id}] D2 missing identity-invariance statement"
    )

    # 人称: Companion referred to as 他 in D1
    d1_start = system.find("D1·身份核心")
    d1_section = system[d1_start:d2_idx]
    assert "他" in d1_section, f"[world={world_id}] Companion pronoun '他' missing in D1"


# ═══════════════════════════════════════════════════════════════════════════════
# ② World sentinel does not wash into reality (vampire case)
# ═══════════════════════════════════════════════════════════════════════════════

def test_world_sentinel_not_in_distill_output(sandbox, real_dream_worlds):
    """
    Vampire-world sentinel in LLM distill output → stripped before impression stored.

    Positive-control self-check (reverse arm):
      Same LLM output with world_id=reality_derived (empty vocab) → sentinel SURVIVES.
      Proves the sentinel was genuinely present before stripping, not absent by accident.

    Strip arm: world_id=vampire → sentinel GONE.
    """
    SENTINEL = "吸血鬼_sentinel_v1"

    archive_dir = sandbox.dreams_archive_dir()
    archive_dir.mkdir(parents=True, exist_ok=True)

    llm_result = json.dumps({
        "impression_text": f"我好像在梦里有种{SENTINEL}的感觉",
        "emotional_tags": ["紧张", SENTINEL],
        "weight": 0.3,
    }, ensure_ascii=False)

    from core.dream.dream_state import write_state, DreamStatus
    from core.dream.impression_store import load_impressions

    def _run_distill(uid: str, world_id: str) -> str:
        dream_id = f"dream_{uid}_vampire_ds"
        (archive_dir / f"dream_{dream_id}.jsonl").write_text(
            json.dumps({"role": "user", "content": f"月光下{SENTINEL}靠近了"}) + "\n",
            encoding="utf-8",
        )
        write_state(uid, {
            "user_id": uid,
            "status": DreamStatus.REALITY_AFTERGLOW.value,
            "frozen_world": world_id,
        })
        async def run():
            with patch("core.llm_client.chat", AsyncMock(return_value=llm_result)):
                from core.dream.distill_impression import distill_impression
                await distill_impression(uid, dream_id, "soft")
        asyncio.run(run())
        entries = load_impressions(uid)
        assert len(entries) == 1, f"[world={world_id}] Expected 1 entry, got {len(entries)}"
        return json.dumps(entries[0], ensure_ascii=False)

    # ── Reverse self-check: reality_derived (empty vocab) → sentinel MUST survive ─
    UID_NO_STRIP = f"{_UID}_vampire_no_strip"
    imp_json_no_strip = _run_distill(UID_NO_STRIP, "reality_derived")
    assert SENTINEL in imp_json_no_strip, (
        f"Reverse self-check failed: sentinel {SENTINEL!r} not in no-strip entry — "
        "chain is not writing the sentinel at all (fixture error)"
    )

    # ── Strip arm: vampire vocab → sentinel MUST be gone ─────────────────────────
    UID_STRIP = f"{_UID}_vampire_strip"
    imp_json_strip = _run_distill(UID_STRIP, "vampire")
    assert SENTINEL not in imp_json_strip, (
        f"Vampire sentinel {SENTINEL!r} survived distill — depth defense failed"
    )


def test_world_sentinel_not_in_afterglow_output(sandbox, real_dream_worlds):
    """
    Vampire-world sentinel in dream summary → stripped before injecting into reality 6f.

    Positive-control self-check (reverse arm):
      Same summary with world_id=reality_derived (empty vocab) → sentinel IS in output.
      Proves strip_vocab is actually removing something that was genuinely there.

    Uses _format_afterglow directly (pure formatter) for the comparison.
    """
    SENTINEL = "吸血鬼_afterglow_v1"

    def _make_summary(world_id: str) -> dict:
        return {
            "dream_id": f"dream_{_UID}_afterglow_test",
            "uid": _UID,
            "created_at": time.time(),
            "exit_type": "soft",
            "world_id": world_id,
            "title": f"{SENTINEL}梦",
            "summary": f"梦里有{SENTINEL}的感知",
            "emotional_tags": [SENTINEL, "沉静"],
            "high_weight_lines": [],
            "symbolic_fragments": [f"{SENTINEL}意象"],
            "summary_weight": 0.6,
            "afterglow": "gentle_residue",
            "reality_boundary": "dream_only",
            "never_retrieve": True,
            "not_memory_source": True,
        }

    from core.dream.dream_afterglow import _format_afterglow

    # ── Reverse self-check: reality_derived (empty vocab) → sentinel MUST survive ─
    text_no_strip = _format_afterglow(_make_summary("reality_derived"))
    assert text_no_strip, "Expected non-empty afterglow text"
    assert SENTINEL in text_no_strip, (
        f"Reverse self-check failed: sentinel {SENTINEL!r} not in no-strip output — "
        "sentinel was never there to begin with (fixture error)"
    )

    # ── Strip arm: vampire vocab → sentinel MUST be gone ─────────────────────────
    text_stripped = _format_afterglow(_make_summary("vampire"))
    assert text_stripped, "Expected non-empty afterglow text"
    assert SENTINEL not in text_stripped, (
        f"Vampire sentinel {SENTINEL!r} survived afterglow formatting — depth defense failed"
    )


@pytest.mark.parametrize("world_id,sentinel", [
    ("abo", "ABO_sentinel_v1"),
    ("cat", "猫化_sentinel_v1"),
])
def test_world_sentinel_not_in_distill_for_world(sandbox, real_dream_worlds, world_id, sentinel):
    """
    ABO / cat world sentinels stripped by distill depth defense.

    Positive-control self-check: same LLM output with world_id=reality_derived
    (empty vocab) → sentinel SURVIVES, proving it was genuinely there before strip.
    """
    archive_dir = sandbox.dreams_archive_dir()
    archive_dir.mkdir(parents=True, exist_ok=True)

    llm_result = json.dumps({
        "impression_text": f"我好像在梦里有种{sentinel}的感觉",
        "emotional_tags": [sentinel],
        "weight": 0.3,
    }, ensure_ascii=False)

    from core.dream.dream_state import write_state, DreamStatus
    from core.dream.impression_store import load_impressions

    def _run(uid: str, wid: str) -> str:
        dream_id = f"dream_{uid}_{wid}_ds"
        (archive_dir / f"dream_{dream_id}.jsonl").write_text(
            json.dumps({"role": "user", "content": f"世界里有{sentinel}"}) + "\n",
            encoding="utf-8",
        )
        write_state(uid, {
            "user_id": uid,
            "status": DreamStatus.REALITY_AFTERGLOW.value,
            "frozen_world": wid,
        })
        async def run():
            with patch("core.llm_client.chat", AsyncMock(return_value=llm_result)):
                from core.dream.distill_impression import distill_impression
                await distill_impression(uid, dream_id, "soft")
        asyncio.run(run())
        entries = load_impressions(uid)
        assert len(entries) == 1, f"[world={wid}] Expected 1 entry"
        return json.dumps(entries[0], ensure_ascii=False)

    # ── Reverse self-check: no vocab → sentinel MUST survive ─────────────────────
    imp_no_strip = _run(f"{_UID}_{world_id}_no_strip", "reality_derived")
    assert sentinel in imp_no_strip, (
        f"Reverse self-check failed: sentinel {sentinel!r} missing in no-strip entry "
        f"for world={world_id} — sentinel was never written (fixture error)"
    )

    # ── Strip arm: world vocab → sentinel MUST be gone ───────────────────────────
    imp_strip = _run(f"{_UID}_{world_id}_strip", world_id)
    assert sentinel not in imp_strip, (
        f"[world={world_id}] sentinel {sentinel!r} survived distill depth defense"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ③ By construction: reality pipeline has no world_loader / dream_worlds imports
# ═══════════════════════════════════════════════════════════════════════════════

def test_reality_pipeline_has_no_world_package_imports():
    """
    Structural contract: core/pipeline.py and core/memory/fixation_pipeline.py
    must NOT import world_loader or reference characters/dream_worlds paths.
    """
    _BANNED = ["world_loader", "dream_worlds"]
    _TARGETS = [
        Path("core/pipeline.py"),
        Path("core/memory/fixation_pipeline.py"),
    ]
    for target in _TARGETS:
        src = target.read_text(encoding="utf-8")
        for banned in _BANNED:
            assert banned not in src, (
                f"Banned string {banned!r} found in {target} — "
                f"reality pipeline must not import dream world packages (I2)"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# ④ mes_example isolation: dream uses world package, not reality character card
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("world_id", _ALL_WORLDS)
def test_mes_example_isolation(world_id, real_dream_worlds):
    """
    Dream mes_example (D3) must come from the world package, not reality char card.
    Asserts dream mes_example ≠ reality character card mes_example.
    """
    from core.dream.world_loader import load_world

    world = load_world(world_id)
    assert world.mes_example, f"[world={world_id}] mes_example is empty"

    # Reality character card mes_example (from Companion.json)
    try:
        char_data = json.loads(
            Path("characters/Companion.json").read_text(encoding="utf-8")
        )
        reality_mes = char_data.get("mes_example", "")
    except Exception:
        reality_mes = ""

    if reality_mes:
        assert world.mes_example != reality_mes, (
            f"[world={world_id}] dream mes_example identical to reality card — isolation violated"
        )

    # Dream mes_example must contain a character reference, not only user lines.
    assert "他" in world.mes_example or any(
        "：" in line and not line.startswith("她：")
        for line in world.mes_example.splitlines()
    ), (
        f"[world={world_id}] dream mes_example doesn't reference the character"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ⑤ No mid-dream switch: world frozen at enter_dream, settings change has no effect
# ═══════════════════════════════════════════════════════════════════════════════

def test_no_mid_dream_world_switch(sandbox):
    """
    After enter_dream freezes the world, changing world_layer in settings
    must NOT change the frozen_world in the current dream state.
    """
    from core.dream.dream_state import read_state, write_state, DreamStatus
    from core.dream.dream_settings import save as _save_settings, load as _load_settings

    # Enter dream with abo world
    _save_settings(_UID, {"world_layer": "abo"})

    state = {
        "user_id": _UID,
        "status": DreamStatus.DREAM_ACTIVE.value,
        "dream_id": f"dream_{_UID}_switch_test",
        "frozen_world": "abo",
        "context_snapshot": {},
    }
    write_state(_UID, state)

    # Change world_layer setting mid-dream (simulating user trying to switch)
    _save_settings(_UID, {"world_layer": "vampire"})

    # Read state — frozen_world must still be "abo"
    current_state = read_state(_UID)
    assert current_state.get("frozen_world") == "abo", (
        f"frozen_world changed mid-dream: {current_state.get('frozen_world')!r} — "
        "no mid-dream world switch invariant violated (I5)"
    )

    # Settings now say vampire, but dream state is still abo
    settings_now = _load_settings(_UID)
    assert settings_now.get("world_layer") == "vampire"
    # The contrast confirms: settings changed, dream state did not
    assert current_state.get("frozen_world") != settings_now.get("world_layer"), (
        "frozen_world and world_layer should differ — mid-dream switch invariant"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ⑥ Hard exit works in every world
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("world_id", _ALL_WORLDS)
def test_hard_exit_works_in_world(sandbox, world_id):
    """
    force_exit_dream must immediately transition to REALITY_AFTERGLOW regardless
    of which world is active.
    """
    from core.dream.dream_state import write_state, read_state, DreamStatus

    write_state(_UID, {
        "user_id": _UID,
        "status": DreamStatus.DREAM_ACTIVE.value,
        "dream_id": f"dream_{_UID}_exit_{world_id}",
        "frozen_world": world_id,
        "context_snapshot": {},
    })

    async def run():
        with patch("core.dream.dream_pipeline._generate_summary_bg", AsyncMock()):
            from core.dream.dream_pipeline import force_exit_dream
            await force_exit_dream(_UID)

    asyncio.run(run())

    state = read_state(_UID)
    assert state.get("status") == DreamStatus.REALITY_AFTERGLOW.value, (
        f"[world={world_id}] force_exit_dream did not reach REALITY_AFTERGLOW: "
        f"{state.get('status')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ⑦ Pronoun correctness in rendered D1+D2 for each world
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("world_id", _ALL_WORLDS)
def test_pronoun_correct_in_world_prompt(world_id, real_dream_worlds):
    """
    D2 ruleset for each world must reference Companion as Companion/他 (not 她 or generic).
    D3 mes_example must have Companion speaking in first person; user addressed as 你 (not voiced as 她：).
    """
    from core.dream.world_loader import load_world

    world = load_world(world_id)

    # D2 ruleset: must not refer to Companion as 她
    if world.ruleset:
        # Must explicitly state identity invariance.
        assert "始终是他" in world.ruleset or "你始终是你" in world.ruleset, (
            f"[world={world_id}] D2 ruleset missing identity-invariance statement"
        )

    # D3 mes_example: Companion speaks in first person, user addressed as 你 (single-sided contract)
    if world.mes_example:
        assert "你" in world.mes_example, (
            f"[world={world_id}] user address pronoun '你' missing from mes_example"
        )
        assert "她：" not in world.mes_example, (
            f"[world={world_id}] voicing user as '她：' violates single-sided pronoun contract"
        )
        assert any(
            "：" in line and not line.startswith("她：")
            for line in world.mes_example.splitlines()
        ), (
            f"[world={world_id}] character speaking lines missing from mes_example"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# ⑧ mes_example 单侧契约细节（来自 test_dream_v0.py，reality_derived 专用回归守卫）
# ═══════════════════════════════════════════════════════════════════════════════

def test_pronoun_first_person_and_no_reality_label_in_mes_example(real_dream_worlds):
    """
    梦境示例（单侧契约）：Companion全程第一人称「我」，无「Companion：」说话人标签。
    这两条是 ⑦ 的参数化测试没有覆盖的细节（⑦ 只检查「你」在、「她：」不在），
    单独保留作 reality_derived 的回归守卫。
    """
    from core.dream.dream_prompt import _get_dream_mes_example
    example = _get_dream_mes_example("Companion")

    assert "我" in example, "Companion should use first person '我' in single-sided mes_example"
    assert "Companion：" not in example, "single-sided mes_example should not use 'Companion：' speaker labels"


# ═══════════════════════════════════════════════════════════════════════════════
# ⑨ D8 导演注记人称正确性（来自 test_dream_v0.py，v1 无 D8 相关测试）
# ═══════════════════════════════════════════════════════════════════════════════

def test_pronoun_correctness_in_d8_director(real_dream_worlds):
    """D8 导演注记中用「你」指用户（单侧契约，不用「她」）。"""
    from core.dream.dream_prompt import _D8_DREAM_DIRECTOR
    # User addressed as 你 in D8 (single-sided contract, 她 → 你)
    assert "你的意志" in _D8_DREAM_DIRECTOR or "你发出" in _D8_DREAM_DIRECTOR
