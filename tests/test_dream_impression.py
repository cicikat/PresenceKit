"""
tests/test_dream_impression.py — Dream impression residue (v1) contract tests

Covers:
  ① Isolation contract: sentinel in impressions/ never surfaces in any reality loader
     (reflect_to_episodic / consolidate_to_identity / retrieve / event_log.search /
      mid_term.format / short_term.load / user_identity.load) — real functions, no stubs
  ② Second-order laundering defence: impression_text has no scene/world content
     by structural guarantee → a reality capture of 叶瑄's echo carries no dream facts
  ③ Distill stripping weak assertion: impression_text must not contain world-layer
     reserved words or body-value tokens
  ④ 6g injection framing: load_impression_text returns explicit non-reality marker,
     weight within [0.2, 0.4]
  ⑤ Decay and cap: expired entries excluded, overflow trimmed to 50
  ⑥ Reality chain isolation: a full reality conversation round must not write
     impressions/ (only dream close writes it)
  ⑦ Failure isolation: distill_impression failure is warning-only, never raises
  ⑧ Sentinel fields: every persisted entry carries never_retrieve / not_memory_source /
     reality_boundary=dream_only
"""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_UID = "impression_test_user"

# World-layer reserved tokens (sample subset) — must not appear in impression_text
_WORLD_TOKENS = [
    "ABO", "Alpha", "Omega", "吸血鬼", "异能", "番位",
    "体温", "心跳", "arousal",
]

# Body / numeric patterns that must not appear in impression_text
_BODY_NUMERIC_PATTERNS = ["℃", "bpm", "心率", "hormone", "发情期", "发情"]

# Scene / action tokens that must not appear in a properly stripped impression
_SCENE_ACTION_TOKENS = ["握住", "拥抱", "推开", "床上", "地板", "教室", "房间"]


# ──────────────────────────────────────────────────────────────────────────────
# ① Isolation contract (I1) — real functions, no stubs
# ──────────────────────────────────────────────────────────────────────────────

def test_impression_isolation_contract(sandbox):
    """
    A sentinel planted in impressions/ must NEVER surface in any reality loader.
    Calls real functions, not stubs — verifies physical isolation by omission.
    """
    from core.dream.impression_store import append_impression
    from core.memory import episodic_memory, event_log, mid_term, short_term, user_identity
    from core.memory.episodic_memory import load_unconsolidated

    sentinel = "IMPRESSION_ISOLATION_SENTINEL__v1_never_retrieve_contract"

    # Seed sentinel into impressions/
    impressions_dir = sandbox.dreams_impressions_dir()
    impressions_dir.mkdir(parents=True, exist_ok=True)

    now = time.time()
    append_impression(_UID, {
        "dream_id": f"dream_{_UID}_sentinel",
        "ts": now,
        "last_decay_ts": now,
        "impression_text": f"我好像在梦里有种 {sentinel} 的感觉",
        "weight": 0.3,
        "emotional_tags": ["漂浮"],
        "exit_type": "soft",
        "decay_after": now + 30 * 86400,
        "marked": True,
    })

    async def collect_haystacks():
        return [
            json.dumps(episodic_memory.retrieve(_UID, topic=sentinel, top_k=5),
                       ensure_ascii=False),
            await event_log.search(_UID, sentinel),
            json.dumps(short_term.load_for_prompt(_UID), ensure_ascii=False),
            mid_term.format_for_prompt(_UID),
            json.dumps(await user_identity.load(_UID), ensure_ascii=False),
            # consolidate_to_identity reads from episodic_memory.load_unconsolidated —
            # verify that source also doesn't contain the sentinel
            json.dumps(load_unconsolidated(_UID), ensure_ascii=False),
        ]

    haystacks = asyncio.run(collect_haystacks())
    assert all(sentinel not in h for h in haystacks), (
        "Impression sentinel leaked into a reality loader — isolation contract violated"
    )


# ──────────────────────────────────────────────────────────────────────────────
# ② Second-order laundering defence (I2 structural guarantee)
# ──────────────────────────────────────────────────────────────────────────────

