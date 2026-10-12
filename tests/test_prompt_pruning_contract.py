"""
tests/test_prompt_pruning_contract.py — Brief 50 · 工单G.2

剪枝顺序的权威测试文件，合并自：
  - test_r4b_prompt_drop_priority.py（R4-B，行为级：实际跑裁剪逻辑）
  - test_r4c_prompt_layer_contract.py（R4-C，契约级：正则扫描 prompt_builder 源码）
两个旧文件已删除。

**剪枝顺序契约**（_drop_priority 越小越先裁，数值越大越后裁）：

    dream_afterglow_soft_hint(10) < 6g_dream_impression(20) < event_search(30)
    < mid_term(40) < diary_context(50) < inner_diary(60) < episodic(70) < lore(80)

即 Brief 50 简述的 `event_search → mid_term → diary → episodic → lore` 是这张
完整表里 6b_event_search/mid_term/6d_diary_context/6c_episodic/5.5_lore 五层的
顺序摘要；dream_afterglow_soft_hint 和 6g_dream_impression 优先级更低（更早被
裁），6e_inner_diary 插在 diary_context 和 episodic 之间，完整顺序以本文件
DROPPABLE_LAYER_PRIORITIES 为准。

去重说明：r4b 中与 r4c 重复/被 r4c 更严格覆盖的部分未合并——
  - r4b TestFormerDroppableHasPriority → 被 r4c Rule1（_DROPPABLE 不存在）+
    Rule2（8 层优先级 parametrize 精确核对）覆盖，且 r4c 用正则精确提取
    _drop_priority 数值，比 r4b 的子串匹配更严谨。
  - r4b TestDropPriorityStrippedAtBoundary → 被 r4c Rule6
    TestPromptLayerToMessageContract 完全覆盖且更全（多测了 mutate/int 类型等）。
  - r4b TestDreamLayersDroppable 的前两个源码字符串断言 → 被 r4c Rule2 parametrize
    覆盖；行为级的 test_trimmer_drops_afterglow_before_lore 保留。
"""
from __future__ import annotations

import inspect
import re

import pytest

# ═══════════════════════════════════════════════════════════════════════════════
# 共享常量（来自 r4c）
# ═══════════════════════════════════════════════════════════════════════════════

# All 8 currently declared droppable layers and their expected _drop_priority values.
# "Lower value = dropped first" — do not change a priority without updating this table.
DROPPABLE_LAYER_PRIORITIES: dict[str, int] = {
    "dream_afterglow_soft_hint": 10,
    "6g_dream_impression":       20,
    "6b_event_search":           30,
    "mid_term":                  40,
    "6d_diary_context":          50,
    "6e_inner_diary":            60,
    "6c_episodic":               70,
    "5.5_lore":                  80,
}

# Keywords that signal "this layer is likely droppable".
DROPPABLE_KEYWORDS: frozenset[str] = frozenset({
    "dream",
    "diary",
    "episodic",
    "event",
    "lore",
    "afterglow",
    "impression",
    "mid_term",
})

# Layers that match a keyword but are intentionally NOT auto-droppable.
NON_DROPPABLE_ALLOWLIST: dict[str, str] = {
    "9.5_episodic_top": (
        "Single high-value long/repair recall retained in the memory section. "
        "Small footprint (one line); no meaningful token saving from dropping it."
    ),
}


# ═══════════════════════════════════════════════════════════════════════════════
# 源码扫描 helpers（来自 r4c）
# ═══════════════════════════════════════════════════════════════════════════════

def _get_pb_source() -> str:
    import core.prompt_builder as pb
    return inspect.getsource(pb)


def _extract_layers_from_source(src: str) -> list[tuple[str, int | None]]:
    """
    Scan prompt_builder source for messages.append({…}) blocks that declare _layer.
    For each such block return (layer_name, drop_priority | None).
    """
    layer_re = re.compile(r'"_layer":\s*"([^"]+)"')
    prio_re = re.compile(r'"_drop_priority":\s*(\d+)')

    blocks = src.split("messages.append(")
    results: list[tuple[str, int | None]] = []
    for block in blocks[1:]:
        window = block[:700]
        lm = layer_re.search(window)
        if not lm:
            continue
        layer_name = lm.group(1)
        pm = prio_re.search(window)
        priority = int(pm.group(1)) if pm else None
        results.append((layer_name, priority))
    return results


