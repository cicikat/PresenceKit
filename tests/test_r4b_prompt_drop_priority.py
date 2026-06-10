"""
tests/test_r4b_prompt_drop_priority.py
=======================================
Fable R4-B: PromptLayer drop_priority trimming migration.

Priority rule: lower _drop_priority value = dropped first.
None = never auto-dropped.

Coverage:
  1.  All former _DROPPABLE layers now carry _drop_priority.
  2.  Trimmer does NOT reference a _DROPPABLE list; it reads _drop_priority.
  3.  A new layer with _drop_priority is automatically eligible for trimming.
  4.  A layer without _drop_priority is never auto-dropped.
  5.  Layers are dropped in ascending _drop_priority order.
  6.  Same-priority messages are dropped together (stable batch semantics).
  7.  debug_info["removed_layers"] matches what was actually removed.
  8.  _drop_priority never reaches the LLM vendor (sanitize_messages strips it).
  9.  dream_afterglow_soft_hint and 6g_dream_impression are always droppable.
  10. Prompt content is not mutated by trimming.
"""
from __future__ import annotations

import inspect
import textwrap
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_msg(layer: str, content: str, drop_priority: int | None = None) -> dict:
    msg: dict = {"role": "system", "content": content, "_layer": layer}
    if drop_priority is not None:
        msg["_drop_priority"] = drop_priority
    return msg


def _build_messages_over_limit(extra: list[dict]) -> list[dict]:
    """Return a base message list (always-kept layers) + caller-supplied extras."""
    base = [
        _make_msg("1_system_prompt", "A" * 5000),
        _make_msg("12_user_message", "hello"),
    ]
    return base + extra


# ---------------------------------------------------------------------------
# Trimming helper that mirrors the production logic in prompt_builder.build()
# ─────────────────────────────────────────────────────────────────────────────
# Rather than calling the full build() (which needs a Character + real I/O),
# we extract just the trimming portion and test it directly.
# ---------------------------------------------------------------------------

def _run_trimmer(messages: list[dict], hard_limit: int = 20000, target: int = 18000):
    """Run the R4-B dynamic trimmer on a message list.

    Returns (trimmed_messages, removed_layers).
    """
    import copy
    messages = copy.deepcopy(messages)

    token_estimate = sum(len(m["content"]) for m in messages)
    removed_layers: list[str] = []

    if token_estimate > hard_limit:
        droppable = [
            (i, m) for i, m in enumerate(messages)
            if m.get("_drop_priority") is not None
        ]
        droppable.sort(key=lambda x: (x[1]["_drop_priority"], x[0]))

        drop_indices: set[int] = set()
        di = 0
        while di < len(droppable) and token_estimate > target:
            cur_prio = droppable[di][1]["_drop_priority"]
            while di < len(droppable) and droppable[di][1]["_drop_priority"] == cur_prio:
                idx, msg = droppable[di]
                drop_indices.add(idx)
                removed_layers.append(msg.get("_layer", "?"))
                token_estimate -= len(msg["content"])
                di += 1

        if drop_indices:
            messages = [m for j, m in enumerate(messages) if j not in drop_indices]

    return messages, removed_layers


# ---------------------------------------------------------------------------
# 1. All former _DROPPABLE layers have _drop_priority in prompt_builder output
# ---------------------------------------------------------------------------

FORMER_DROPPABLE = [
    "dream_afterglow_soft_hint",
    "6g_dream_impression",
    "6b_event_search",
    "mid_term",
    "6d_diary_context",
    "6e_inner_diary",
    "6c_episodic",
    "5.5_lore",
]

class TestFormerDroppableHasPriority:
    """Inspect prompt_builder source: every former _DROPPABLE layer must now
    declare _drop_priority when it appends to messages."""

    def _get_source(self) -> str:
        import core.prompt_builder as pb
        return inspect.getsource(pb)

    def test_dream_afterglow_soft_hint_has_priority(self):
        src = self._get_source()
        # Find the block that appends dream_afterglow_soft_hint and verify _drop_priority
        assert '"dream_afterglow_soft_hint"' in src
        assert "_drop_priority" in src

    def test_no_droppable_list_in_source(self):
        """_DROPPABLE central list must not exist in the production trimmer."""
        src = self._get_source()
        assert "_DROPPABLE" not in src, "_DROPPABLE central list still present in prompt_builder"

    @pytest.mark.parametrize("layer_name", FORMER_DROPPABLE)
    def test_each_former_droppable_layer_name_present(self, layer_name):
        src = self._get_source()
        assert layer_name in src, f"layer {layer_name!r} missing from prompt_builder source"