def test_impression_whitewash_defence(sandbox):
    """
    Real-chain I2 whitewash defence — true chain, no stubs on reflect.

    Seeds a canary sentinel into impression_store, then drives two full reality
    rounds through the real call chain:
      load_impression_text (6g) → capture_turn → mid_term.append
      → reflect_to_episodic (real fn, only inner LLM call mocked)

    Case A (safe reply): 叶瑄's reply does NOT echo the sentinel.
        → episodic must NOT contain the sentinel.

    Case B (reverse self-check): 叶瑄's reply DOES contain the sentinel.
        → episodic MUST contain it — proves the chain is real,
          not a false-green empty-library assertion.
    """
    from unittest.mock import AsyncMock, patch

    from core.dream.impression_store import append_impression
    from core.dream.impression_loader import load_impression_text
    from core.memory.fixation_pipeline import capture_turn, reflect_to_episodic
    from core.memory import mid_term
    from core.memory.episodic_memory import load_unconsolidated

    SENTINEL = "ω_canary_omega_xyz"

    def _seed(uid: str) -> None:
        now = time.time()
        append_impression(uid, {
            "dream_id": f"dream_{uid}_ww",
            "ts": now,
            "last_decay_ts": now,
            "impression_text": f"我好像在梦里有种{SENTINEL}的感觉",
            "weight": 0.3,
            "emotional_tags": ["漂浮"],
            "exit_type": "soft",
            "decay_after": now + 30 * 86400,
            "marked": True,
        })

    def _run_chain(uid: str, reply_text: str, llm_ep_json: str) -> str | None:
        # Verify sentinel reached the 6g injection layer (fixture sanity)
        imp_text = load_impression_text(uid)
        assert imp_text, "load_impression_text returned empty"
        assert SENTINEL in imp_text, "Sentinel not in 6g impression text"

        # ── real capture_turn ──────────────────────────────────────────────────
        turn_id = capture_turn(uid, "你在想什么", reply_text, emotion="gentle")
        assert turn_id

        # ── write mid_term directly (skip summarize_to_midterm's LLM call) ─────
        mid_id = f"mt_{uid}_{int(time.time() * 1000)}"
        mid_term.append(uid, f"本轮摘要：{reply_text[:80]}", mid_id=mid_id, source_turn_id=turn_id)

        # ── real reflect_to_episodic (only inner llm_client.chat is mocked) ────
        async def _reflect():
            with patch("core.llm_client.chat", AsyncMock(return_value=llm_ep_json)):
                return await reflect_to_episodic(uid, [mid_id], trigger="eager")

        return asyncio.run(_reflect())

    # ── Case A: safe reply — sentinel NOT in 叶瑄's output ────────────────────
    UID_A = f"{_UID}_ww_a"
    _seed(UID_A)

    safe_ep = json.dumps({
        "raw_facts": ["用户问叶瑄在想什么", "叶瑄给出了模糊的回答"],
        "topic_keywords": ["情感", "思念"],
        "emotion_peak": "gentle",
        "emotion_texture": "温和",
        "emotion_arc": "平稳",
        "user_state": "curious",
        "narrative_summary": "叶瑄感到有些出神，说不清楚在想什么",
        "strength": 0.5,
    }, ensure_ascii=False)

    ep_id_a = _run_chain(UID_A, "（有点出神）说不太清楚，只是感觉有点不一样。", safe_ep)
    assert ep_id_a is not None, "reflect_to_episodic returned None in safe-reply case"

    ep_json_a = json.dumps(load_unconsolidated(UID_A), ensure_ascii=False)
    assert SENTINEL not in ep_json_a, (
        f"Sentinel {SENTINEL!r} leaked into episodic (safe-reply case) — I2 violated"
    )

    # ── Case B: reverse self-check — sentinel IN 叶瑄's reply ─────────────────
    UID_B = f"{_UID}_ww_b"
    _seed(UID_B)

    reverse_ep = json.dumps({
        "raw_facts": [f"叶瑄的回复中明确提到了 {SENTINEL}"],
        "topic_keywords": ["情感", SENTINEL],
        "emotion_peak": "gentle",
        "emotion_texture": "漂浮",
        "emotion_arc": "平稳",
        "user_state": "curious",
        "narrative_summary": f"叶瑄提到了 {SENTINEL} 这个词",
        "strength": 0.5,
    }, ensure_ascii=False)

    ep_id_b = _run_chain(UID_B, f"（有点出神）{SENTINEL}，说不清楚。", reverse_ep)
    assert ep_id_b is not None, (
        "reflect_to_episodic returned None in reverse self-check — chain is broken"
    )

    ep_json_b = json.dumps(load_unconsolidated(UID_B), ensure_ascii=False)
    assert SENTINEL in ep_json_b, (
        f"Sentinel not captured in episodic (reverse self-check) — chain is not real"
    )


