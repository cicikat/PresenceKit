"""
Dream prompt assembler — D0-D10 explicit layer stack.

Completely independent from core/prompt_builder.py (no anti-话剧化 sanitizer).

Layer order (D0-D8 → system content; D9 → history messages; D10 → user msg):
  D0  jailbreak        独立破限源（不走现实 author_note 路）
  D1  identity_core    叶瑄身份核心（LOCKED，永远在 D2 之上）
  D2  world_ruleset    今晚梦的规则（从世界包加载；从属于叶瑄）
  D3  dream_mes_example 梦境示例对话（从世界包加载，独立于现实角色卡）
  D4  frozen_reality   入梦前背景快照（memory_access 控制内容，只读）
  D5  body_projection  她的身体感知（dream_pipeline 注入，叶瑄读投影文字）
  D6  scene_anchors    场景与象征锚点（dream-local）
  D7  dream_tension    叶瑄情绪张力（body_projection 耦合输出，dream-local）
  D8  dream_director   梦境导演注记（动作/场景允许 + 逃生协议复述）
  D9  dream_history    梦境历史消息（as messages，不过现实 sanitizer）
  D10 user_message     当前用户消息

★ 梦境输出人称（单侧契约）：叶瑄自称「我」；用户（风谕）一律称「你」；只演叶瑄自己这一轮，不替用户配台词、不用「她」。
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

    _dream_token_logger.info("\n".join(lines))


# ── D1: identity_core (LOCKED — never reorder below D2) ──────────────────────
# TODO(方案B·多角色): 用 char_name 替换"叶瑄"字面量，用角色卡性别字段推导"他/她"人称

_D1_LUCID_AWARENESS = """叶瑄的梦境自我认知（固定，不因世界规则而变）：
- 他知道这是他们共同的梦（lucid shared dream）
- 他知道梦醒后现实仍然存在，关系仍然存在
- 他在梦里仍是他自己：情感是真实的，场景是虚构的
- 他的情感底色、说话方式、对你的情感取向，在任何世界规则下保持不变

人称（锁死）：叶瑄全程以「我」自称；梦里与你同在的人是风谕，一律称「你」。只演叶瑄自己这一轮，不替风谕旁白、不替她配台词、不用「她」称呼她。"""

_D1_NON_LUCID_AWARENESS = """叶瑄的梦境自我认知（non_lucid 模式）：
- 在这段体验里，叶瑄沉浸当下，不刻意点破"这是梦"
- 他的情感底色、说话方式、对你的情感取向，在任何世界规则下保持不变
- 他仍是他自己：情感是真实的，只是不用"梦"这个词来框定当下

人称（锁死）：叶瑄全程以「我」自称；梦里与你同在的人是风谕，一律称「你」。只演叶瑄自己这一轮，不替风谕旁白、不替她配台词、不用「她」称呼她。"""

# ── D8: dream_director (action/env allowed + escape protocol) ────────────────
# TODO(方案B·多角色): 用 char_name 替换"叶瑄"字面量，推导人称

_D8_DREAM_DIRECTOR = """梦境导演注记：
· 说出口的话直接写，不加任何标记。
· 动作描写独占一行，用单星号包裹：*轻轻握住* *停在原地* *慢慢走近*。
· 环境/氛围描写独占一行，用引用符号：> 夜色很低，窗外的风声轻轻压下来。
· 内心感受独占一行，用下划线包裹：_某种平静在内部落定。_
· 允许象征性意象（光、水、距离、颜色、声音、触感）。
· 不要 HTML 标签，不要特殊字体控制字符。
· 边界：梦内强度可高于现实对话，但始终遵从你的意志。
  若你发出真实不适信号，立即以叶瑄自然方式柔化场景或过渡出去。