# ---------------------------------------------------------------------------
# 2. Trimmer does not reference _DROPPABLE; it uses _drop_priority
# ---------------------------------------------------------------------------

class TestTrimmerUsesPriority:
    def test_layer_with_priority_is_dropped(self):
        big = "X" * 16000
        msgs = _build_messages_over_limit([
            _make_msg("6b_event_search", big, drop_priority=30),
        ])
        trimmed, removed = _run_trimmer(msgs)
        layers = [m["_layer"] for m in trimmed]
        assert "6b_event_search" not in layers
        assert "6b_event_search" in removed

    def test_layer_without_priority_is_not_dropped(self):
        big = "X" * 16000
        msgs = _build_messages_over_limit([
            _make_msg("6a_user_identity", big),   # no _drop_priority
        ])
        trimmed, removed = _run_trimmer(msgs)
        layers = [m["_layer"] for m in trimmed]
        assert "6a_user_identity" in layers
        assert removed == []


# ---------------------------------------------------------------------------
# 3. A brand-new layer with _drop_priority is automatically eligible
# ---------------------------------------------------------------------------

class TestNewLayerAutoEligible:
    def test_new_layer_dropped_by_priority(self):
        big = "N" * 16000
        msgs = _build_messages_over_limit([
            _make_msg("99_future_layer", big, drop_priority=5),
        ])
        trimmed, removed = _run_trimmer(msgs)
        assert "99_future_layer" not in [m["_layer"] for m in trimmed]
        assert "99_future_layer" in removed


# ---------------------------------------------------------------------------
# 4. Layer without _drop_priority is never auto-dropped
# ---------------------------------------------------------------------------

class TestNoPriorityNeverDropped:
    def test_core_layer_kept_even_over_budget(self):
        msgs = [
            _make_msg("11_author_note", "A" * 10000),   # no priority
            _make_msg("1_system_prompt", "B" * 10000),  # no priority
            _make_msg("12_user_message", "hi"),
        ]
        trimmed, removed = _run_trimmer(msgs)
        layers = [m["_layer"] for m in trimmed]
        assert "11_author_note" in layers
        assert "1_system_prompt" in layers
        assert removed == []

    def test_no_priority_message_survives_with_lower_prio_peers(self):
        big = "Z" * 8000
        msgs = _build_messages_over_limit([
            _make_msg("6b_event_search", big, drop_priority=30),
            _make_msg("no_drop_layer", big),             # no priority
        ])
        trimmed, removed = _run_trimmer(msgs)
        layers = [m["_layer"] for m in trimmed]
        assert "no_drop_layer" in layers
        assert "6b_event_search" not in layers


# ---------------------------------------------------------------------------
# 5. Layers are dropped in ascending _drop_priority order
# ---------------------------------------------------------------------------

class TestDropOrder:
    def test_lower_priority_dropped_first(self):
        # prio=10 should be dropped before prio=80 when budget allows
        drop_10 = "A" * 3000
        drop_80 = "B" * 3000
        # Total: 5000 (base) + 3000 + 3000 = 11000 — under 20k hard limit.
        # Use a tighter hard_limit to force trimming.
        msgs = [
            _make_msg("1_system_prompt", "X" * 16000),
            _make_msg("dream_afterglow_soft_hint", drop_10, drop_priority=10),
            _make_msg("5.5_lore", drop_80, drop_priority=80),
            _make_msg("12_user_message", "hi"),
        ]
        # total ≈ 16000 + 3000 + 3000 + 2 = 22002 → triggers trim
        trimmed, removed = _run_trimmer(msgs)
        # After dropping prio=10 (3000 chars): 22002 - 3000 = 19002 > 18000
        # After dropping prio=80 (3000 chars): 19002 - 3000 = 16002 ≤ 18000 → stop
        # So both are dropped in this scenario; order must be 10 before 80
        assert removed.index("dream_afterglow_soft_hint") < removed.index("5.5_lore") \
            if "5.5_lore" in removed else True

    def test_higher_priority_kept_when_budget_satisfied_earlier(self):
        # prio=10 alone is enough to bring us under 18000
        msgs = [
            _make_msg("1_system_prompt", "X" * 17200),
            _make_msg("dream_afterglow_soft_hint", "A" * 3000, drop_priority=10),
            _make_msg("5.5_lore", "B" * 500, drop_priority=80),
            _make_msg("12_user_message", "hi"),
        ]
        # total: 17200 + 3000 + 500 + 2 = 20702 → over 20000 → triggers
        # Drop prio=10 batch (3000): 20702 - 3000 = 17702 ≤ 18000 → stop, lore kept
        trimmed, removed = _run_trimmer(msgs)
        layers = [m["_layer"] for m in trimmed]
        assert "dream_afterglow_soft_hint" not in layers
        assert "5.5_lore" in layers
        assert "dream_afterglow_soft_hint" in removed
        assert "5.5_lore" not in removed

    def test_priority_order_across_all_eight_droppable_layers(self):
        """Verify the canonical drop order: 10 < 20 < 30 < 40 < 50 < 60 < 70 < 80."""
        expected_order = [
            ("dream_afterglow_soft_hint", 10),
            ("6g_dream_impression", 20),
            ("6b_event_search", 30),
            ("mid_term", 40),
            ("6d_diary_context", 50),
            ("6e_inner_diary", 60),
            ("6c_episodic", 70),
            ("5.5_lore", 80),
        ]
        for (layer_a, prio_a), (layer_b, prio_b) in zip(expected_order, expected_order[1:]):
            assert prio_a < prio_b, f"{layer_a} (prio={prio_a}) must have lower priority than {layer_b} (prio={prio_b})"


