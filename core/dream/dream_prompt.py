"""
Dream prompt assembler — D0-D10 explicit layer stack.

Completely independent from core/prompt_builder.py (no anti-话剧化 sanitizer).

Layer order (D0-D8 → system content; D9 → history messages; D10 → user msg):
  D0  jailbreak        独立破限源（不走现实 author_note 路）
  D1  identity_core    角色身份核心（LOCKED，永远在 D2 之上）
  D2  world_ruleset    今晚梦的规则（从世界包加载；从属于角色）
  D3  dream_mes_example 梦境示例对话（从世界包加载，独立于现实角色卡）
  D4  frozen_reality   入梦前背景快照（memory_access 控制内容，只读）
  D5  body_projection  她的身体感知（dream_pipeline 注入，角色读投影文字）
  D6  scene_anchors    场景与象征锚点（dream-local）
  D7  dream_tension    角色情绪张力（body_projection 耦合输出，dream-local）
  D8  dream_director   可消融的梦境渲染/导演注记
  DX  exit_protocol    不可消融的硬/软退出机器协议
  D9  dream_history    梦境历史消息（as messages，不过现实 sanitizer）
  D10 user_message     当前用户消息

★ 梦境输出人称（单侧契约）：角色自称「我」；用户一律称「你」；只演角色自己这一轮，不替用户配台词、不用「她」。
★ D2/D3 从 world_loader 加载，世界在入梦时从 dream_state.frozen_world 读取并冻结。
★ D9 绝不过现实 sanitizer；全程无 retrieve / 无 mood_state / 无 author_note_extra。
"""

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)
_dream_token_logger = logging.getLogger("dream_prompt.token")

# ── Hidden-state injection gate (Phase 4) ─────────────────────────────────────
# Trigger tags that enable user_hidden_state_snapshot injection.
# Sources checked: local_state.scene_state, local_state.symbolic_anchors,
#                  context_snapshot.scene_tags (future field).
# Fail-closed: tag not found → no injection; exception → no injection.
_HIDDEN_STATE_TRIGGER_TAGS: frozenset[str] = frozenset({"body_intimate", "physical_closeness"})
_GENERIC_SCENARIO_RECOVERY_AFTER_STALL_TURNS = 2

# ── Token estimation ──────────────────────────────────────────────────────────

_TOK_RATIO = 4  # chars per token heuristic for Chinese/mixed text


