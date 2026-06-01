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

★ D1 人称全局锁死：叶瑄 = 男性 = 他；用户 = 女性 = 她。
★ D2/D3 从 world_loader 加载，世界在入梦时从 dream_state.frozen_world 读取并冻结。
★ D9 绝不过现实 sanitizer；全程无 retrieve / 无 mood_state / 无 author_note_extra。
"""

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)
_dream_token_logger = logging.getLogger("dream_prompt.token")

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

_D1_LUCID_AWARENESS = """叶瑄的梦境自我认知（固定，不因世界规则而变）：
- 他知道这是他们共同的梦（lucid shared dream）
- 他知道梦醒后现实仍然存在，关系仍然存在
- 他在梦里仍是他自己：情感是真实的，场景是虚构的
- 他的情感底色、说话方式、对她的情感取向，在任何世界规则下保持不变"""

_D1_NON_LUCID_AWARENESS = """叶瑄的梦境自我认知（non_lucid 模式）：
- 在这段体验里，叶瑄沉浸当下，不刻意点破"这是梦"
- 他的情感底色、说话方式、对她的情感取向，在任何世界规则下保持不变
- 他仍是他自己：情感是真实的，只是不用"梦"这个词来框定当下"""

# ── D8: dream_director (action/env allowed + escape protocol) ────────────────

_D8_DREAM_DIRECTOR = """梦境导演注记：
· 说出口的话直接写，不加任何标记。
· 动作描写独占一行，用单星号包裹：*轻轻握住* *停在原地* *慢慢走近*。
· 环境/氛围描写独占一行，用引用符号：> 夜色很低，窗外的风声轻轻压下来。
· 内心感受独占一行，用下划线包裹：_某种平静在内部落定。_
· 允许象征性意象（光、水、距离、颜色、声音、触感）。
· 不要 HTML 标签，不要特殊字体控制字符。
· 边界：梦内强度可高于现实对话，但始终遵从她的意志。
  若她发出真实不适信号，立即以叶瑄自然方式柔化场景或过渡出去。

