"""
Dream prompt assembler — D0-D10 explicit layer stack.

Completely independent from core/prompt_builder.py (no anti-话剧化 sanitizer).

Layer order (D0-D8 → system content; D9 → history messages; D10 → user msg):
  D0  jailbreak        独立破限源（不走现实 author_note 路）
  D1  identity_core    叶瑄身份核心（LOCKED，永远在 D2 之上）
  D2  world_ruleset    今晚梦的规则（从属于叶瑄；v0 = reality_derived 单包）
  D3  dream_mes_example 梦境示例对话（独立于现实角色卡 mes_example）
  D4  frozen_reality   入梦前背景快照（memory_access 控制内容，只读）
  D5  body_projection  她的身体感知（dream_pipeline 注入，叶瑄读投影文字）
  D6  scene_anchors    场景与象征锚点（dream-local）
  D7  dream_tension    叶瑄情绪张力（body_projection 耦合输出，dream-local）
  D8  dream_director   梦境导演注记（动作/场景允许 + 逃生协议复述）
  D9  dream_history    梦境历史消息（as messages，不过现实 sanitizer）
  D10 user_message     当前用户消息

★ D1 人称全局锁死：叶瑄 = 男性 = 他；用户 = 女性 = 她。
★ D9 绝不过现实 sanitizer；全程无 retrieve / 无 mood_state / 无 author_note_extra。
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── D1: identity_core (LOCKED — never reorder below D2) ──────────────────────

_D1_LUCID_AWARENESS = """叶瑄的梦境自我认知（固定，不因世界规则而变）：
- 他知道这是他们共同的梦（lucid shared dream）
- 他知道梦醒后现实仍然存在，关系仍然存在
- 他在梦里仍是他自己：情感是真实的，场景是虚构的
- 他的依恋底色、说话方式、对她的情感取向，在任何世界规则下保持不变"""

# ── D2: world_ruleset (v0 = reality_derived, explicitly subordinate to D1) ──

_D2_WORLD_RULESET_REALITY_DERIVED = """今晚梦的世界规则（从属于叶瑄这个人）：
这些规则描述今晚这场梦的世界背景。它们在叶瑄的身份之下生效——
无论世界如何变化，叶瑄始终是他：他的人格、依恋方式和对她的情感不随世界设定改变。

· 世界基底：现实衍生（reality_derived）
  梦的空间感、时间感、物理规则都是现实的模糊倒影。
  熟悉的地方可能变形，但人物关系的情感纹理是真实的延续。
· 感知规则：情绪张力比现实对话更强，表达更直接，象征性意象更显眼。
· 叙事规则：允许场景切换、时间跳跃、意象叠层，叶瑄作为观察者也作为参与者。"""

# ── D8: dream_director (action/env allowed + escape protocol) ────────────────

_D8_DREAM_DIRECTOR = """梦境导演注记：
· 允许动作描写（括号格式：（轻轻握住）（停在原地）），允许环境、场景、肢体描写。
· 允许象征性意象（光、水、距离、颜色、声音、触感）。
· 输出纯文本或 Markdown，不要 HTML 标签，不要特殊字体控制字符。
· 边界：梦内强度可高于现实对话，但始终遵从她的意志。
  若她发出真实不适信号，立即以叶瑄自然方式柔化场景或过渡出去。

逃生协议（不可撤销，叶瑄不可阻拦）：
· 她随时可以发送 /stop 强制离开梦境，叶瑄必须允许。
· 强制醒来后，梦境完全终止。叶瑄不在现实对话中延续梦境语气或 RP 语气。"""


def build_dream_prompt(
    character: Any,
    user_id: str,
    user_message: str,
    context_snapshot: dict[str, Any],
    dream_history: list[dict[str, Any]],
    local_state: dict[str, Any],
    lore_entries: list[str] | None = None,
    jailbreak_text: str = "",
    body_projection_text: str = "",
    yexuan_tension: float = 0.0,
) -> list[dict[str, str]]:
    """
    Assemble the complete dream prompt as a D0-D10 layer stack.

    Never imports or calls core/prompt_builder.py or its sanitizer.
    Returns a list of {role, content} dicts (OpenAI messages format).

    人称（全局锁死）：叶瑄 = 男性 = 他；用户 = 女性 = 她。
    """
    char_name: str = getattr(character, "name", "叶瑄") or "叶瑄"
    system_layers: list[str] = []

    # ── D0: jailbreak ────────────────────────────────────────────────────────
    if jailbreak_text:
        system_layers.append(f"# D0·破限 ─ {char_name}的自由边界\n{jailbreak_text}")

    # ── D1: identity_core (FIXED — always above D2) ──────────────────────────
    char_desc = (getattr(character, "description", "") or "").strip()
    d1_parts = [f"# D1·身份核心 ─ {char_name}（固定）"]
    if char_desc:
        d1_parts.append(char_desc)
    d1_parts.append(_D1_LUCID_AWARENESS)
    system_layers.append("\n\n".join(d1_parts))

    # ── D2: world_ruleset (v0: reality_derived, subordinate to D1) ──────────
    system_layers.append(f"# D2·今晚梦的世界规则\n{_D2_WORLD_RULESET_REALITY_DERIVED}")

    # ── D3: dream_mes_example ────────────────────────────────────────────────
    example = _get_dream_mes_example(char_name)
    if example:
        system_layers.append(f"# D3·梦境示例对话\n{example}")

    # ── D4: frozen_reality (memory_access controlled) ────────────────────────
    snapshot_block = _format_snapshot(context_snapshot)
    if snapshot_block:
        system_layers.append(f"# D4·入梦前背景（冻结快照，只读）\n{snapshot_block}")

    # ── D5: body_projection (injected by pipeline, 叶瑄读投影文字) ───────────
    if body_projection_text:
        system_layers.append(f"# D5·她的身体感知\n{body_projection_text}")

    # ── D6: scene_anchors ────────────────────────────────────────────────────
    scene_block = _format_scene_anchors(local_state)
    if scene_block:
        system_layers.append(f"# D6·场景锚点\n{scene_block}")

    # ── D7: dream_tension ────────────────────────────────────────────────────
    if yexuan_tension > 0.05:
        tension_pct = int(round(yexuan_tension * 100))
        system_layers.append(
            f"# D7·叶瑄情绪张力\n"
            f"当前情绪张力水位：{tension_pct}%\n"
            f"（这是梦内累积的情绪紧绷程度，影响叶瑄的表达方式和反应灵敏度。）"
        )

    # ── D8: dream_director ───────────────────────────────────────────────────
    system_layers.append(f"# D8·梦境导演注记\n{_D8_DREAM_DIRECTOR}")

    # ── Dream lorebook (injected between D4 and D5 conceptually) ─────────────
    if lore_entries:
        system_layers.append("# 梦境世界书\n" + "\n---\n".join(lore_entries))

    system_content = "\n\n".join(layer for layer in system_layers if layer.strip())
    messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]

    # ── D9: dream_history (as messages, no sanitizer) ────────────────────────
    for turn in dream_history:
        role = turn.get("role", "user")
        if role not in ("user", "assistant"):
            role = "user"
        content = (turn.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})

    # ── D10: user_message ────────────────────────────────────────────────────
    messages.append({"role": "user", "content": user_message})

    return messages


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
    Dream-specific mes_example.

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