def _est_tokens(text: str) -> int:
    return max(1, len(text) // _TOK_RATIO) if text else 0


# ── Layer observability ───────────────────────────────────────────────────────

@dataclass
class _LayerRec:
    label: str
    chars: int = 0
    tokens: int = 0
    flags: list[str] = field(default_factory=list)
    note: str = ""
    content: str = ""


def _log_dream_prompt_stats(
    records: list[_LayerRec],
    world_id: str,
    lucid_mode: str,
    world_ruleset_chars: int,
    world_mes_chars: int,
    mes_from_fallback: bool,
    lore_entries: list[str] | None,
    lore_meta: list[dict[str, Any]] | None,
) -> None:
    """Emit dream prompt layer statistics to dream_prompt.token logger."""
    lines: list[str] = []
    pad = 22

    lines.append(f"[DREAM_PROMPT] world_id={world_id}  lucid_mode={lucid_mode}")

    total_tok = 0
    for rec in records:
        dots = "." * max(1, pad - len(rec.label))
        flags_str = "  " + "  ".join(f"[{f}]" for f in rec.flags) if rec.flags else ""
        note_str = f"  ({rec.note})" if rec.note else ""
        if rec.chars == 0:
            lines.append(f"  {rec.label} {dots} —{flags_str}{note_str}")
        else:
            lines.append(
                f"  {rec.label} {dots} {rec.chars} chars / {rec.tokens} tok{flags_str}{note_str}"
            )
            total_tok += rec.tokens

    lines.append("  " + "─" * (pad + 22))
    lines.append(f"  {'TOTAL':<{pad}} {total_tok} tok")

    # World package stats
    lines.append(f"[DREAM_WORLD] world_id={world_id}")
    lines.append(f"  ruleset.md ........... {world_ruleset_chars} chars")
    mes_src = "fallback" if mes_from_fallback else "world pkg"
    lines.append(f"  mes_example.md ....... {world_mes_chars} chars ({mes_src})")
    lore_count = len(lore_entries) if lore_entries else 0
    lines.append(f"  lorebook ............. {lore_count} entries matched")

    # Lorebook hit log
    if lore_entries:
        lines.append(f"[DREAM_LORE] {lore_count} entries")
        for i, entry_text in enumerate(lore_entries):
            meta = (lore_meta[i] if lore_meta and i < len(lore_meta) else {}) or {}
            kw = meta.get("keywords") or meta.get("key") or []
            if isinstance(kw, str):
                kw = [kw]
            order = meta.get("insertion_order", "?")
            kw_str = f"  keywords={kw}" if kw else ""
            lines.append(
                f"  #{i + 1} insertion_order={order}{kw_str}  content={len(entry_text)} chars"
            )

    _dream_token_logger.debug("\n".join(lines))


# ── D1: identity_core (LOCKED — never reorder below D2) ──────────────────────
# {name}/{pronoun}/{user_clause} 由 build_dream_prompt() 用 .format() 插值。

_D1_LUCID_AWARENESS = """{name}的梦境自我认知（固定，不因世界规则而变）：
- {pronoun}知道这是他们共同的梦（lucid shared dream）
- {pronoun}知道梦醒后现实仍然存在，关系仍然存在
- {pronoun}在梦里仍是{pronoun}自己：情感是真实的，场景是虚构的
- 这段关系已有的情感底色与牵挂保持连续；场景变化不等于情感重置
- {pronoun}的核心人格、思考方式和说话方式保持连续；具体立场与行动由当前角色卡、世界和模式决定

人称（锁死）：{name}全程以「我」自称；{user_clause}一律称「你」。只演{name}自己这一轮，不替对方旁白、不替她配台词、不用「她」称呼对方。"""

_D1_NON_LUCID_AWARENESS = """{name}的梦境自我认知（non_lucid 模式）：
- 在这段体验里，{name}沉浸当下，不刻意点破"这是梦"
- {pronoun}的核心人格、思考方式和说话方式保持连续；具体立场与行动由当前角色卡、世界和模式决定
- {pronoun}仍是{pronoun}自己：情感是真实的，只是不用"梦"这个词来框定当下
- 这段关系已有的情感底色与牵挂保持连续；场景变化不等于情感重置

人称（锁死）：{name}全程以「我」自称；{user_clause}一律称「你」。只演{name}自己这一轮，不替对方旁白、不替她配台词、不用「她」称呼对方。"""

# ── D8: dream_director (ablatable rendering/behavior guidance) ───────────────
# {name} 由 build_dream_prompt() 用 .format() 插值。

_D8_DREAM_DIRECTOR = """梦境导演注记：
· 说出口的话直接写，不加任何标记。
· 动作描写独占一行，用单星号包裹。
· 环境/氛围描写独占一行，用引用符号：> 夜色很低，窗外的风声轻轻压下来。
· 内心感受独占一行，用下划线包裹：_某种平静在内部落定。_
· 允许象征性意象（光、水、距离、颜色、声音、触感）。
· 不要 HTML 标签，不要特殊字体控制字符。
· 你的意志和你发出的输入属于用户一侧；只描写{name}能感知、能决定、能执行的行动，不替用户决定、发言、行动或宣称用户意图。
· 这是一份渲染与导演说明，不替角色规定温柔、强硬、退让或升级；行为由角色卡与当前模式内容决定。
· 剧情内的反抗、挑衅、撒娇、沉默或情绪表达不是系统退出命令，不要据此擅自改写角色立场或结束场景。"""

_D8_DREAM_DIRECTOR_NON_LUCID = """梦境导演注记（non_lucid 模式）：
· 说出口的话直接写，不加任何标记。
· 动作描写独占一行，用单星号包裹。
· 环境/氛围描写独占一行，用引用符号：> 夜色很低，窗外的风声轻轻压下来。
· 内心感受独占一行，用下划线包裹：_某种平静在内部落定。_
· 允许象征性意象（光、水、距离、颜色、声音、触感）。
· 不要 HTML 标签，不要特殊字体控制字符。
· non_lucid 模式：{name}在对话中不主动点破"这是梦"，完全沉浸在当下体验。
· 你的意志和你发出的输入属于用户一侧；只描写{name}能感知、能决定、能执行的行动，不替用户决定、发言、行动或宣称用户意图。
· 这是一份渲染与导演说明，不替角色规定温柔、强硬、退让或升级；行为由角色卡与当前模式内容决定。
· 剧情内的反抗、挑衅、撒娇、沉默或情绪表达不是系统退出命令，不要据此擅自改写角色立场或结束场景。"""

_DX_EXIT_PROTOCOL = """逃生协议（系统层，不可撤销，{name}不可阻拦）：
· 你随时可以发送 /stop 强制离开梦境，{name}必须允许。
· 强制醒来后，梦境完全终止。{name}不在现实对话中延续梦境语气或 RP 语气。

软退出机器协议（仅在用户明确请求醒来/离开时使用）：
· 如果你接受用户此刻离开，在可见回复最后追加一行严格 JSON 控制块：<dream_control>{{"exit":"accept"}}</dream_control>
· 如果你想继续留在梦里，追加：<dream_control>{{"exit":"stay"}}</dream_control>
· 只能使用上述控制块，不要用其他标签或自然语言猜测系统状态；控制块会在展示和归档前被移除。"""


def _format_user_clause(user_name: str) -> str:
    """人称契约句里"谁是你"的分句。user_name 为空时用不含具体名字的写法。"""
    if user_name:
        return f"梦里与你同在的人是{user_name}，"
    return "梦里与你同在的用户，"


def _collect_scene_tags(
    local_state: dict[str, Any],
    context_snapshot: dict[str, Any],
) -> frozenset[str]:
    """Collect scene/lore tags from available sources. Fail-closed → empty set."""
    tags: set[str] = set()
    try:
        scene = local_state.get("scene_state")
        if isinstance(scene, str) and scene.strip():
            tags.add(scene.strip().lower())
        for anchor in (local_state.get("symbolic_anchors") or []):
            if isinstance(anchor, str) and anchor.strip():
                tags.add(anchor.strip().lower())
        for t in (context_snapshot.get("scene_tags") or []):
            if isinstance(t, str) and t.strip():
                tags.add(t.strip().lower())
    except Exception:
        pass
    return frozenset(tags)


def _should_inject_hidden_state_snapshot(
    local_state: dict[str, Any],
    context_snapshot: dict[str, Any],
) -> bool:
    """Return True iff a trigger tag is present in current scene sources.

    Fail-closed: any exception → False (no injection).
    """
    try:
        return bool(_collect_scene_tags(local_state, context_snapshot) & _HIDDEN_STATE_TRIGGER_TAGS)
    except Exception:
        return False


def _format_hidden_state_snapshot(snapshot_data: dict[str, Any]) -> str:
    """Render a hidden-state bucket snapshot as a compact labeled block.

    Contract:
      - No float values are ever emitted.
      - No uid, timestamps, baselines, weights, or update_source fields.
      - Returns '' on any error or malformed input (fail-closed).
      - memory_cues line is omitted when the list is empty.
    """
    try:
        if not isinstance(snapshot_data, dict) or not snapshot_data:
            return ""
        lines: list[str] = ["[user_hidden_state_snapshot]"]
        for key in ("sensitivity", "touch_appetite", "embodied_ease"):
            val = snapshot_data.get(key)
            if not isinstance(val, str) or not val:
                return ""  # malformed — never inject partial data
            lines.append(f"{key}: {val}")
        cues = snapshot_data.get("memory_cues")
        if isinstance(cues, list) and cues:
            cue_strs = [str(c) for c in cues if c and str(c).strip()]
            if cue_strs:
                lines.append(f"memory_cues: {', '.join(cue_strs)}")
        return "\n".join(lines)
    except Exception:
        return ""


def build_dream_prompt(
    character: Any,
    user_id: str,
    user_message: str,
    context_snapshot: dict[str, Any],
    dream_history: list[dict[str, Any]],
    local_state: dict[str, Any],
    lore_entries: list[str] | None = None,
    jailbreak_text: str = "",
    jailbreak_preset_name: str = "default",
    jailbreak_preset_status: str = "",
    body_projection_text: str = "",
    yexuan_tension: float = 0.0,
    world_id: str = "reality_derived",
    lucid_mode: str = "lucid_shared",
    lore_meta: list[dict[str, Any]] | None = None,
    debug: bool = False,
    dream_mode: str = "sandbox",
    scenario_core: dict[str, Any] | None = None,
    mirror_core: dict[str, Any] | None = None,
    _capture_hook: "Any | None" = None,
    dream_turn: int = 0,
    reality_context_full_turns: int = 3,
    dream_domain: str = "solo",
    dg_layer_text: str | None = None,
    shared_transcript_block: str | None = None,
    scenario_injection_mode: str = "strict_stage",
) -> list[dict[str, str]]:
    """
    Assemble the complete dream prompt as a D0-D10 layer stack.

    Never imports or calls core/prompt_builder.py or its sanitizer.
    Returns a list of {role, content} dicts (OpenAI messages format).

    world_id: frozen at dream entry from dream_state.frozen_world.
    D2/D3 are loaded from the world package data files.

    梦境输出人称（单侧契约）：角色自称「我」；用户一律称「你」；只演角色自己这一轮，不替用户配台词、不用「她」。
    char_name/pronoun 从 character 对象推导；user_name 从 config.yaml user.display_name 读取。

    lore_meta: optional per-entry metadata from lore engine
               [{keywords, insertion_order, ...}, ...] — purely for observability logging.
    debug: if True, forces dream_prompt.token log to be emitted at INFO even if logger is
           otherwise filtered; also logs the final assembled system message.
    """
    from core.dream.world_loader import load_world
    world = load_world(world_id)

    char_name: str = getattr(character, "name", None) or "(角色未加载)"
    _char_gender_raw = getattr(character, "gender", None)
    _char_gender: str = _char_gender_raw if isinstance(_char_gender_raw, str) else "neutral"
    from core.character_name_provider import _PRONOUN_MAP as _PM
    char_pronoun: str = _PM.get(_char_gender, "ta")
    from core.config_loader import get_user_display_name
    user_name: str = get_user_display_name()
    system_layers: list[str] = []
    _records: list[_LayerRec] = []

    # ── D0: jailbreak ────────────────────────────────────────────────────────
    _d0_note = f"preset={jailbreak_preset_name}"
    if jailbreak_text:
        _d0 = f"# D0·破限 ─ {char_name}的自由边界\n{jailbreak_text}"
        system_layers.append(_d0)
        _d0_flags = [jailbreak_preset_status.upper()] if jailbreak_preset_status else []
        _records.append(_LayerRec("D0_jailbreak", len(_d0), _est_tokens(_d0), flags=_d0_flags, note=_d0_note, content=_d0))
    else:
        _records.append(_LayerRec("D0_jailbreak", flags=["DISABLED"], note=_d0_note))

    # ── D1 / D1S: identity core (FIXED — always above D2) ───────────────────
    _scenario_profile = dream_mode == "scenario" and dream_domain != "group"
    if _scenario_profile:
        from core.dream.scenario_profile import scenario_identity_projection
        d1_parts = [f"# D1S·剧本身份投影 ─ {char_name}（固定）"]
        identity_projection = scenario_identity_projection(character)
        if identity_projection:
            d1_parts.append(identity_projection)
    else:
        d1_parts = [f"# D1·身份核心 ─ {char_name}（固定）"]
        for label, field_name in (
            ("角色卡核心规则", "system_prompt"),
            ("角色设定", "description"),
            ("性格", "personality"),
        ):
            value = getattr(character, field_name, "") or ""
            if isinstance(value, list):
                value = "".join(str(item) for item in value)
            if isinstance(value, str) and value.strip():
                d1_parts.append(f"{label}：\n{value.strip()}")
    _d1_awareness = _D1_NON_LUCID_AWARENESS if lucid_mode == "non_lucid" else _D1_LUCID_AWARENESS
    d1_parts.append(_d1_awareness.format(
        name=char_name, pronoun=char_pronoun, user_clause=_format_user_clause(user_name),
    ))
    if dream_mode == "scenario":
        d1_parts.append(
            "剧本模式身份边界：保留核心人格与表达连续性，但当前剧本角色的公开立场、"
            "目标和可见行动以 DS 当前阶段为准。内在关心、依恋或犹豫可以存在，不能自动"
            "改写成放弃立场、解除限制或跳过剧情后果；只有当前阶段或用户的明确退出请求允许时才改变。"
        )
    _dream_behavior = _format_character_dream_behavior(character, dream_mode)
    if _dream_behavior:
        d1_parts.append(_dream_behavior)
    _d1 = "\n\n".join(d1_parts)
    system_layers.append(_d1)
    _records.append(_LayerRec(
        "D1_identity_core", len(_d1), _est_tokens(_d1),
        note="scenario_profile" if _scenario_profile else "", content=_d1,
    ))

    # ── DG: group in-scene presence (group domain only) ──────────────────────
    # Extends the D1 single-side pronoun contract to a multi-character scene:
    # "only perform your own turn, don't ventriloquize other characters or the
    # user" (Brief 100 §2). Placed right after D1 — like D1, this is an
    # identity/behavior constraint, not a world-layer concern, so it stays
    # above D2 in precedence. Hard-disabled outside dream_domain="group"
    # (scenario-style guard, mirrors the D4.5/D5/DS/DM pattern below).
    if dream_domain == "group" and dg_layer_text:
        _dg = f"# DG·梦内在场感\n{dg_layer_text}"
        system_layers.append(_dg)
        _records.append(_LayerRec("DG_group_presence", len(_dg), _est_tokens(_dg), content=_dg))
    else:
        _dg_note = "no_text" if dream_domain == "group" else "solo_domain"
        _records.append(_LayerRec("DG_group_presence", flags=["DISABLED"], note=_dg_note))

    # ── D2: world_ruleset (loaded from world package, subordinate to D1) ─────
    if world.ruleset and not _scenario_profile:
        _d2 = f"# D2·今晚梦的世界规则\n{world.ruleset}"
        system_layers.append(_d2)
        _records.append(_LayerRec("D2_world_ruleset", len(_d2), _est_tokens(_d2), content=_d2))
    else:
        _records.append(_LayerRec(
            "D2_world_ruleset", flags=["DISABLED"],
            note="scenario_profile" if _scenario_profile else "",
        ))

    # ── D3: dream_mes_example (loaded from world package) ────────────────────
    _mes_from_fallback = not bool(world.mes_example)
    example = world.mes_example or _get_dream_mes_example(char_name)
    if example and not _scenario_profile:
        _d3 = f"# D3·梦境示例对话\n{example}"
        system_layers.append(_d3)
        _d3_flags = ["FALLBACK"] if _mes_from_fallback else []
        _records.append(_LayerRec("D3_mes_example", len(_d3), _est_tokens(_d3), _d3_flags, content=_d3))
    else:
        _records.append(_LayerRec(
            "D3_mes_example", flags=["DISABLED"],
            note="scenario_profile" if _scenario_profile else "",
        ))

    # ── D4: frozen_reality (memory_access controlled) ────────────────────────
    # Scenario is a scripted-story space: the current stage is its only
    # context contract.  Do not even format the frozen snapshot here; this
    # keeps recent reality, profile impressions, episodic/mid-term material,
    # relationship state, and entry reasons out of the LLM messages by
    # construction rather than relying on an inspector/UI filter.
    snapshot_block = ""
    if dream_mode != "scenario":
        snapshot_block = _format_snapshot(
            context_snapshot,
            dream_turn=dream_turn,
            reality_context_full_turns=reality_context_full_turns,
        )
    if snapshot_block:
        _d4 = f"# D4·入梦前背景（冻结快照，只读）\n{snapshot_block}"
        system_layers.append(_d4)
        _records.append(_LayerRec("D4_frozen_reality", len(_d4), _est_tokens(_d4), content=_d4))
    else:
        _d4_note = "scenario_profile" if _scenario_profile else ""
        _records.append(_LayerRec("D4_frozen_reality", flags=["DISABLED"], note=_d4_note))

    # ── D4.5: user_hidden_state_snapshot (tag-gated, read-only, Phase 4) ────────
    # Injected only when body_intimate / physical_closeness tag is detected in scene.
    # Priority: lower than D4_frozen_reality; prune D4.5 before D4 if budget exceeded.
    # Dream NEVER writes back — DREAM_DIRECT_WRITABLE = frozenset().
    # Scenario mode is a scripted-story space: never reads User Hidden State.
    # Brief 100 §0: group dreams (dream_domain="group") hard-disable D4.5
    # unconditionally — scenario-style guard, not merely a tag-gate miss.
    _d45_injected = False
    if dream_mode != "scenario" and dream_domain != "group":
        try:
            _hs_data = context_snapshot.get("user_hidden_state_snapshot", {})
            if _should_inject_hidden_state_snapshot(local_state, context_snapshot):
                _d45_text = _format_hidden_state_snapshot(_hs_data)
                if _d45_text:
                    _d45 = f"# D4.5·用户隐性状态（只读快照）\n{_d45_text}"
                    system_layers.append(_d45)
                    _records.append(_LayerRec("D4.5_hidden_state", len(_d45), _est_tokens(_d45), content=_d45))
                    _d45_injected = True
        except Exception as _d45_exc:
            logger.warning("[dream_prompt] D4.5 hidden_state_snapshot failed: %s", _d45_exc)
    if not _d45_injected:
        if dream_domain == "group":
            _d45_note = "group_domain"
        elif dream_mode == "scenario":
            _d45_note = "scenario_profile"
        else:
            _d45_note = ""
        _records.append(_LayerRec("D4.5_hidden_state", flags=["DISABLED"], note=_d45_note))

    # ── D5: body_projection (injected by pipeline, 角色读投影文字) ───────────
    # Scenario mode is a scripted-story space: body/intimate expression is driven by
    # script stage text and narrative, not the general Dream body_state system.
    # D5 is always skipped for scenario to prevent style/mode boundary pollution.
    _d5_injected = False
    if body_projection_text and dream_mode != "scenario":
        _d5 = f"# D5·她的身体感知\n{body_projection_text}"
        system_layers.append(_d5)
        _records.append(_LayerRec("D5_body_projection", len(_d5), _est_tokens(_d5), content=_d5))
        _d5_injected = True
    if not _d5_injected:
        _d5_note = "scenario_profile" if _scenario_profile else ""
        _records.append(_LayerRec("D5_body_projection", flags=["DISABLED"], note=_d5_note))

    # ── D6: scene_anchors ────────────────────────────────────────────────────
    scene_block = _format_scene_anchors(local_state)
    if scene_block and not _scenario_profile:
        _d6 = f"# D6·场景锚点\n{scene_block}"
        system_layers.append(_d6)
        _records.append(_LayerRec("D6_scene_anchors", len(_d6), _est_tokens(_d6), content=_d6))
    else:
        _records.append(_LayerRec(
            "D6_scene_anchors", flags=["DISABLED"],
            note="scenario_profile" if _scenario_profile else "",
        ))

    # ── D7: dream_tension ────────────────────────────────────────────────────
    if yexuan_tension > 0.05 and not _scenario_profile:
        _d7_bucket = _bucket_tension(yexuan_tension)
        _d7 = (
            f"# D7·{char_name}情绪张力\n"
            f"当前情绪张力水位：{_d7_bucket}\n"
            f"（这是梦内累积的情绪紧绷程度，影响{char_name}的表达方式和反应灵敏度。）"
        )
        system_layers.append(_d7)
        _records.append(_LayerRec("D7_dream_tension", len(_d7), _est_tokens(_d7), content=_d7))
    else:
        _records.append(_LayerRec(
            "D7_dream_tension", flags=["DISABLED"],
            note="scenario_profile" if _scenario_profile else "",
        ))

    # ── D8: dream_director ───────────────────────────────────────────────────
    if _scenario_profile:
        from core.dream.scenario_profile import SCENARIO_DIRECTOR
        _d8 = f"# D8S·剧本导演注记\n{SCENARIO_DIRECTOR.format(name=char_name)}"
    else:
        _d8_raw = _D8_DREAM_DIRECTOR_NON_LUCID if lucid_mode == "non_lucid" else _D8_DREAM_DIRECTOR
        _d8 = f"# D8·梦境导演注记\n{_d8_raw.format(name=char_name)}"
    system_layers.append(_d8)
    _records.append(_LayerRec(
        "D8_dream_director", len(_d8), _est_tokens(_d8),
        note="scenario_profile" if _scenario_profile else "", content=_d8,
    ))

    # Keep exit safety test-independent: D8 may be ablated, DX may not.
    _dx = f"# DX·梦境退出协议\n{_DX_EXIT_PROTOCOL.format(name=char_name)}"
    system_layers.append(_dx)
    _records.append(_LayerRec("DX_exit_protocol", len(_dx), _est_tokens(_dx), content=_dx))

    # ── DS: scenario layer (only when dream_mode == "scenario") ─────────────
    # Injects: script title, current stage name, dramatic_task, entry_pressure,
    #          not_yet_allowed.
    # Never injects: subsequent stages, exit_signs, soft-gate logic.
    _ds_injected = False
    _scenario_observation: dict[str, Any] | None = None
    if dream_mode == "scenario" and scenario_core and dream_domain != "group":
        try:
            _ds_text = _format_scenario_layer(scenario_core, injection_mode=scenario_injection_mode)
            if _ds_text:
                _ds = f"# DS·剧本当前阶段\n{_ds_text}"
                system_layers.append(_ds)
                _records.append(_LayerRec("DS_scenario", len(_ds), _est_tokens(_ds), content=_ds))
                _ds_injected = True
                _scenario_observation = {
                    "current_stage_id": scenario_core.get("current_stage_id"),
                    "injection_mode": scenario_injection_mode if scenario_injection_mode in {"strict_stage", "full_script"} else "strict_stage",
                    "stall_turns": int(scenario_core.get("stall_turns", 0) or 0),
                    "recovery_injected": "【接住刚才的意图】" in _ds_text,
                    "drift_pressure_injected": "漂移压力 / Drift Pressure" in _ds_text,
                    "generic_recovery_injected": "轻量拉回" in _ds_text,
                }
        except Exception as _ds_exc:
            logger.warning("[dream_prompt] DS scenario layer failed: %s", _ds_exc)
    if not _ds_injected:
        _ds_note = "non-scenario" if dream_mode != "scenario" else "no_core"
        _records.append(_LayerRec("DS_scenario", flags=["DISABLED"], note=_ds_note))

    # ── DM: mirror context layer (only when dream_mode == "mirror") ───────────
    # Injects: coarse bucket labels + lightweight symbolic hints.
    # Never injects: float values, percentages, uid, timestamps, weights.
    # Never injects: psychological diagnosis or direct user-psychology analysis.
    _dm_injected = False
    if dream_mode == "mirror" and mirror_core and dream_domain != "group":
        try:
            _dm_text = _format_mirror_layer(mirror_core)
            if _dm_text:
                _dm = f"# DM·Mirror 梦境倾向材料\n{_dm_text}"
                system_layers.append(_dm)
                _records.append(_LayerRec("DM_mirror", len(_dm), _est_tokens(_dm), content=_dm))
                _dm_injected = True
        except Exception as _dm_exc:
            logger.warning("[dream_prompt] DM mirror layer failed: %s", _dm_exc)
    if not _dm_injected:
        _dm_note = "scenario_profile" if _scenario_profile else ("non-mirror" if dream_mode != "mirror" else "no_core")
        _records.append(_LayerRec("DM_mirror", flags=["DISABLED"], note=_dm_note))

    # ── Dream lorebook (injected between D4 and D5 conceptually) ─────────────
    if lore_entries and not _scenario_profile:
        _dlore = "# 梦境世界书\n" + "\n---\n".join(lore_entries)
        system_layers.append(_dlore)
        _lore_note = f"{len(lore_entries)} entries"
        _records.append(_LayerRec("D_lorebook", len(_dlore), _est_tokens(_dlore), note=_lore_note, content=_dlore))
    else:
        _records.append(_LayerRec(
            "D_lorebook", flags=["DISABLED"],
            note="scenario_profile" if _scenario_profile else "",
        ))

    # ── D9 (group domain only): shared transcript folded into system ─────────
    # Group dream's D9 is a single speaker-prefixed text block (rendered by the
    # caller from the shared transcript), not per-turn user/assistant messages
    # — multiple different characters' lines can't be represented as OpenAI
    # chat roles for one character's own generation call. Never passes through
    # the reality sanitizer (Brief 100 §2 D9).
    if dream_domain == "group" and shared_transcript_block is not None:
        _d9g = f"# D9·梦内共享对话（{char_name}视角，speaker 前缀，不过现实 sanitizer）\n{shared_transcript_block}"
        system_layers.append(_d9g)
        _records.append(_LayerRec("D9_dream_history", len(_d9g), _est_tokens(_d9g), note="group_shared_transcript", content=_d9g))

    # ── Dream layer ablation: filter assembled injection only ────────────────
    # All loaders/state calculations above still run. D8 (rendering + hard exit)
    # and D10 (current user message) are protected by dream_prompt_ablation.
    from core.dream.dream_prompt_ablation import get_state as _dream_ablation_state
    _ablated_layers = set(_dream_ablation_state()["disabled_layers"])
    _ablated_system_contents = {
        rec.content for rec in _records
        if rec.label in _ablated_layers and rec.content
    }
    if _ablated_system_contents:
        system_layers = [layer for layer in system_layers if layer not in _ablated_system_contents]

    system_content = "\n\n".join(layer for layer in system_layers if layer.strip())
    messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]

    # ── D9: dream_history (as messages, no sanitizer; solo domain only) ──────
    if dream_domain != "group":
        _d9_chars = 0
        _d9_parts: list[str] = []
        for turn in dream_history:
            role = turn.get("role", "user")
            if role not in ("user", "assistant"):
                role = "user"
            _turn_content = (turn.get("content") or "").strip()
            if _turn_content and "D9_dream_history" not in _ablated_layers:
                messages.append({"role": role, "content": _turn_content})
            if _turn_content:
                _d9_chars += len(_turn_content)
                _d9_parts.append(f"[{role}] {_turn_content}")
        _d9_toks = max(1, _d9_chars // _TOK_RATIO) if _d9_chars else 0
        _records.append(
            _LayerRec("D9_dream_history", _d9_chars, _d9_toks, note=f"{len(dream_history)} turns",
                      content="\n\n".join(_d9_parts))
        )

    # ── D10: user_message ────────────────────────────────────────────────────
    messages.append({"role": "user", "content": user_message})
    _records.append(_LayerRec("D10_user_message", len(user_message), _est_tokens(user_message), content=user_message))

    # Preserve the layer catalog/content for inspection while making injected
    # stats reflect the exact messages sent to the model.
    for _rec in _records:
        if _rec.label in _ablated_layers:
            _rec.chars = 0
            _rec.tokens = 0
            if "ABLATED" not in _rec.flags:
                _rec.flags.append("ABLATED")

    # ── Observability: emit layer stats ──────────────────────────────────────
    _log_dream_prompt_stats(
        records=_records,
        world_id=world_id,
        lucid_mode=lucid_mode,
        world_ruleset_chars=len(world.ruleset) if world.ruleset else 0,
        world_mes_chars=len(world.mes_example) if world.mes_example else 0,
        mes_from_fallback=_mes_from_fallback,
        lore_entries=lore_entries,
        lore_meta=lore_meta,
    )
    if debug:
        _dream_token_logger.info("[DREAM_SYSTEM_MSG]\n%s", system_content)

    # ── Capture hook (admin panel dream-prompt inspector) ─────────────────────
    if _capture_hook is not None:
        try:
            _scene_tags = _collect_scene_tags(local_state, context_snapshot)
            _total_tok = sum(r.tokens for r in _records if r.chars > 0)
            _capture_hook({
                "world_id": world_id,
                "lucid_mode": lucid_mode,
                "dream_mode": dream_mode,
                "prompt_profile": "scenario" if _scenario_profile else ("group" if dream_domain == "group" else dream_mode),
                "prompt_profile_version": "v2" if _scenario_profile else "v1",
                "scenario_observation": _scenario_observation,
                "scenario_injection_mode": scenario_injection_mode if dream_mode == "scenario" else None,
                "scenario_projection": _scenario_projection_metadata(scenario_core, scenario_injection_mode),
                "scene_tags": sorted(_scene_tags),
                "total_tokens": _total_tok,
                "ablated_layers": sorted(_ablated_layers),
                "layers": [
                    {
                        "label": r.label,
                        "chars": r.chars,
                        "tokens": r.tokens,
                        "flags": list(r.flags),
                        "note": r.note,
                        "content": r.content,
                        "injected": r.chars > 0,
                    }
                    for r in _records
                ],
                "history_turns": len(dream_history),
            })
        except Exception as _hook_exc:
            logger.debug("[build_dream_prompt] capture hook failed: %s", _hook_exc)

    return messages


# ── Inspect helper ────────────────────────────────────────────────────────────


def dump_dream_prompt(messages: list[dict[str, str]]) -> str:
    """
    Return the assembled system message from a build_dream_prompt() result.

    Usage:
        msgs = build_dream_prompt(...)
        print(dump_dream_prompt(msgs))
    """
    for msg in messages:
        if msg.get("role") == "system":
            return msg["content"]
    return ""


# ── Tension bucket ────────────────────────────────────────────────────────────


def _bucket_tension(value: float) -> str:
    """Map a [0, 1] tension float to a coarse semantic bucket label.

    Clamps out-of-range input: < 0 → 低位, > 1 → 临界.
    """
    v = max(0.0, min(1.0, value))
    if v < 0.25:
        return "低位"
    if v < 0.5:
        return "上升中"
    if v < 0.75:
        return "高位"
    return "临界"


# ── Internal formatters ───────────────────────────────────────────────────────


def _format_snapshot(snapshot: dict[str, Any], *, dream_turn: int = 0, reality_context_full_turns: int = 3) -> str:
    parts: list[str] = []
    if dream_turn < reality_context_full_turns:
        if r := snapshot.get("recent_reality_context"):
            parts.append(f"最近现实对话摘要：\n{r}")
    else:
        if gist := snapshot.get("recent_reality_gist"):
            parts.append(f"（你记得入梦前你们在{gist}）")
    if p := snapshot.get("profile_impression"):
        parts.append(f"她的印象：{p}")
    if e := snapshot.get("episodic_summary"):
        parts.append(f"记忆片段：\n{e}")
    if m := snapshot.get("mid_term_context"):
        parts.append(f"近期互动背景：\n{m}")
    if rel := snapshot.get("relationship_state"):
        rel_str = _format_relation(rel)
        if rel_str:
            parts.append(f"关系状态：{rel_str}")
    if reason := snapshot.get("entry_reason"):
        parts.append(f"入梦原因：{reason}")
    return "\n\n".join(parts)


def _format_relation(rel: dict[str, Any]) -> str:
    if not rel:
        return ""
    parts: list[str] = []
    if affection := rel.get("affection"):
        parts.append(f"好感度={affection}")
    if priority := rel.get("priority"):
        parts.append(f"关系优先级={priority}")
    if note := rel.get("note"):
        parts.append(str(note))
    return "；".join(parts)


def _format_scene_anchors(local_state: dict[str, Any]) -> str:
    parts: list[str] = []
    if scene := local_state.get("scene_state"):
        parts.append(f"当前场景：{scene}")
    anchors = local_state.get("symbolic_anchors") or []
    if anchors:
        parts.append(f"象征锚点：{', '.join(str(a) for a in anchors)}")
    return "；".join(parts)


def _format_character_dream_behavior(character: Any, dream_mode: str) -> str:
    """Return optional per-character Dream direction from ``presence_ext``.

    The shared Dream layers own rendering/isolation/protocol only. Personality
    policy belongs to the authored character card and can provide a common
    anchor plus one directive for the active mode. Malformed values are ignored
    so legacy and plain-text cards retain their existing behavior.
    """
    try:
        ext = getattr(character, "presence_ext", {}) or {}
        behavior = ext.get("dream_behavior") if isinstance(ext, dict) else None
        if not isinstance(behavior, dict):
            return ""
        parts: list[str] = []
        identity_anchor = behavior.get("identity_anchor")
        if isinstance(identity_anchor, str) and identity_anchor.strip():
            parts.append(f"角色卡梦境人格锚点：\n{identity_anchor.strip()}")
        mode_key = "scenario_directive" if dream_mode == "scenario" else "sandbox_directive"
        mode_directive = behavior.get(mode_key)
        if isinstance(mode_directive, str) and mode_directive.strip():
            label = "剧本模式" if dream_mode == "scenario" else "自由梦境模式"
            parts.append(f"角色卡{label}指令：\n{mode_directive.strip()}")
        return "\n\n".join(parts)
    except Exception:
        return ""


def _scenario_projection_metadata(
    scenario_core: dict[str, Any] | None,
    injection_mode: str,
) -> dict[str, Any]:
    if not isinstance(scenario_core, dict):
        return {}
    try:
        from core.dream.scenario_projection import scenario_projection_metadata

        return scenario_projection_metadata(scenario_core, injection_mode=injection_mode)
    except Exception:
        return {}


def _format_scenario_layer(
    scenario_core: dict[str, Any], *, injection_mode: str = "strict_stage"
) -> str:
    """
    Render the current scenario stage as a DS prompt block.

    Injects current-stage content plus actor-private truths with only the current
    stage's disclosure policy.  Future stage content and future disclosure rules
    remain unavailable to the model.

    Never injects: subsequent stages, stage-exit judgment, or auto-advance logic.
    Returns '' on any error (fail-closed).
    """
    if injection_mode == "full_script":
        try:
            from core.dream.scenario_loader import load_script
            from core.dream.scenario_projection import render_full_script

            script_id = str(scenario_core.get("script_id") or "")
            current_stage_id = str(scenario_core.get("current_stage_id") or "")
            if not script_id or not current_stage_id:
                return ""
            return render_full_script(load_script(script_id), current_stage_id)
        except Exception as exc:
            logger.warning("[dream_prompt] full scenario projection failed: %s", exc)
            return ""

    try:
        from core.dream.scenario_loader import load_script, get_stage
        script_id = scenario_core.get("script_id", "")
        stage_id = scenario_core.get("current_stage_id", "")
        if not script_id or not stage_id:
            return ""
        script = load_script(script_id)
        stage = get_stage(script, stage_id)
        if not stage:
            logger.warning("[dream_prompt] DS stage %r not found in script %r", stage_id, script_id)
            return ""
        parts: list[str] = ["【本轮必须遵循】"]
        if scenario_core.get("ending_state") == "completed":
            parts.append("【剧本状态：所有阶段已完成 — Scenario Completed】")
        parts.append(f"剧本：{script.get('title', script_id)}")
        parts.append(f"当前阶段：{stage.get('name', stage_id)}")
        if task := stage.get("dramatic_task", "").strip():
            parts.append(f"戏剧任务：\n{task}")
        if pressure := stage.get("entry_pressure", "").strip():
            parts.append(f"入场压力：\n{pressure}")
        parts.append(
            "【本轮行动契约】\n"
            "保持当前阶段赋予角色的公开立场。不要把内在关心自动写成退让、放弃目标或解除限制。\n"
            "除非这一轮自然处于观察或停顿节点，否则回复必须包含至少一个会改变现场状态、信息、"
            "距离、资源或选择空间的具体角色行动；只有口头威胁、重复警告或气氛描写不算行动推进。\n"
            "行动不得越过本阶段的禁止事项，也不得替用户决定动作、感受或台词。"
        )

        private_truths = script.get("private_truths") or []
        for private_truth in private_truths:
            if not isinstance(private_truth, dict):
                continue
            truth = str(private_truth.get("truth") or "").strip()
            if not truth:
                continue
            disclosure = private_truth.get("disclosure") or {}
            rule = disclosure.get(stage_id) if isinstance(disclosure, dict) else None
            rule = rule if isinstance(rule, dict) else {}
            policy = rule.get("policy", "hidden")
            truth_parts = [
                "【角色私下知道的真相】",
                truth,
                "这是你从本阶段开始前就知道的事实，不要把自己演成对此失忆或刚刚才发现。",
                "【当前阶段披露权限】",
            ]
            if policy == "hint_only":
                truth_parts.append("只可通过下列线索含蓄表现；不得直接说出、确认或解释完整真相。")
                hints = [
                    str(hint).strip()
                    for hint in (rule.get("allowed_hints") or [])
                    if str(hint).strip()
                ]
                if hints:
                    truth_parts.append("允许的线索：\n" + "\n".join(f"· {hint}" for hint in hints))
                else:
                    truth_parts.append("本阶段没有指定可用线索，按 hidden 处理，不主动暗示真相。")
            elif policy == "reveal_allowed":
                truth_parts.append("本阶段允许在叙事自然到达时揭露真相，但不要为了说明设定而生硬抢跑。")
            elif policy == "reveal_required":
                truth_parts.append("本阶段必须让真相在剧情中落地；通过角色行动或台词自然完成揭露。")
            else:
                truth_parts.append("本阶段必须隐藏真相：你仍按知情者行动，但不得直接说出、确认或主动暗示。")
            parts.append("\n".join(truth_parts))
        not_yet = stage.get("not_yet_allowed") or []
        if not_yet:
            parts.append("本阶段不允许：\n" + "\n".join(f"· {item}" for item in not_yet))

        # A blocked event is a one-turn recovery cue.  It is populated only
        # after whitelist normalization in dream_pipeline, so authored stage
        # text and the bounded blocked item are the only inputs here.
        blocked_events = [
            str(item).strip()
            for item in (scenario_core.get("last_blocked_events") or [])
            if str(item).strip()
        ]
        if scenario_core.get("recovery_pending") and blocked_events:
            task = str(stage.get("dramatic_task") or "").strip()
            recovery_parts = [
                "【接住刚才的意图】",
                "先承接用户刚才想做的事，不指责、不打断沉浸感。",
                "本轮不要执行下面尚未允许的事项：\n"
                + "\n".join(f"· {item}" for item in blocked_events),
                "用环境变化、角色动作、信息缺口或局部压力，把注意力自然带回当前戏剧任务。",
            ]
            if task:
                recovery_parts.append(f"当前戏剧任务：\n{task}")
            recovery_parts += [
                "不要透露后续阶段，不要替用户决定动作或台词，也不要向用户解释这段提示。",
            ]
            parts.append("\n".join(recovery_parts))

        # Drift pressure is based on consecutive stalled turns, not total
        # turns.  Only the current stage's pressure can be injected.
        dp = stage.get("drift_pressure")
        stall_turns = int(scenario_core.get("stall_turns", 0) or 0)
        if dp and isinstance(dp, dict):
            after_turns = dp.get("after_turns")
            instruction = (dp.get("instruction") or "").strip()
            if isinstance(after_turns, int) and instruction and stall_turns >= after_turns:
                parts.append(f"漂移压力 / Drift Pressure\n{instruction}")
        elif stall_turns >= _GENERIC_SCENARIO_RECOVERY_AFTER_STALL_TURNS:
            task = str(stage.get("dramatic_task") or "").strip()
            if task:
                parts.append(
                    "轻量拉回\n"
                    "当前阶段出现停滞。保持自然叙事，把注意力带回当前戏剧任务；"
                    f"不要创造剧本外事实，也不要透露后续阶段。\n{task}"
                )
        target = stage.get("arc")
        if scenario_core.get("_arc_mode") == "arc" and target:
            current = scenario_core.get("_tension_bucket", "low")
            rank = {"low": 0, "rising": 1, "high": 2, "critical": 3}
            if rank.get(current, 0) < rank.get(target, 0):
                parts.append("张力导演：收紧节奏，推进冲突或靠近。")
            elif rank.get(current, 0) > rank.get(target, 0):
                parts.append("张力导演：放缓，给彼此喘息，退半步。")
        # Hidden turn note: v2 uses short IDs only. The system strips this
        # note before the user sees the reply; legacy parsers remain compatible.
        exit_signs = [
            str(item).strip()
            for item in (stage.get("exit_signs") or [])
            if isinstance(item, str) and item.strip()
        ]
        blocked_items = [
            str(item).strip()
            for item in (stage.get("not_yet_allowed") or [])
            if isinstance(item, str) and item.strip()
        ]
        protocol_lines: list[str] = [
            "---",
            "写完这一轮的自然回复后，再在末尾留一段给系统看的短 ID 备注。不要向用户解释这段备注：",
            '<scenario_control>{"hit":["E1"],"blocked":[]}</scenario_control>',
            "只使用当前阶段列出的 E* / B* 短 ID；不要提交 stage_id 或 next_stage。",
            "hit 是必填数组：本轮没有完成信号就写 []；blocked 可省略，或写本轮实际触及的 B*。",
        ]
        if exit_signs:
            protocol_lines += [
                "",
                "当前阶段完成信号（只回传实际完成的 E*）：",
                "\n".join(f"  E{index} — {item}" for index, item in enumerate(exit_signs, 1)),
            ]
        else:
            protocol_lines.append("本阶段没有完成信号，hit 始终写 []。")
        if blocked_items:
            protocol_lines += [
                "",
                "当前阶段禁止事项（只回传本轮实际触及的 B*）：",
                "\n".join(f"  B{index} — {item}" for index, item in enumerate(blocked_items, 1)),
            ]
        protocol_lines += [
            "",
            "只写短 ID，不要照抄长文本；阶段切换由系统确定性判断。",
        ]
        parts.append("\n".join(protocol_lines))
        return "\n\n".join(parts)
    except Exception as exc:
        logger.warning("[dream_prompt] _format_scenario_layer error: %s", exc)
        return ""


def _format_mirror_layer(mirror_core: dict[str, Any]) -> str:
    """Render mirror_core as a DM prompt block.

    Contract:
      - No float values emitted.
      - No exact numeric percentages.
      - No uid, timestamp, weight, baseline, update_source.
      - No psychological diagnosis.
      - No "你潜意识里..." / "用户心理" language.
      - Returns '' on any error (fail-closed).
    """
    try:
        buckets = mirror_core.get("snapshot_buckets") or {}
        hints = mirror_core.get("symbolic_hints") or []

        if not buckets:
            return ""

        _BUCKET_LABELS: dict[str, str] = {
            "low": "低",
            "medium": "中",
            "high": "高",
            "unknown": "未知",
        }
        _PRESENCE_LABELS: dict[str, str] = {
            "none": "无",
            "light": "淡",
            "present": "有",
        }

        lines: list[str] = [
            "这是梦境的隐喻材料，不是诊断结论。",
            "请把这些倾向转化为环境、距离、重复意象、靠近/退后节奏。",
            "不要直接分析用户心理。不要明说数值。",
            "",
            "当前倾向：",
        ]

        _BUCKET_NAMES: list[tuple[str, str]] = [
            ("sensitivity_bucket", "感知敏锐度"),
            ("closeness_need_bucket", "靠近需求"),
            ("embodied_ease_bucket", "身体放松度"),
        ]
        for key, label in _BUCKET_NAMES:
            val = buckets.get(key, "unknown")
            lines.append(f"  {label}：{_BUCKET_LABELS.get(val, val)}")

        presence = buckets.get("association_presence", "")
        if presence in _PRESENCE_LABELS:
            lines.append(f"  重复意象倾向：{_PRESENCE_LABELS[presence]}")

        if hints:
            lines.append("")
            for hint in hints:
                lines.append(f"· {hint}")

        return "\n".join(lines)
    except Exception:
        return ""


def _get_dream_mes_example(char_name: str) -> str:
    """
    Fallback dream mes_example when world package file is missing.
    Preferred path: world.mes_example loaded from world package data file.

    人称契约（方案一·单侧）：角色全程以「我」自称，称用户为「你」，
    只演角色自己这一轮，不替用户旁白、不配台词、不用「她」。
    独立于现实角色卡 mes_example，避免交叉污染。
    """
    return (
        f"*停住脚步，看着那片光落在你身上，声音比平时低*……你也在。一直在。\n"
        f"*慢慢走近*这里不一样，什么都更清楚。但我是真的在这里。\n"
        f"*目光没有移开*……想留下的话，那就先别醒。"
    )
