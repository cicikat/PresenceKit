"""
Dream prompt assembler — completely independent from core/prompt_builder.py.

Why not reuse prompt_builder:
  prompt_builder carries an anti-话剧化 sanitizer that strips action/environment
  descriptions, which directly conflicts with dream RP requirements.

Dream prompt structure:
  1. Character card base (description only, no sanitizer)
  2. Dream boundary + lucid-shared awareness
  3. Dream RP rules (actions / environment / scene allowed)
  4. Jailbreak section (independent source)
  5. Frozen context snapshot (read-only, assembled at entry)
  6. Dream lorebook (controlled by dream_settings)
  7. Dream-local emotional / scene state
  8. Dream mes_example (separate from reality character card examples)
  9. Dream history (from current_dream.jsonl, role-mapped)
  10. Current user message

Output: clean text/markdown only — no raw HTML, no special font tags.
Fancy styling is the renderer's job (dream window theme/CSS).
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

_DREAM_BOUNDARY_BLOCK = """【梦境模式】
当前是叶瑄与用户的共同梦境（lucid shared dream）。
叶瑄知道这是梦，也知道梦醒后现实仍在，不需要掩饰这一点。
梦境里允许更强的情绪表达、肢体动作和场景描写。
输出纯文本或 Markdown，不要输出 HTML 标签或特殊字体控制字符。
边界：dream_only。梦内一切事件不构成现实记忆。"""

_LUCID_AWARENESS_BLOCK = """叶瑄的梦境自我认知：
- 他知道这是他们共同的梦
- 他知道梦醒后现实仍然存在，关系仍然存在
- 他在梦里仍是他自己，情感是真实的，但场景是虚构的"""

_DREAM_RP_GUIDANCE_BLOCK = """梦境 RP 规则：
- 允许动作描写（用括号表示，如（轻轻握住对方的手））
- 允许环境与场景描写
- 情绪张力比现实对话更强，表达更直接，情绪映射权重更大
- 允许象征性意象（光、水、距离、颜色、声音）
- 不输出 raw HTML；花哨样式交给渲染层"""


def build_dream_prompt(
    character: Any,
    user_id: str,
    user_message: str,
    context_snapshot: dict[str, Any],
    dream_history: list[dict[str, Any]],
    local_state: dict[str, Any],
    lore_entries: list[str] | None = None,
    jailbreak_text: str = "",
) -> list[dict[str, str]]:
    """
    Assemble the complete dream prompt in OpenAI messages format.

    Never imports or calls core/prompt_builder.py or its sanitizer.
    Returns a list of {role, content} dicts.
    """
    char_name: str = getattr(character, "name", "叶瑄") or "叶瑄"
    system_parts: list[str] = []

    # 1. Character card base
    char_desc = (getattr(character, "description", "") or "").strip()
    if char_desc:
        system_parts.append(f"# 角色：{char_name}\n{char_desc}")

    # 2. Dream boundary & lucid awareness
    system_parts.append(_DREAM_BOUNDARY_BLOCK)
    system_parts.append(_LUCID_AWARENESS_BLOCK)
    system_parts.append(_DREAM_RP_GUIDANCE_BLOCK)

    # 3. Jailbreak section (independent — not the reality author_note route)
    if jailbreak_text:
        system_parts.append(f"# 破限指令\n{jailbreak_text}")

    # 4. Frozen context snapshot
    snapshot_block = _format_snapshot(context_snapshot)
    if snapshot_block:
        system_parts.append(f"# 入梦前背景（冻结快照，只读）\n{snapshot_block}")

    # 5. Dream lorebook
    if lore_entries:
        system_parts.append("# 梦境世界书\n" + "\n---\n".join(lore_entries))

    # 6. Dream-local emotional / scene state
    local_block = _format_local_state(local_state)
    if local_block:
        system_parts.append(f"# 梦境当前状态\n{local_block}")

    # 7. Dream mes_example (separate from reality character card)
    example = _get_dream_mes_example(char_name)
    if example:
        system_parts.append(f"# 梦境示例对话\n{example}")

    system_content = "\n\n".join(p for p in system_parts if p.strip())
    messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]

    # 8. Dream history
    for turn in dream_history:
        role = turn.get("role", "user")
        if role not in ("user", "assistant"):
            role = "user"
        content = (turn.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})

    # 9. Current user message
    messages.append({"role": "user", "content": user_message})

    return messages


def _format_snapshot(snapshot: dict[str, Any]) -> str:
    parts: list[str] = []
    if r := snapshot.get("recent_reality_context"):
        parts.append(f"最近现实对话摘要：\n{r}")
    if e := snapshot.get("episodic_summary"):
        parts.append(f"记忆片段：\n{e}")
    if m := snapshot.get("mid_term_context"):
        parts.append(f"近期互动背景：\n{m}")
    if p := snapshot.get("profile_impression"):
        parts.append(f"用户印象：{p}")
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


def _format_local_state(local_state: dict[str, Any]) -> str:
    parts: list[str] = []
    tension = float(local_state.get("emotional_tension") or 0.0)
    if tension > 0.0:
        parts.append(f"情绪张力：{tension:.2f}")
    if scene := local_state.get("scene_state"):
        parts.append(f"当前场景：{scene}")
    anchors = local_state.get("symbolic_anchors") or []
    if anchors:
        parts.append(f"象征锚点：{', '.join(str(a) for a in anchors)}")
    return "；".join(parts)


def _get_dream_mes_example(char_name: str) -> str:
    """
    Dream-specific mes_example.

    Demonstrates dream RP style: stronger emotion, action descriptions,
    symbolic imagery. Intentionally separate from reality character card
    mes_example to avoid cross-contamination.
    """
    return (
        f"用户：（走进那片光里，转头看他）你也在。\n"
        f"{char_name}：（停住脚步，看着眼前的光落在你身上）……嗯。一直在。\n"
        f"（慢慢走近，声音比平时低）这里不一样。什么都更清楚——但我不知道是好事还是坏事。\n"
        f"用户：这是梦吗？\n"
        f"{char_name}：（轻轻笑了一下）是。但我是真的在这里。\n"
        f"用户：（靠近了一步）我不想醒。\n"
        f"{char_name}：（沉默了一会，目光没有移开）……那就先别醒。"
    )