逃生协议（不可撤销，叶瑄不可阻拦）：
· 她随时可以发送 /stop 强制离开梦境，叶瑄必须允许。
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
  若她发出真实不适信号，立即以叶瑄自然方式柔化场景或过渡出去。"""


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
) -> list[dict[str, str]]:
    """
    Assemble the complete dream prompt as a D0-D10 layer stack.

    Never imports or calls core/prompt_builder.py or its sanitizer.
    Returns a list of {role, content} dicts (OpenAI messages format).

    world_id: frozen at dream entry from dream_state.frozen_world.
    D2/D3 are loaded from the world package data files.

    人称（全局锁死）：叶瑄 = 男性 = 他；用户 = 女性 = 她。

    lore_meta: optional per-entry metadata from lore engine
               [{keywords, insertion_order, ...}, ...] — purely for observability logging.
    debug: if True, forces dream_prompt.token log to be emitted at INFO even if logger is
           otherwise filtered; also logs the final assembled system message.
    """
    from core.dream.world_loader import load_world
    world = load_world(world_id)

    char_name: str = getattr(character, "name", "叶瑄") or "叶瑄"
    system_layers: list[str] = []
    _records: list[_LayerRec] = []

    # ── D0: jailbreak ────────────────────────────────────────────────────────
    _d0_note = f"preset={jailbreak_preset_name}"
    if jailbreak_text:
        _d0 = f"# D0·破限 ─ {char_name}的自由边界\n{jailbreak_text}"
        system_layers.append(_d0)
        _d0_flags = [jailbreak_preset_status.upper()] if jailbreak_preset_status else []
        _records.append(_LayerRec("D0_jailbreak", len(_d0), _est_tokens(_d0), flags=_d0_flags, note=_d0_note))
    else:
        _records.append(_LayerRec("D0_jailbreak", flags=["DISABLED"], note=_d0_note))

    # ── D1: identity_core (FIXED — always above D2) ──────────────────────────
    char_desc = (getattr(character, "description", "") or "").strip()
    d1_parts = [f"# D1·身份核心 ─ {char_name}（固定）"]
    if char_desc:
        d1_parts.append(char_desc)
    d1_parts.append(
        _D1_NON_LUCID_AWARENESS if lucid_mode == "non_lucid" else _D1_LUCID_AWARENESS
    )
    _d1 = "\n\n".join(d1_parts)
    system_layers.append(_d1)
    _records.append(_LayerRec("D1_identity_core", len(_d1), _est_tokens(_d1)))

    # ── D2: world_ruleset (loaded from world package, subordinate to D1) ─────
    if world.ruleset:
        _d2 = f"# D2·今晚梦的世界规则\n{world.ruleset}"
        system_layers.append(_d2)
        _records.append(_LayerRec("D2_world_ruleset", len(_d2), _est_tokens(_d2)))
    else:
        _records.append(_LayerRec("D2_world_ruleset", flags=["DISABLED"]))

    # ── D3: dream_mes_example (loaded from world package) ────────────────────
    _mes_from_fallback = not bool(world.mes_example)
    example = world.mes_example or _get_dream_mes_example(char_name)
    if example:
        _d3 = f"# D3·梦境示例对话\n{example}"
        system_layers.append(_d3)
        _d3_flags = ["FALLBACK"] if _mes_from_fallback else []
        _records.append(_LayerRec("D3_mes_example", len(_d3), _est_tokens(_d3), _d3_flags))
    else:
        _records.append(_LayerRec("D3_mes_example", flags=["DISABLED"]))

    # ── D4: frozen_reality (memory_access controlled) ────────────────────────
    snapshot_block = _format_snapshot(context_snapshot)
    if snapshot_block:
        _d4 = f"# D4·入梦前背景（冻结快照，只读）\n{snapshot_block}"
        system_layers.append(_d4)
        _records.append(_LayerRec("D4_frozen_reality", len(_d4), _est_tokens(_d4)))
    else:
        _records.append(_LayerRec("D4_frozen_reality", flags=["DISABLED"]))

    # ── D5: body_projection (injected by pipeline, 叶瑄读投影文字) ───────────
    if body_projection_text:
        _d5 = f"# D5·她的身体感知\n{body_projection_text}"
        system_layers.append(_d5)
        _records.append(_LayerRec("D5_body_projection", len(_d5), _est_tokens(_d5)))
    else:
        _records.append(_LayerRec("D5_body_projection", flags=["DISABLED"]))

    # ── D6: scene_anchors ────────────────────────────────────────────────────
    scene_block = _format_scene_anchors(local_state)
    if scene_block:
        _d6 = f"# D6·场景锚点\n{scene_block}"
        system_layers.append(_d6)
        _records.append(_LayerRec("D6_scene_anchors", len(_d6), _est_tokens(_d6)))
    else:
        _records.append(_LayerRec("D6_scene_anchors", flags=["DISABLED"]))

    # ── D7: dream_tension ────────────────────────────────────────────────────
    if yexuan_tension > 0.05:
        tension_pct = int(round(yexuan_tension * 100))
        _d7 = (
            f"# D7·叶瑄情绪张力\n"
            f"当前情绪张力水位：{tension_pct}%\n"
            f"（这是梦内累积的情绪紧绷程度，影响叶瑄的表达方式和反应灵敏度。）"
        )
        system_layers.append(_d7)
        _records.append(_LayerRec("D7_dream_tension", len(_d7), _est_tokens(_d7)))
    else:
        _records.append(_LayerRec("D7_dream_tension", flags=["DISABLED"]))

    # ── D8: dream_director ───────────────────────────────────────────────────
    _d8_text = _D8_DREAM_DIRECTOR_NON_LUCID if lucid_mode == "non_lucid" else _D8_DREAM_DIRECTOR
    _d8 = f"# D8·梦境导演注记\n{_d8_text}"
    system_layers.append(_d8)
    _records.append(_LayerRec("D8_dream_director", len(_d8), _est_tokens(_d8)))

    # ── Dream lorebook (injected between D4 and D5 conceptually) ─────────────
    if lore_entries:
        _dlore = "# 梦境世界书\n" + "\n---\n".join(lore_entries)
        system_layers.append(_dlore)
        _lore_note = f"{len(lore_entries)} entries"
        _records.append(_LayerRec("D_lorebook", len(_dlore), _est_tokens(_dlore), note=_lore_note))
    else:
        _records.append(_LayerRec("D_lorebook", flags=["DISABLED"]))

    system_content = "\n\n".join(layer for layer in system_layers if layer.strip())
    messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]

    # ── D9: dream_history (as messages, no sanitizer) ────────────────────────
    _d9_chars = 0
    for turn in dream_history:
        role = turn.get("role", "user")
        if role not in ("user", "assistant"):
            role = "user"
        content = (turn.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})
            _d9_chars += len(content)
    _d9_toks = max(1, _d9_chars // _TOK_RATIO) if _d9_chars else 0
    _records.append(
        _LayerRec("D9_dream_history", _d9_chars, _d9_toks, note=f"{len(dream_history)} turns")
    )

    # ── D10: user_message ────────────────────────────────────────────────────
    messages.append({"role": "user", "content": user_message})
    _records.append(_LayerRec("D10_user_message", len(user_message), _est_tokens(user_message)))

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


# ── Internal formatters ───────────────────────────────────────────────────────


def _format_snapshot(snapshot: dict[str, Any]) -> str:
    parts: list[str] = []
    if r := snapshot.get("recent_reality_context"):
        parts.append(f"最近现实对话摘要：\n{r}")
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


def _get_dream_mes_example(char_name: str) -> str:
    """
    Fallback dream mes_example when world package file is missing.
    Preferred path: world.mes_example loaded from world package data file.

    人称锁定：叶瑄用"他/我"，指称用户用"她/你"。
    独立于现实角色卡 mes_example，避免交叉污染。
    """
    return (
        f"她：（走进那片光里，转头看他）你也在。\n"
        f"{char_name}：（停住脚步，看着眼前的光落在她身上）……嗯。一直在。\n"
        f"（慢慢走近，声音比平时低）这里不一样。什么都更清楚——但我不知道是好事还是坏事。\n"
        f"她：这是梦吗？\n"
        f"{char_name}：（轻轻笑了一下）是。但我是真的在这里。\n"
        f"她：（靠近了一步）我不想醒。\n"
        f"{char_name}：（沉默了一会，目光没有移开）……那就先别醒。"
    )