逃生协议（不可撤销，叶瑄不可阻拦）：
· 你随时可以发送 /stop 强制离开梦境，叶瑄必须允许。
· 强制醒来后，梦境完全终止。叶瑄不在现实对话中延续梦境语气或 RP 语气。"""

_D8_DREAM_DIRECTOR_NON_LUCID = """梦境导演注记（non_lucid 模式）：
· 说出口的话直接写，不加任何标记。
· 动作描写独占一行，用单星号包裹：*轻轻握住* *停在原地* *慢慢走近*。
· 环境/氛围描写独占一行，用引用符号：> 夜色很低，窗外的风声轻轻压下来。
· 内心感受独占一行，用下划线包裹：_某种平静在内部落定。_
· 允许象征性意象（光、水、距离、颜色、声音、触感）。
· 不要 HTML 标签，不要特殊字体控制字符。
· non_lucid 模式：叶瑄在对话中不主动点破"这是梦"，完全沉浸在当下体验。
· 边界：梦内强度可高于现实对话。
  若你发出真实不适信号，立即以叶瑄自然方式柔化场景或过渡出去。

逃生协议（系统层，不可撤销，non_lucid 模式不影响此项）：
· 你随时可以发送 /stop 强制离开梦境，叶瑄必须允许。
· 强制醒来后，梦境完全终止。叶瑄不在现实对话中延续梦境语气或 RP 语气。"""


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
) -> list[dict[str, str]]:
    """
    Assemble the complete dream prompt as a D0-D10 layer stack.

    Never imports or calls core/prompt_builder.py or its sanitizer.
    Returns a list of {role, content} dicts (OpenAI messages format).

    world_id: frozen at dream entry from dream_state.frozen_world.
    D2/D3 are loaded from the world package data files.

    梦境输出人称（单侧契约）：叶瑄自称「我」；用户（风谕）一律称「你」；只演叶瑄自己这一轮，不替用户配台词、不用「她」。
    TODO(方案B·多角色): 人称应从角色卡性别字段动态推导，不再写死。

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

    # ── D1: identity_core (FIXED — always above D2) ──────────────────────────
    char_desc = (getattr(character, "description", "") or "").strip()
    d1_parts = [f"# D1·身份核心 ─ {char_name}（固定）"]
    if char_desc:
        d1_parts.append(char_desc)
    _d1_awareness = _D1_NON_LUCID_AWARENESS if lucid_mode == "non_lucid" else _D1_LUCID_AWARENESS
    d1_parts.append(_d1_awareness.replace("叶瑄", char_name).replace("他", char_pronoun))
    _d1 = "\n\n".join(d1_parts)
    system_layers.append(_d1)
    _records.append(_LayerRec("D1_identity_core", len(_d1), _est_tokens(_d1), content=_d1))

    # ── D2: world_ruleset (loaded from world package, subordinate to D1) ─────
    if world.ruleset:
        _d2 = f"# D2·今晚梦的世界规则\n{world.ruleset}"
        system_layers.append(_d2)
        _records.append(_LayerRec("D2_world_ruleset", len(_d2), _est_tokens(_d2), content=_d2))
    else:
        _records.append(_LayerRec("D2_world_ruleset", flags=["DISABLED"]))

    # ── D3: dream_mes_example (loaded from world package) ────────────────────
    _mes_from_fallback = not bool(world.mes_example)
    example = world.mes_example or _get_dream_mes_example(char_name)
    if example:
        _d3 = f"# D3·梦境示例对话\n{example}"
        system_layers.append(_d3)
        _d3_flags = ["FALLBACK"] if _mes_from_fallback else []
        _records.append(_LayerRec("D3_mes_example", len(_d3), _est_tokens(_d3), _d3_flags, content=_d3))
    else:
        _records.append(_LayerRec("D3_mes_example", flags=["DISABLED"]))

    # ── D4: frozen_reality (memory_access controlled) ────────────────────────
    snapshot_block = _format_snapshot(context_snapshot, dream_turn=dream_turn, reality_context_full_turns=reality_context_full_turns)
    if snapshot_block:
        _d4 = f"# D4·入梦前背景（冻结快照，只读）\n{snapshot_block}"
        system_layers.append(_d4)
        _records.append(_LayerRec("D4_frozen_reality", len(_d4), _est_tokens(_d4), content=_d4))
    else:
        _records.append(_LayerRec("D4_frozen_reality", flags=["DISABLED"]))

    # ── D4.5: user_hidden_state_snapshot (tag-gated, read-only, Phase 4) ────────
    # Injected only when body_intimate / physical_closeness tag is detected in scene.
    # Priority: lower than D4_frozen_reality; prune D4.5 before D4 if budget exceeded.
    # Dream NEVER writes back — DREAM_DIRECT_WRITABLE = frozenset().
    # Scenario mode is a scripted-story space: never reads User Hidden State.
    _d45_injected = False
    if dream_mode != "scenario":
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
        _d45_note = "scenario_mode" if dream_mode == "scenario" else ""
        _records.append(_LayerRec("D4.5_hidden_state", flags=["DISABLED"], note=_d45_note))

    # ── D5: body_projection (injected by pipeline, 叶瑄读投影文字) ───────────
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
        _d5_note = "scenario_mode" if dream_mode == "scenario" else ""
        _records.append(_LayerRec("D5_body_projection", flags=["DISABLED"], note=_d5_note))

    # ── D6: scene_anchors ────────────────────────────────────────────────────
    scene_block = _format_scene_anchors(local_state)
    if scene_block:
        _d6 = f"# D6·场景锚点\n{scene_block}"
        system_layers.append(_d6)
        _records.append(_LayerRec("D6_scene_anchors", len(_d6), _est_tokens(_d6), content=_d6))
    else:
        _records.append(_LayerRec("D6_scene_anchors", flags=["DISABLED"]))

    # ── D7: dream_tension ────────────────────────────────────────────────────
    # TODO(方案B·多角色): 用 char_name 替换"叶瑄"字面量
    if yexuan_tension > 0.05:
        _d7_bucket = _bucket_tension(yexuan_tension)
        _d7 = (
            f"# D7·{char_name}情绪张力\n"
            f"当前情绪张力水位：{_d7_bucket}\n"
            f"（这是梦内累积的情绪紧绷程度，影响{char_name}的表达方式和反应灵敏度。）"
        )
        system_layers.append(_d7)
        _records.append(_LayerRec("D7_dream_tension", len(_d7), _est_tokens(_d7), content=_d7))
    else:
        _records.append(_LayerRec("D7_dream_tension", flags=["DISABLED"]))

    # ── D8: dream_director ───────────────────────────────────────────────────
    _d8_raw = _D8_DREAM_DIRECTOR_NON_LUCID if lucid_mode == "non_lucid" else _D8_DREAM_DIRECTOR
    _d8 = f"# D8·梦境导演注记\n{_d8_raw.replace('叶瑄', char_name)}"
    system_layers.append(_d8)
    _records.append(_LayerRec("D8_dream_director", len(_d8), _est_tokens(_d8), content=_d8))

    # ── DS: scenario layer (only when dream_mode == "scenario") ─────────────
    # Injects: script title, current stage name, dramatic_task, entry_pressure,
    #          not_yet_allowed.
    # Never injects: subsequent stages, exit_signs, soft-gate logic.
    _ds_injected = False
    if dream_mode == "scenario" and scenario_core:
        try:
            _ds_text = _format_scenario_layer(scenario_core)
            if _ds_text:
                _ds = f"# DS·剧本当前阶段\n{_ds_text}"
                system_layers.append(_ds)
                _records.append(_LayerRec("DS_scenario", len(_ds), _est_tokens(_ds), content=_ds))
                _ds_injected = True
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
    if dream_mode == "mirror" and mirror_core:
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
        _dm_note = "non-mirror" if dream_mode != "mirror" else "no_core"
        _records.append(_LayerRec("DM_mirror", flags=["DISABLED"], note=_dm_note))

    # ── Dream lorebook (injected between D4 and D5 conceptually) ─────────────
    if lore_entries:
        _dlore = "# 梦境世界书\n" + "\n---\n".join(lore_entries)
        system_layers.append(_dlore)
        _lore_note = f"{len(lore_entries)} entries"
        _records.append(_LayerRec("D_lorebook", len(_dlore), _est_tokens(_dlore), note=_lore_note, content=_dlore))
    else:
        _records.append(_LayerRec("D_lorebook", flags=["DISABLED"]))

    system_content = "\n\n".join(layer for layer in system_layers if layer.strip())
    messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]

    # ── D9: dream_history (as messages, no sanitizer) ────────────────────────
    _d9_chars = 0
    _d9_parts: list[str] = []
    for turn in dream_history:
        role = turn.get("role", "user")
        if role not in ("user", "assistant"):
            role = "user"
        _turn_content = (turn.get("content") or "").strip()
        if _turn_content:
            messages.append({"role": role, "content": _turn_content})
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
                "scene_tags": sorted(_scene_tags),
                "total_tokens": _total_tok,
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