def _layer_priority_map(src: str) -> dict[str, int | None]:
    """Deduplicated {layer_name: drop_priority} from source. First occurrence wins."""
    result: dict[str, int | None] = {}
    for name, prio in _extract_layers_from_source(src):
        if name not in result:
            result[name] = prio
    return result


def _matches_keyword(layer_name: str) -> bool:
    low = layer_name.lower()
    return any(kw in low for kw in DROPPABLE_KEYWORDS)


# ═══════════════════════════════════════════════════════════════════════════════
# 行为级 helpers（来自 r4b）：镜像 prompt_builder.build() 里的裁剪逻辑
# ═══════════════════════════════════════════════════════════════════════════════

def _make_msg(layer: str, content: str, drop_priority: int | None = None) -> dict:
    msg: dict = {"role": "system", "content": content, "_layer": layer}
    if drop_priority is not None:
        msg["_drop_priority"] = drop_priority
    return msg


def _build_messages_over_limit(extra: list[dict]) -> list[dict]:
    base = [
        _make_msg("1_system_prompt", "A" * 5000),
        _make_msg("12_user_message", "hello"),
    ]
    return base + extra


def _run_trimmer(messages: list[dict], hard_limit: int = 20000, target: int = 18000):
    """Run the R4-B dynamic trimmer on a message list.

    Uses a compact fixture budget for priority tests; production's 30k/28k
    boundary is exercised through build() in test_prompt_evidence_refs.

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


# ═══════════════════════════════════════════════════════════════════════════════
# Rule 1（r4c）— No _DROPPABLE central list in core/prompt_builder.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoDROPPABLEInSource:
    """_DROPPABLE was retired in R4-B; must never be re-introduced in production."""

    def test_droppable_constant_absent_from_prompt_builder(self):
        src = _get_pb_source()
        assert "_DROPPABLE" not in src, (
            "_DROPPABLE central list was re-introduced in core/prompt_builder.py. "
            "R4-B retired it; all trimming must use per-message _drop_priority."
        )

    def test_no_droppable_assignment_in_prompt_builder(self):
        src = _get_pb_source()
        matches = re.findall(r'\b_DROPPABLE\b', src)
        assert not matches, (
            f"Found {len(matches)} occurrence(s) of _DROPPABLE in prompt_builder source."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Rule 2（r4c）— All 8 known droppable layers declare _drop_priority
# ═══════════════════════════════════════════════════════════════════════════════

class TestKnownDroppableLayersHavePriority:
    def _get_lpm(self) -> dict[str, int | None]:
        return _layer_priority_map(_get_pb_source())

    @pytest.mark.parametrize("layer,expected_prio", DROPPABLE_LAYER_PRIORITIES.items())
    def test_layer_has_expected_priority(self, layer: str, expected_prio: int):
        lpm = self._get_lpm()
        assert layer in lpm, (
            f"Layer {layer!r} not found in any messages.append() block "
            "in core/prompt_builder.py."
        )
        actual = lpm[layer]
        assert actual == expected_prio, (
            f"Layer {layer!r}: expected _drop_priority={expected_prio}, got {actual!r}. "
            "Update DROPPABLE_LAYER_PRIORITIES in this test file if the priority was "
            "intentionally changed."
        )

    def test_complete_droppable_set_size(self):
        """Sentinel: DROPPABLE_LAYER_PRIORITIES must list exactly 8 layers."""
        assert len(DROPPABLE_LAYER_PRIORITIES) == 8, (
            "DROPPABLE_LAYER_PRIORITIES has unexpected size. "
            "Update it when adding or removing a droppable layer."
        )

    def test_all_8_layers_found_in_source(self):
        lpm = self._get_lpm()
        missing = [l for l in DROPPABLE_LAYER_PRIORITIES if l not in lpm]
        assert not missing, f"Layers missing from source: {missing}"


# ═══════════════════════════════════════════════════════════════════════════════
# Rule 3（r4c）— Keyword gate for future layers
# ═══════════════════════════════════════════════════════════════════════════════

class TestKeywordGate:
    """
    Any _layer whose name matches a DROPPABLE_KEYWORDS entry must either:
      (a) carry _drop_priority in the same append block, or
      (b) appear in NON_DROPPABLE_ALLOWLIST with a justification.
    """

    def _keyword_layers(self) -> list[tuple[str, int | None]]:
        src = _get_pb_source()
        seen: set[str] = set()
        result = []
        for name, prio in _extract_layers_from_source(src):
            if _matches_keyword(name) and name not in seen:
                seen.add(name)
                result.append((name, prio))
        return result

    def test_all_keyword_layers_have_priority_or_allowlist(self):
        violations: list[str] = []
        for name, prio in self._keyword_layers():
            if prio is None and name not in NON_DROPPABLE_ALLOWLIST:
                violations.append(name)
        assert not violations, (
            "The following layers match droppable keywords but declare neither "
            "_drop_priority nor a NON_DROPPABLE_ALLOWLIST entry:\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\n\nFix: either add `\"_drop_priority\": N` to the messages.append() "
            "call, or add the layer to NON_DROPPABLE_ALLOWLIST in this test file "
            "with a justification string."
        )

    def test_allowlist_entries_have_non_trivial_justification(self):
        for name, reason in NON_DROPPABLE_ALLOWLIST.items():
            assert isinstance(reason, str) and len(reason.strip()) >= 20, (
                f"NON_DROPPABLE_ALLOWLIST[{name!r}] justification is too short. "
                "Write at least 20 characters explaining why it is not auto-droppable."
            )

    def test_allowlist_entries_actually_match_keyword(self):
        for name in NON_DROPPABLE_ALLOWLIST:
            assert _matches_keyword(name), (
                f"NON_DROPPABLE_ALLOWLIST[{name!r}] does not match any keyword in "
                f"DROPPABLE_KEYWORDS {sorted(DROPPABLE_KEYWORDS)}. "
                "Remove it — this allowlist is only for keyword-matching layers."
            )

    def test_no_layer_in_both_droppable_and_allowlist(self):
        overlap = set(DROPPABLE_LAYER_PRIORITIES) & set(NON_DROPPABLE_ALLOWLIST)
        assert not overlap, (
            f"Layers appear in both DROPPABLE_LAYER_PRIORITIES and "
            f"NON_DROPPABLE_ALLOWLIST: {overlap}. "
            "A layer cannot be both auto-droppable and exempted."
        )

    @pytest.mark.parametrize("layer", list(DROPPABLE_LAYER_PRIORITIES.keys()))
    def test_known_droppable_layer_matches_keyword(self, layer: str):
        assert _matches_keyword(layer), (
            f"Layer {layer!r} is in DROPPABLE_LAYER_PRIORITIES but does NOT match "
            f"any keyword in DROPPABLE_KEYWORDS {sorted(DROPPABLE_KEYWORDS)}. "
            "Either add a keyword that covers it, or reconsider its classification."
        )

    def test_keyword_scanner_finds_all_known_droppable_layers(self):
        src = _get_pb_source()
        lpm = _layer_priority_map(src)
        missing_or_no_prio = [
            layer for layer in DROPPABLE_LAYER_PRIORITIES
            if lpm.get(layer) != DROPPABLE_LAYER_PRIORITIES[layer]
        ]
        assert not missing_or_no_prio, (
            f"Keyword scanner failed to confirm priority for: {missing_or_no_prio}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Rule 4（r4c）— _drop_priority must be int (not a string)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDropPriorityType:
    def test_no_string_drop_priority_in_source(self):
        src = _get_pb_source()
        bad = re.findall(r'"_drop_priority":\s*"[^"]*"', src)
        assert not bad, (
            f"_drop_priority assigned as a string literal in prompt_builder: {bad}. "
            "Use integer literals only (e.g. `\"_drop_priority\": 30`)."
        )

    def test_all_declared_priorities_are_positive_ints(self):
        src = _get_pb_source()
        for name, prio in _extract_layers_from_source(src):
            if prio is not None:
                assert isinstance(prio, int), (
                    f"Layer {name!r}: parsed _drop_priority={prio!r} is not int"
                )
                assert prio > 0, (
                    f"Layer {name!r}: _drop_priority={prio!r} must be > 0"
                )

    def test_promptlayer_accepts_int_drop_priority(self):
        from core.prompt_layer import PromptLayer
        layer = PromptLayer(name="6c_episodic", content="x", drop_priority=70)
        assert isinstance(layer.drop_priority, int)
        assert layer.drop_priority == 70

    def test_promptlayer_accepts_none_drop_priority(self):
        from core.prompt_layer import PromptLayer
        layer = PromptLayer(name="1_system_prompt", content="x")
        assert layer.drop_priority is None

    def test_prompt_layer_to_message_embeds_int(self):
        from core.prompt_layer import PromptLayer, prompt_layer_to_message
        layer = PromptLayer(name="6b_event_search", content="x", drop_priority=30)
        msg = prompt_layer_to_message(layer)
        assert isinstance(msg["_drop_priority"], int)
        assert msg["_drop_priority"] == 30


# ═══════════════════════════════════════════════════════════════════════════════
# Rule 5（r4c）— Duplicate _drop_priority values are explicitly permitted
# ═══════════════════════════════════════════════════════════════════════════════

class TestSamePriorityBatchSemantics:
    """R4-B: same-priority messages are dropped as an atomic batch."""

    def test_6e_inner_diary_shares_priority_60(self):
        src = _get_pb_source()
        all_pairs = _extract_layers_from_source(src)
        diary_entries = [(n, p) for n, p in all_pairs if n == "6e_inner_diary"]
        assert len(diary_entries) >= 2, (
            "Expected at least 2 append blocks for '6e_inner_diary' (facts + feeling); "
            f"found {len(diary_entries)}."
        )
        for name, prio in diary_entries:
            assert prio == 60, (
                f"6e_inner_diary entry has _drop_priority={prio!r}, expected 60."
            )

    def test_promptlayer_allows_shared_priority(self):
        from core.prompt_layer import PromptLayer
        a = PromptLayer(name="layer_a", content="x", drop_priority=60)
        b = PromptLayer(name="layer_b", content="y", drop_priority=60)
        assert a.drop_priority == b.drop_priority == 60

    def test_priority_not_globally_unique_across_known_layers(self):
        """The trimmer does NOT enforce priority uniqueness — only ordering matters."""
        msgs = [
            {"role": "system", "content": "A" * 16000, "_layer": "base"},
            {"role": "system", "content": "B" * 2000, "_layer": "layer_x", "_drop_priority": 55},
            {"role": "system", "content": "C" * 2000, "_layer": "layer_y", "_drop_priority": 55},
            {"role": "system", "content": "hi", "_layer": "user"},
        ]
        trimmed, removed = _run_trimmer(msgs)
        trimmed_names = {m["_layer"] for m in trimmed}
        assert "layer_x" not in trimmed_names
        assert "layer_y" not in trimmed_names


# ═══════════════════════════════════════════════════════════════════════════════
# Rule 6（r4c）— PromptLayer→message contract
# ═══════════════════════════════════════════════════════════════════════════════

class TestPromptLayerToMessageContract:
    """Verifies the stable PromptLayer→message dict conversion contract."""

    def test_none_drop_priority_omits_field(self):
        from core.prompt_layer import PromptLayer, prompt_layer_to_message
        layer = PromptLayer(name="1_system_prompt", content="x")
        msg = prompt_layer_to_message(layer)
        assert "_drop_priority" not in msg

    def test_int_drop_priority_embeds_field(self):
        from core.prompt_layer import PromptLayer, prompt_layer_to_message
        layer = PromptLayer(name="6c_episodic", content="x", drop_priority=70)
        msg = prompt_layer_to_message(layer)
        assert "_drop_priority" in msg
        assert msg["_drop_priority"] == 70

    def test_name_written_as_layer_key(self):
        from core.prompt_layer import PromptLayer, prompt_layer_to_message
        layer = PromptLayer(name="mid_term", content="x")
        msg = prompt_layer_to_message(layer)
        assert msg["_layer"] == "mid_term"

    def test_role_written_correctly(self):
        from core.prompt_layer import PromptLayer, prompt_layer_to_message
        layer = PromptLayer(name="x", content="y", role="user")
        msg = prompt_layer_to_message(layer)
        assert msg["role"] == "user"

    def test_content_preserved(self):
        from core.prompt_layer import PromptLayer, prompt_layer_to_message
        layer = PromptLayer(name="x", content="hello world")
        msg = prompt_layer_to_message(layer)
        assert msg["content"] == "hello world"

    def test_sanitize_strips_drop_priority_and_layer(self):
        from core.prompt_layer import sanitize_messages
        msg = {"role": "system", "content": "x", "_layer": "6b", "_drop_priority": 30}
        result = sanitize_messages([msg])
        assert "_drop_priority" not in result[0]
        assert "_layer" not in result[0]
        assert result[0]["content"] == "x"

    def test_sanitize_does_not_mutate_originals(self):
        from core.prompt_layer import sanitize_messages
        original = {"role": "system", "content": "x", "_layer": "test", "_drop_priority": 5}
        sanitize_messages([original])
        assert "_layer" in original, "sanitize_messages mutated the original dict"
        assert "_drop_priority" in original

    def test_non_droppable_layer_message_has_no_priority(self):
        from core.prompt_layer import PromptLayer, prompt_layer_to_message
        layer = PromptLayer(name="11_author_note", content="rules")
        msg = prompt_layer_to_message(layer)
        assert "_drop_priority" not in msg
        assert msg["_layer"] == "11_author_note"

    def test_default_role_is_system(self):
        from core.prompt_layer import PromptLayer, prompt_layer_to_message
        layer = PromptLayer(name="x", content="y")
        msg = prompt_layer_to_message(layer)
        assert msg["role"] == "system"


# ═══════════════════════════════════════════════════════════════════════════════
# 行为级 · Trimmer 使用 _drop_priority 而非 _DROPPABLE（来自 r4b）
# ═══════════════════════════════════════════════════════════════════════════════

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
            _make_msg("6a_user_identity", big),
        ])
        trimmed, removed = _run_trimmer(msgs)
        layers = [m["_layer"] for m in trimmed]
        assert "6a_user_identity" in layers
        assert removed == []


class TestNewLayerAutoEligible:
    def test_new_layer_dropped_by_priority(self):
        big = "N" * 16000
        msgs = _build_messages_over_limit([
            _make_msg("99_future_layer", big, drop_priority=5),
        ])
        trimmed, removed = _run_trimmer(msgs)
        assert "99_future_layer" not in [m["_layer"] for m in trimmed]
        assert "99_future_layer" in removed


class TestNoPriorityNeverDropped:
    def test_core_layer_kept_even_over_budget(self):
        msgs = [
            _make_msg("11_author_note", "A" * 10000),
            _make_msg("1_system_prompt", "B" * 10000),
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
            _make_msg("no_drop_layer", big),
        ])
        trimmed, removed = _run_trimmer(msgs)
        layers = [m["_layer"] for m in trimmed]
        assert "no_drop_layer" in layers
        assert "6b_event_search" not in layers


# ═══════════════════════════════════════════════════════════════════════════════
# 行为级 · 剪枝顺序（来自 r4b；Brief 50 明确要求的核心契约）
# ═══════════════════════════════════════════════════════════════════════════════

class TestDropOrder:
    def test_lower_priority_dropped_first(self):
        drop_10 = "A" * 3000
        drop_80 = "B" * 3000
        msgs = [
            _make_msg("1_system_prompt", "X" * 16000),
            _make_msg("dream_afterglow_soft_hint", drop_10, drop_priority=10),
            _make_msg("5.5_lore", drop_80, drop_priority=80),
            _make_msg("12_user_message", "hi"),
        ]
        trimmed, removed = _run_trimmer(msgs)
        assert "dream_afterglow_soft_hint" in removed
        assert "5.5_lore" in removed
        assert removed.index("dream_afterglow_soft_hint") < removed.index("5.5_lore")

    def test_higher_priority_kept_when_budget_satisfied_earlier(self):
        msgs = [
            _make_msg("1_system_prompt", "X" * 17200),
            _make_msg("dream_afterglow_soft_hint", "A" * 3000, drop_priority=10),
            _make_msg("5.5_lore", "B" * 500, drop_priority=80),
            _make_msg("12_user_message", "hi"),
        ]
        trimmed, removed = _run_trimmer(msgs)
        layers = [m["_layer"] for m in trimmed]
        assert "dream_afterglow_soft_hint" not in layers
        assert "5.5_lore" in layers
        assert "dream_afterglow_soft_hint" in removed
        assert "5.5_lore" not in removed

    def test_priority_order_across_all_eight_droppable_layers(self):
        """权威顺序断言：10 < 20 < 30 < 40 < 50 < 60 < 70 < 80，即
        dream_afterglow_soft_hint < 6g_dream_impression < event_search < mid_term
        < diary_context < inner_diary < episodic < lore。"""
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

    def test_trimmer_drops_afterglow_before_lore(self):
        """来自 r4b TestDreamLayersDroppable：dream_afterglow_soft_hint 实际先于 lore 被裁。"""
        msgs = [
            _make_msg("1_system_prompt", "X" * 16000),
            _make_msg("dream_afterglow_soft_hint", "A" * 3000, drop_priority=10),
            _make_msg("5.5_lore", "L" * 3000, drop_priority=80),
            _make_msg("12_user_message", "hi"),
        ]
        trimmed, removed = _run_trimmer(msgs)
        assert removed[0] == "dream_afterglow_soft_hint"
        assert "5.5_lore" in removed
        assert removed.index("dream_afterglow_soft_hint") < removed.index("5.5_lore")

    def test_event_search_to_lore_order(self):
        """Brief 50 简述顺序的直接断言：event_search → mid_term → diary → episodic → lore。"""
        msgs = [
            _make_msg("1_system_prompt", "X" * 15000),
            _make_msg("6b_event_search", "E" * 1000, drop_priority=30),
            _make_msg("mid_term", "M" * 1000, drop_priority=40),
            _make_msg("6d_diary_context", "D" * 1000, drop_priority=50),
            _make_msg("6c_episodic", "P" * 1000, drop_priority=70),
            _make_msg("5.5_lore", "L" * 1000, drop_priority=80),
            _make_msg("12_user_message", "hi"),
        ]
        # total = 15000+1000*5+2 = 20002 > 20000 → 触发；target=18000 需要裁掉至少 2001
        trimmed, removed = _run_trimmer(msgs)
        expected_prefix = ["6b_event_search", "mid_term", "6d_diary_context", "6c_episodic", "5.5_lore"]
        assert removed == expected_prefix[: len(removed)], (
            f"drop order mismatch: got {removed}, expected a prefix of {expected_prefix}"
        )
        assert len(removed) >= 3, "scenario should force dropping at least event_search/mid_term/diary"


# ═══════════════════════════════════════════════════════════════════════════════
# 行为级 · 同优先级批量裁剪（来自 r4b）
# ═══════════════════════════════════════════════════════════════════════════════

class TestSamePriorityBatchDrop:
    def test_same_priority_both_dropped_together(self):
        msgs = [
            _make_msg("1_system_prompt", "X" * 16000),
            _make_msg("6e_inner_diary", "F" * 2000, drop_priority=60),
            _make_msg("6e_inner_diary", "G" * 2000, drop_priority=60),
            _make_msg("12_user_message", "hi"),
        ]
        trimmed, removed = _run_trimmer(msgs)
        layers = [m["_layer"] for m in trimmed]
        assert layers.count("6e_inner_diary") == 0
        assert removed.count("6e_inner_diary") == 2

    def test_same_priority_original_order_preserved_in_removed(self):
        msgs = [
            _make_msg("1_system_prompt", "X" * 16000),
            _make_msg("layer_a", "A" * 2000, drop_priority=60),
            _make_msg("layer_b", "B" * 2000, drop_priority=60),
            _make_msg("12_user_message", "hi"),
        ]
        _, removed = _run_trimmer(msgs)
        assert removed == ["layer_a", "layer_b"]


# ═══════════════════════════════════════════════════════════════════════════════
# 行为级 · removed_layers 元数据准确性（来自 r4b）
# ═══════════════════════════════════════════════════════════════════════════════

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
        msgs = [
            _make_msg("1_system_prompt", "X" * 16000),
            _make_msg("dream_afterglow_soft_hint", "A" * 3000, drop_priority=10),
            _make_msg("5.5_lore", "L" * 100, drop_priority=80),
            _make_msg("12_user_message", "hi"),
        ]
        trimmed, removed = _run_trimmer(msgs)
        kept_layers = [m["_layer"] for m in trimmed]
        for r in removed:
            assert r not in kept_layers, f"{r!r} appears in both removed and trimmed"


# ═══════════════════════════════════════════════════════════════════════════════
# 行为级 · 内容不被裁剪逻辑篡改（来自 r4b）
# ═══════════════════════════════════════════════════════════════════════════════

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
        assert len(msgs) == len(original)
        for orig, after in zip(original, msgs):
            assert orig == after


# ═══════════════════════════════════════════════════════════════════════════════
# 行为级 · 裁完仍超预算的告警路径（来自 r4b，P2-R4B-2）
# ═══════════════════════════════════════════════════════════════════════════════

class TestOverBudgetWarning:
    """Coverage for the 裁完仍超预算 path: all droppable layers removed but still > 18000."""

    def test_budget_remains_exceeded_after_exhausting_all_droppable(self):
        msgs = [
            _make_msg("1_system_prompt", "X" * 19000),
            _make_msg("5.5_lore", "L" * 2000, drop_priority=80),
            _make_msg("12_user_message", "hi"),
        ]
        trimmed, removed = _run_trimmer(msgs)
        remaining = sum(len(m["content"]) for m in trimmed)
        assert "5.5_lore" in removed
        assert remaining > 18000, "scenario must leave budget still exceeded"

    def test_prompt_builder_has_over_budget_warning_in_source(self):
        import core.prompt_builder as pb
        src = inspect.getsource(pb.build)
        assert "裁完仍超预算" in src, (
            "build() must log a warning when all droppable layers are exhausted "
            "but token_estimate still exceeds 18000"
        )