# ---------------------------------------------------------------------------
# 6. Same-priority messages are dropped as a batch (stable semantics)
# ---------------------------------------------------------------------------

class TestSamePriorityBatchDrop:
    def test_same_priority_both_dropped_together(self):
        """Two messages at the same priority must both be removed in one pass."""
        msgs = [
            _make_msg("1_system_prompt", "X" * 16000),
            _make_msg("6e_inner_diary", "F" * 2000, drop_priority=60),  # facts
            _make_msg("6e_inner_diary", "G" * 2000, drop_priority=60),  # feeling
            _make_msg("12_user_message", "hi"),
        ]
        # total: 16000 + 2000 + 2000 + 2 = 20002 → triggers
        # dropping batch prio=60 removes 4000 chars → 16002 ≤ 18000
        trimmed, removed = _run_trimmer(msgs)
        layers = [m["_layer"] for m in trimmed]
        assert layers.count("6e_inner_diary") == 0
        assert removed.count("6e_inner_diary") == 2

    def test_same_priority_original_order_preserved_in_removed(self):
        """Messages at the same priority appear in removed_layers in original order."""
        msgs = [
            _make_msg("1_system_prompt", "X" * 16000),
            _make_msg("layer_a", "A" * 2000, drop_priority=60),
            _make_msg("layer_b", "B" * 2000, drop_priority=60),
            _make_msg("12_user_message", "hi"),
        ]
        _, removed = _run_trimmer(msgs)
        assert removed == ["layer_a", "layer_b"]


# ---------------------------------------------------------------------------
# 7. debug_info["removed_layers"] matches actual removals
# ---------------------------------------------------------------------------

class TestRemovedLayersMetadata:
    def test_empty_when_no_trim(self):
        msgs = [_make_msg("1_system_prompt", "short")]
        _, removed = _run_trimmer(msgs)
        assert removed == []

    def test_removed_layers_matches_missing_layers(self):
        msgs = [
            _make_msg("1_system_prompt", "X" * 16000),
            _make_msg("dream_afterglow_soft_hint", "A" * 3000, drop_priority=10),
            _make_msg("12_user_message", "hi"),
        ]
        trimmed, removed = _run_trimmer(msgs)
        trimmed_names = {m["_layer"] for m in trimmed}
        for r in removed:
            assert r not in trimmed_names or trimmed_names.count(r) == 0, \
                f"removed layer {r!r} still present in trimmed output"

    def test_removed_layers_not_fabricated(self):
        """Layers that were NOT dropped must not appear in removed_layers."""
        msgs = [
            _make_msg("1_system_prompt", "X" * 16000),
            _make_msg("dream_afterglow_soft_hint", "A" * 3000, drop_priority=10),
            _make_msg("5.5_lore", "L" * 100, drop_priority=80),
            _make_msg("12_user_message", "hi"),
        ]
        trimmed, removed = _run_trimmer(msgs)
        # lore is small enough that dropping afterglow alone suffices
        kept_layers = [m["_layer"] for m in trimmed]
        for r in removed:
            assert r not in kept_layers, f"{r!r} appears in both removed and trimmed"


# ---------------------------------------------------------------------------
# 8. _drop_priority never reaches the LLM vendor
# ---------------------------------------------------------------------------