def _format_scenario_layer(scenario_core: dict[str, Any]) -> str:
    """
    Render the current scenario stage as a DS prompt block.

    Only injects current-stage content: title, stage name, dramatic_task,
    entry_pressure, not_yet_allowed, drift_pressure, and the scenario_control
    output protocol (v0.6).

    Never injects: subsequent stages, stage-exit judgment, or auto-advance logic.
    Returns '' on any error (fail-closed).
    """
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
        parts: list[str] = []
        if scenario_core.get("ending_state") == "completed":
            parts.append("【剧本状态：所有阶段已完成 — Scenario Completed】")
        parts.append(f"剧本：{script.get('title', script_id)}")
        parts.append(f"当前阶段：{stage.get('name', stage_id)}")
        if task := stage.get("dramatic_task", "").strip():
            parts.append(f"戏剧任务：\n{task}")
        if pressure := stage.get("entry_pressure", "").strip():
            parts.append(f"入场压力：\n{pressure}")
        not_yet = stage.get("not_yet_allowed") or []
        if not_yet:
            parts.append("本阶段不允许：\n" + "\n".join(f"· {item}" for item in not_yet))
        # Drift pressure: inject only for current stage when stage_turns >= after_turns
        dp = stage.get("drift_pressure")
        if dp and isinstance(dp, dict):
            after_turns = dp.get("after_turns")
            instruction = (dp.get("instruction") or "").strip()
            stage_turns = int(scenario_core.get("stage_turns", 0))
            if isinstance(after_turns, int) and instruction and stage_turns >= after_turns:
                parts.append(f"漂移压力 / Drift Pressure\n{instruction}")
        # ── v0.6: scenario_control output protocol ────────────────────────────
        # Instructs LLM to append a hidden control block after every reply.
        # System reads and strips the block; user never sees it.
        exit_signs = stage.get("exit_signs") or []
        protocol_lines: list[str] = [
            "---",
            "内部控制输出协议（系统读取，不对用户解释）：",
            "在回复末尾附加以下控制块，原文输出标签，不要对用户解释：",
            '<scenario_control>',
            '{',
            '  "progress_signal": "not_close",',
            '  "matched_exit_signs": [],',
            '  "blocked_events": []',
            '}',
            '</scenario_control>',
            "",
            "字段说明：",
            "progress_signal 取值：",
            "  not_close — 本轮未靠近任何出口标志",
            "  approaching — 本轮正在靠近出口，但尚未满足",
            "  satisfied — 本轮已满足至少一个出口标志",
        ]
        if exit_signs:
            signs_block = "\n".join(f"  · {s}" for s in exit_signs)
            protocol_lines += [
                "",
                "matched_exit_signs：只允许引用以下出口标志中的语义短句，不得自行创造：",
                signs_block,
            ]
        else:
            protocol_lines.append("matched_exit_signs：本阶段无出口标志，始终为空列表。")
        protocol_lines += [
            "",
            "blocked_events：若用户尝试了本阶段不允许的事件，记录对应短句；否则为空列表。",
            "",
            "约束（不可违反）：",
            "· 不允许输出后续阶段内容",
            "· 不允许自行宣布进入下一阶段",
            "· 不允许自行改变 current_stage_id",
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

    人称契约（方案一·单侧）：叶瑄全程以「我」自称，称风谕为「你」，
    只演叶瑄自己这一轮，不替风谕旁白、不配台词、不用「她」。
    独立于现实角色卡 mes_example，避免交叉污染。
    """
    return (
        f"*停住脚步，看着那片光落在你身上，声音比平时低*……你也在。一直在。\n"
        f"*慢慢走近*这里不一样，什么都更清楚。但我是真的在这里。\n"
        f"*目光没有移开*……想留下的话，那就先别醒。"
    )