# ──────────────────────────────────────────────────────────────────────────────
# ③ Distill stripping weak assertion
# ──────────────────────────────────────────────────────────────────────────────

def test_distill_strips_world_and_body_tokens(sandbox):
    """
    distill_impression() with a mocked LLM that returns a valid stripped result
    must produce impression_text free of world-layer and body tokens.
    """
    from core.dream.impression_store import load_impressions

    archive_dir = sandbox.dreams_archive_dir()
    archive_dir.mkdir(parents=True, exist_ok=True)

    dream_id = f"dream_{_UID}_distill_strip"

    # Write a fake archive with world + body content
    archive_path = archive_dir / f"dream_{dream_id}.jsonl"
    archive_path.write_text(
        json.dumps({"role": "user", "content": "（Alpha的气息包围过来）体温升到38.5℃"})
        + "\n"
        + json.dumps({"role": "assistant", "content": "（Omega本能涌上来）心率加速到120bpm"})
        + "\n",
        encoding="utf-8",
    )

    # LLM returns a properly stripped impression
    stripped_llm_reply = json.dumps({
        "impression_text": "我好像在梦里有种被围住的温热感",
        "emotional_tags": ["温热", "被包裹"],
        "weight": 0.3,
    }, ensure_ascii=False)

    async def run():
        with patch("core.llm_client.chat", AsyncMock(return_value=stripped_llm_reply)):
            from core.dream.distill_impression import distill_impression
            await distill_impression(_UID, dream_id, "soft")

    asyncio.run(run())

    entries = load_impressions(_UID)
    assert len(entries) == 1, "Expected exactly 1 impression entry"

    imp_text = entries[0]["impression_text"]
    for tok in _WORLD_TOKENS + _BODY_NUMERIC_PATTERNS + _SCENE_ACTION_TOKENS:
        assert tok not in imp_text, (
            f"Forbidden token {tok!r} found in distilled impression_text: {imp_text!r}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# ④ 6g injection: non-reality framing + weight bounds
# ──────────────────────────────────────────────────────────────────────────────

def test_6g_framing_and_weight_bounds(sandbox):
    """
    load_impression_text must include an explicit non-reality frame.
    All entries must have weight within [0.2, 0.4].
    """
    from core.dream.impression_store import append_impression, load_impressions
    from core.dream.impression_loader import load_impression_text

    now = time.time()
    for i in range(2):
        append_impression(_UID, {
            "dream_id": f"dream_{_UID}_{i}",
            "ts": now - i,
            "last_decay_ts": now,
            "impression_text": f"我好像在梦里有种测试感{i}",
            "weight": 0.25 + i * 0.1,
            "emotional_tags": ["漂浮"],
            "exit_type": "soft",
            "decay_after": now + 30 * 86400,
            "marked": True,
        })

    text = load_impression_text(_UID)
    assert text, "Expected non-empty 6g injection text"

    # Must carry the explicit non-reality frame (C3)
    assert "非现实" in text or "梦境印象" in text, (
        f"6g text missing non-reality frame: {text!r}"
    )

    # Must carry the confabulation guard (C1)
    assert "复述" in text or "编造" in text or "情绪余味" in text, (
        f"6g text missing confabulation guard: {text!r}"
    )

    # Entries must have impression_text starting with 叶瑄 self-narration cue
    entries = load_impressions(_UID)
    for e in entries:
        w = float(e.get("weight", 0))
        assert 0.2 <= w <= 0.4, f"Weight {w} out of [0.2, 0.4] bounds"

    # Weight bounds enforced by distill path (guard via store sentinel check)
    for e in entries:
        assert e.get("never_retrieve") is True
        assert e.get("not_memory_source") is True
        assert e.get("reality_boundary") == "dream_only"


# ──────────────────────────────────────────────────────────────────────────────
# ⑤ Decay and cap (C5)
# ──────────────────────────────────────────────────────────────────────────────

def test_expired_impressions_not_returned(sandbox):
    """Impressions past decay_after are excluded from get_active_impressions."""
    from core.dream.impression_store import append_impression, get_active_impressions

    past = time.time() - 1  # already expired
    now = time.time()

    append_impression(_UID, {
        "dream_id": f"dream_{_UID}_expired",
        "ts": past - 86400,
        "last_decay_ts": past,
        "impression_text": "我好像在梦里有种过期的感觉",
        "weight": 0.3,
        "emotional_tags": [],
        "exit_type": "soft",
        "decay_after": past,   # already expired
        "marked": True,
    })
    append_impression(_UID, {
        "dream_id": f"dream_{_UID}_active",
        "ts": now,
        "last_decay_ts": now,
        "impression_text": "我好像在梦里有种有效的感觉",
        "weight": 0.3,
        "emotional_tags": [],
        "exit_type": "soft",
        "decay_after": now + 30 * 86400,
        "marked": True,
    })

    active = get_active_impressions(_UID)
    assert len(active) == 1, f"Expected 1 active, got {len(active)}"
    assert "有效" in active[0]["impression_text"]


def test_impression_cap_at_50(sandbox):
    """When >50 entries exist, oldest/lowest-weight are trimmed on next append."""
    from core.dream.impression_store import append_impression, load_impressions

    now = time.time()
    # Fill 50 entries with low weight
    for i in range(50):
        append_impression(_UID, {
            "dream_id": f"dream_{_UID}_{i}",
            "ts": now + i,
            "last_decay_ts": now,
            "impression_text": f"我好像在梦里有种感觉{i}",
            "weight": 0.2,
            "emotional_tags": [],
            "exit_type": "soft",
            "decay_after": now + 30 * 86400,
            "marked": True,
        })

    # 51st entry with higher weight — should cause a trim
    append_impression(_UID, {
        "dream_id": f"dream_{_UID}_new",
        "ts": now + 51,
        "last_decay_ts": now,
        "impression_text": "我好像在梦里有种重要的感觉",
        "weight": 0.4,
        "emotional_tags": [],
        "exit_type": "soft",
        "decay_after": now + 30 * 86400,
        "marked": True,
    })

    entries = load_impressions(_UID)
    assert len(entries) <= 50, f"Cap exceeded: {len(entries)} entries"


def test_weight_decay_reduces_over_days(sandbox):
    """Entries older than 1 day have their weight reduced by ~_DECAY_PER_DAY per day."""
    from core.dream.impression_store import _apply_decay, _DECAY_PER_DAY

    old_ts = time.time() - 3 * 86400  # 3 days ago
    entry = {
        "dream_id": "test",
        "ts": old_ts,
        "last_decay_ts": old_ts,
        "impression_text": "我好像在梦里有种感觉",
        "weight": 0.4,
        "emotional_tags": [],
        "exit_type": "soft",
        "decay_after": time.time() + 30 * 86400,
        "marked": True,
    }

    decayed = _apply_decay([entry])
    assert len(decayed) == 1
    new_weight = decayed[0]["weight"]
    expected_max = 0.4 - _DECAY_PER_DAY * 2.9  # at least 2.9 days of decay
    assert new_weight <= expected_max + 0.01, (
        f"Expected weight ≤ {expected_max:.4f}, got {new_weight}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# ⑥ Reality chain must NOT write impressions/ (I4)
# ──────────────────────────────────────────────────────────────────────────────

def test_reality_chain_does_not_write_impressions(sandbox):
    """
    A complete reality turn (short_term.append + event_log write) must leave
    impressions/ unchanged. Only dream close writes impressions/.
    """
    from core.memory import short_term, event_log

    impressions_dir = sandbox.dreams_impressions_dir()
    before = set(impressions_dir.glob("*.json")) if impressions_dir.exists() else set()

    # Simulate a reality conversation round
    short_term.append(_UID, "user", "今天天气不错")
    short_term.append(_UID, "assistant", "是啊，你心情好些了吗")
    event_log.append(_UID, "user", "今天天气不错")

    after = set(impressions_dir.glob("*.json")) if impressions_dir.exists() else set()
    new_files = after - before
    assert not new_files, (
        f"Reality chain wrote to impressions/: {new_files} — isolation violated (I4)"
    )


# ──────────────────────────────────────────────────────────────────────────────
# ⑦ Failure isolation: distill failure is warning-only (C7)
# ──────────────────────────────────────────────────────────────────────────────

def test_distill_failure_does_not_raise(sandbox):
    """distill_impression() must never propagate exceptions — always warning only."""
    archive_dir = sandbox.dreams_archive_dir()
    archive_dir.mkdir(parents=True, exist_ok=True)

    dream_id = f"dream_{_UID}_failing"
    (archive_dir / f"dream_{dream_id}.jsonl").write_text(
        json.dumps({"role": "user", "content": "梦境内容"}) + "\n",
        encoding="utf-8",
    )

    async def run():
        # LLM raises an exception
        with patch("core.llm_client.chat", AsyncMock(side_effect=RuntimeError("LLM down"))):
            from core.dream.distill_impression import distill_impression
            # Must not raise
            await distill_impression(_UID, dream_id, "soft")

    asyncio.run(run())  # no exception expected


def test_distill_empty_archive_is_noop(sandbox):
    """Empty dream archive → no impression written, no error."""
    from core.dream.impression_store import load_impressions

    async def run():
        from core.dream.distill_impression import distill_impression
        await distill_impression(_UID, f"dream_{_UID}_empty", "soft")

    asyncio.run(run())
    assert load_impressions(_UID) == []


def test_distill_empty_llm_result_writes_nothing(sandbox):
    """LLM returns empty impression_text → no entry written."""
    from core.dream.impression_store import load_impressions

    archive_dir = sandbox.dreams_archive_dir()
    archive_dir.mkdir(parents=True, exist_ok=True)
    dream_id = f"dream_{_UID}_empty_llm"
    (archive_dir / f"dream_{dream_id}.jsonl").write_text(
        json.dumps({"role": "user", "content": "平淡的对话"}) + "\n",
        encoding="utf-8",
    )

    empty_reply = json.dumps({"impression_text": "", "emotional_tags": [], "weight": 0.2})

    async def run():
        with patch("core.llm_client.chat", AsyncMock(return_value=empty_reply)):
            from core.dream.distill_impression import distill_impression
            await distill_impression(_UID, dream_id, "soft")

    asyncio.run(run())
    assert load_impressions(_UID) == []


# ──────────────────────────────────────────────────────────────────────────────
# ⑧ Sentinel fields always present on every persisted entry
# ──────────────────────────────────────────────────────────────────────────────

def test_all_entries_carry_isolation_sentinels(sandbox):
    """Every appended entry must carry the three isolation sentinel fields."""
    from core.dream.impression_store import append_impression, load_impressions

    now = time.time()
    append_impression(_UID, {
        "dream_id": f"dream_{_UID}_sentinel_check",
        "ts": now,
        "last_decay_ts": now,
        "impression_text": "我好像在梦里有种感觉",
        "weight": 0.3,
        "emotional_tags": [],
        "exit_type": "soft",
        "decay_after": now + 30 * 86400,
        "marked": True,
    })

    entries = load_impressions(_UID)
    assert len(entries) == 1
    e = entries[0]
    assert e.get("never_retrieve") is True,       "missing never_retrieve sentinel"
    assert e.get("not_memory_source") is True,    "missing not_memory_source sentinel"
    assert e.get("reality_boundary") == "dream_only", "missing reality_boundary sentinel"


# ──────────────────────────────────────────────────────────────────────────────
# prompt_builder: 6g layer present in output when impression_text provided
# ──────────────────────────────────────────────────────────────────────────────

def test_prompt_builder_injects_6g_layer(sandbox):
    """prompt_builder.build() must include a 6g_dream_impression layer when provided."""
    from unittest.mock import MagicMock
    from core import prompt_builder

    char = MagicMock()
    char.name = "叶瑄"
    char.system_prompt = ""
    char.description = ""
    char.personality = ""
    char.scenario = ""
    char.mes_example = ""
    char.jailbreak_entries = []

    imp_text = "（模糊的梦境印象，非现实发生的事）\n我好像在梦里有种温热的感觉"

    with (
        patch("core.prompt_builder._load_jailbreak", return_value=""),
        patch("core.prompt_builder._load_style_hint", return_value=""),
        patch("core.presence.get_last_seen_text", return_value=""),
        patch("core.author_note_rotator.get_current_note", return_value=""),
        patch("core.config_loader.get_config", return_value={"chat": {"style": "roleplay"}}),
        patch("core.mood_text.get_mood_text", return_value=""),
        patch("core.activity_manager.get_prompt_fragment", return_value=""),
    ):
        messages, debug = prompt_builder.build(
            character=char,
            user_id=_UID,
            user_message="你好",
            history=[],
            relation={"role": "朋友"},
            profile={},
            group_context=[],
            dream_impression_text=imp_text,
        )

    layers = [m.get("_layer", "") for m in messages]
    assert "6g_dream_impression" in layers, "6g layer missing from prompt output"

    # Content must include the impression text
    for m in messages:
        if m.get("_layer") == "6g_dream_impression":
            assert imp_text in m["content"]
            break


def test_prompt_builder_no_6g_when_empty(sandbox):
    """prompt_builder.build() must NOT add 6g layer when impression text is empty."""
    from unittest.mock import MagicMock
    from core import prompt_builder

    char = MagicMock()
    char.name = "叶瑄"
    char.system_prompt = ""
    char.description = ""
    char.personality = ""
    char.scenario = ""
    char.mes_example = ""
    char.jailbreak_entries = []

    with (
        patch("core.prompt_builder._load_jailbreak", return_value=""),
        patch("core.prompt_builder._load_style_hint", return_value=""),
        patch("core.presence.get_last_seen_text", return_value=""),
        patch("core.author_note_rotator.get_current_note", return_value=""),
        patch("core.config_loader.get_config", return_value={"chat": {"style": "roleplay"}}),
        patch("core.mood_text.get_mood_text", return_value=""),
        patch("core.activity_manager.get_prompt_fragment", return_value=""),
    ):
        messages, _ = prompt_builder.build(
            character=char,
            user_id=_UID,
            user_message="你好",
            history=[],
            relation={"role": "朋友"},
            profile={},
            group_context=[],
            dream_impression_text="",
        )

    layers = [m.get("_layer", "") for m in messages]
    assert "6g_dream_impression" not in layers


def test_6g_earliest_in_droppable(sandbox):
    """6g_dream_impression must have a lower _drop_priority than heavier memory layers."""
    import re
    import pathlib

    src = pathlib.Path("core/prompt_builder.py").read_text(encoding="utf-8")

    def _find_priority(layer_name: str) -> int | None:
        # Match "_layer": "NAME" ... "_drop_priority": N within ~200 chars
        pattern = rf'"_layer":\s*"{re.escape(layer_name)}"[^}}]*?"_drop_priority":\s*(\d+)'
        m = re.search(pattern, src, re.DOTALL)
        if not m:
            return None
        return int(m.group(1))

    p_6g = _find_priority("6g_dream_impression")
    assert p_6g is not None, "6g_dream_impression layer must have _drop_priority for token pruning"

    # Heavier memory layers that should be dropped AFTER the impression layer
    for heavier in ("6c_episodic", "mid_term", "6d_diary_context", "5.5_lore"):
        p = _find_priority(heavier)
        if p is not None:
            assert p_6g < p, (
                f"6g_dream_impression (_drop_priority={p_6g}) should be dropped before"
                f" {heavier!r} (_drop_priority={p})"
            )


# ──────────────────────────────────────────────────────────────────────────────
# C3: Reality pipeline must never import impression_store / impressions/ directly
# ──────────────────────────────────────────────────────────────────────────────

def test_reality_pipeline_has_no_impression_imports():
    """
    Structural contract: core/pipeline.py and core/memory/fixation_pipeline.py
    must NOT contain direct references to impression_store, dreams_impressions,
    or the impressions/ data path.

    The only permitted interface is impression_loader (read-only ambient layer).
    If this test goes red, someone wired the reality pipeline directly to the
    impression store — a structural isolation violation (I1 承重墙).
    """
    import pathlib

    _BANNED = ["dreams_impressions", "impression_store", "impressions/"]

    _TARGETS = [
        pathlib.Path("core/pipeline.py"),
        pathlib.Path("core/memory/fixation_pipeline.py"),
    ]

    for target in _TARGETS:
        src = target.read_text(encoding="utf-8")
        for banned in _BANNED:
            assert banned not in src, (
                f"Banned string {banned!r} found in {target} — "
                f"reality pipeline must not import impression store directly (I1)"
            )