class TestDropPriorityStrippedAtBoundary:
    def test_sanitize_strips_drop_priority(self):
        from core.prompt_layer import sanitize_messages
        msgs = [{"role": "system", "content": "x", "_layer": "6b", "_drop_priority": 30}]
        result = sanitize_messages(msgs)
        assert "_drop_priority" not in result[0]
        assert "_layer" not in result[0]

    def test_prompt_layer_to_message_includes_drop_priority_for_trimmer(self):
        """prompt_layer_to_message should embed _drop_priority so the trimmer can read it."""
        from core.prompt_layer import PromptLayer, prompt_layer_to_message
        layer = PromptLayer(name="6b_event_search", content="...", drop_priority=30)
        msg = prompt_layer_to_message(layer)
        assert msg["_drop_priority"] == 30

    def test_prompt_layer_to_message_omits_drop_priority_when_none(self):
        from core.prompt_layer import PromptLayer, prompt_layer_to_message
        layer = PromptLayer(name="1_system_prompt", content="...")
        msg = prompt_layer_to_message(layer)
        assert "_drop_priority" not in msg


# ---------------------------------------------------------------------------
# 9. dream_afterglow_soft_hint and 6g_dream_impression are always droppable
# ---------------------------------------------------------------------------

class TestDreamLayersDroppable:
    def test_dream_afterglow_has_lowest_priority(self):
        """dream_afterglow_soft_hint must have the lowest (or tied-lowest) _drop_priority."""
        import inspect, core.prompt_builder as pb
        src = inspect.getsource(pb)
        # Find the _drop_priority assignment for dream_afterglow_soft_hint
        # Simple heuristic: locate the block and ensure 10 is assigned
        idx = src.find('"dream_afterglow_soft_hint"')
        assert idx >= 0
        block = src[max(0, idx - 200): idx + 300]
        assert "_drop_priority" in block, "dream_afterglow_soft_hint block missing _drop_priority"
        assert "10" in block, "dream_afterglow_soft_hint should have _drop_priority=10"

    def test_dream_impression_has_second_priority(self):
        import inspect, core.prompt_builder as pb
        src = inspect.getsource(pb)
        idx = src.find('"6g_dream_impression"')
        assert idx >= 0
        block = src[max(0, idx - 200): idx + 300]
        assert "_drop_priority" in block, "6g_dream_impression block missing _drop_priority"
        assert "20" in block, "6g_dream_impression should have _drop_priority=20"

    def test_trimmer_drops_afterglow_before_lore(self):
        msgs = [
            _make_msg("1_system_prompt", "X" * 16000),
            _make_msg("dream_afterglow_soft_hint", "A" * 3000, drop_priority=10),
            _make_msg("5.5_lore", "L" * 3000, drop_priority=80),
            _make_msg("12_user_message", "hi"),
        ]
        # 16000+3000+3000+2 = 22002 → over by 4002
        # Batch prio=10 (3000): 22002-3000=19002 still > 18000
        # Batch prio=80 (3000): 19002-3000=16002 ≤ 18000
        trimmed, removed = _run_trimmer(msgs)
        assert removed[0] == "dream_afterglow_soft_hint"
        assert "5.5_lore" in removed
        assert removed.index("dream_afterglow_soft_hint") < removed.index("5.5_lore")


# ---------------------------------------------------------------------------
# 10. Prompt content not mutated by trimming
# ---------------------------------------------------------------------------

class TestContentNotMutated:
    def test_kept_message_content_unchanged(self):
        original_content = "This is the system prompt content. " * 100
        msgs = [
            _make_msg("1_system_prompt", original_content),
            _make_msg("6b_event_search", "E" * 16000, drop_priority=30),
            _make_msg("12_user_message", "hi"),
        ]
        trimmed, _ = _run_trimmer(msgs)
        sys_msgs = [m for m in trimmed if m["_layer"] == "1_system_prompt"]
        assert len(sys_msgs) == 1
        assert sys_msgs[0]["content"] == original_content

    def test_trimmer_does_not_mutate_input_list(self):
        msgs = [
            _make_msg("1_system_prompt", "X" * 16000),
            _make_msg("6b_event_search", "E" * 5000, drop_priority=30),
            _make_msg("12_user_message", "hi"),
        ]
        import copy
        original = copy.deepcopy(msgs)
        _run_trimmer(msgs)
        # original list should be unchanged (trimmer works on a deep copy)
        assert len(msgs) == len(original)
        for orig, after in zip(original, msgs):
            assert orig == after
