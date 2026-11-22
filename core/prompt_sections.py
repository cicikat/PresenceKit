"""Presentation-only source sections; leaf policy and receipts stay intact."""

import re

from core.prompt_language import text as _pt

SECTIONS = (
    ("真实数据", "按各项记录时间与有效期理解；设备观测不代表用户意图或内心状态。", {
        "2.5_time", "2.55_last_seen", "3.5_period", "3.6_watch", "3.7_sensor",
        "3.75_heart_rate", "3.9_screen_awareness", "3.8_audio_impression", "activity_continuity", "coplay_context",
        "10.6_hardware_jobs",
    }),
    ("用户事实", "区分用户自述、已记录事项与行为归纳；归纳不是本轮事实。", {
        "5_profile", "5_profile_pref", "5.1_user_facts", "5.2_food_tastes",
        "5.2_reminders", "6a_user_identity", "11.7_pinned_facts",
        "2.56_relationship_span",
    }),
    ("现实回忆", "以下是历史记录与回忆线索，不是此刻正在发生的事；日记感受与叙事归纳不等于客观事实。需要细节或出现冲突时，先用expand_event_window按所附event_id回读原始证据，get_related_events查关联事件；dossier_id用read_memory_dossier/search_dossier_events，长消息序号用read_long_user_message。未附可用ID时按关键词和时间检索，不猜造ID；工具以本轮实际暴露为准。", {
        "6b_event_search", "6b_memory_dossiers", "6c_episodic", "mid_term",
        "6d_diary_context", "6e_inner_diary", "6h_storyline",
        "9.5_episodic_top", "coplay_recall",
    }),
    ("参考资料", "", {
        "web_recall",
    }),
    ("工具结果", "保留各次返回的时间和状态；历史快照与动作痕迹不证明当前状态，失败或已受理不等于完成。", {
        "1.4_perception", "10_tool_result", "10.5_action_trace", "10.8_recent_tool_results",
        "10.9_pinned_tool_results",
    }),
    ("主观余韵", "以下仅是主观表达或梦境余韵，不作为现实事件证据。", {
        "expression_afterglow", "6f_dream_afterglow", "dream_afterglow_soft_hint",
        "6g_dream_impression", "coplay_residue_soft_hint", "1.6_drinking",
    }),
)


def character_description(character, *, max_field_chars: int | None = None) -> str:
    parts = (
        ("【角色描述】\n", character.description),
        ("【性格】\n", character.personality),
        ("【当前情境】\n", character.scenario),
    )
    return "\n\n".join(_pt(label) + value[:max_field_chars] for label, value in parts if value)


def arrange_sections(messages: list[dict]) -> list[dict]:
    """Return copies, with contiguous sections before examples/history.

    Called on unwrapped leaf messages after ablation and again during pruning.
    No protocol messages or unknown layers are moved into data sections.
    """
    grouped_layers = set().union(*(layers for _, _, layers in SECTIONS))
    remaining = [dict(m) for m in messages
                 if m.get("role") != "system" or m.get("_layer") not in grouped_layers]
    boundary = next((m for m in remaining if m.get("_layer") == "1.5_fact_boundary"), None)
    if boundary is not None:
        remaining.remove(boundary)
        identity_index = next((i for i, m in enumerate(remaining)
                               if m.get("_layer") == "1_system_prompt"), 0)
        remaining.insert(identity_index, boundary)
    blocks = []
    for name, guidance, layers in SECTIONS:
        name, guidance = _pt(name), _pt(guidance)
        leaves = [dict(m) for m in messages
                  if m.get("role") == "system" and m.get("_layer") in layers]
        if not leaves:
            continue
        leaves[0]["content"] = f"【{name}】\n{guidance}\n" + leaves[0]["content"]
        leaves[-1]["content"] += f"\n【/{name}】"
        blocks.extend(leaves)
    anchor = next((i for i, m in enumerate(remaining)
                   if m.get("_layer") in {"7_mes_example_item", "9_history", "12_user_message"}),
                  len(remaining))
    arranged = remaining[:anchor] + blocks + remaining[anchor:]
    materials = [m for m in arranged if m.get('_layer') in {'10.6_pending_material', '10.7_recent_material'}]
    if materials:
        arranged = [m for m in arranged if m not in materials]
        history = next((i for i, m in enumerate(arranged) if m.get('_layer') in {'9_history', '12_user_message'}), len(arranged))
        materials[0]['content'] = _pt('【对话历史】\n') + materials[0]['content']
        materials[-1]['content'] += _pt('\n【/对话历史】')
        arranged[history:history] = materials
    return arranged


_INNER_DIARY_FEELING_MARKER = re.compile(r"(?m)^##\s*今日感受\s*$")
_INNER_DIARY_TODAY_HEADING = re.compile(r"^(#{0,6}\s*)今日(事件|感受)\s*$")


def _split_inner_diary_layers(diary_text: str) -> tuple[str, str]:
    """Split on-disk inner diary into facts/feeling. File headings stay as written."""
    text = (diary_text or "").strip()
    if not text:
        return "", ""
    marker = _INNER_DIARY_FEELING_MARKER.search(text)
    if marker:
        return text[:marker.start()].strip(), text[marker.end():].strip()
    if re.search(r"(?m)^##\s*今日事件\s*$", text):
        return text, ""
    return "", text


def _project_inner_diary_excerpt(text: str, limit: int) -> str:
    """Rewrite today's file headings for yesterday's prompt projection."""
    if not text:
        return ""
    lines: list[str] = []
    for line in text.splitlines():
        match = _INNER_DIARY_TODAY_HEADING.match(line.strip())
        if match:
            lines.append(f"{match.group(1)}昨日{match.group(2)}")
            continue
        lines.append(line)
    return "\n".join(lines).strip()[:limit]


def _recent_openings(history: list[dict], n: int = 3, k: int = 8) -> list[str]:
    """Return the first k chars of the last n assistant turns (fail-open)."""
    outs: list[str] = []
    for turn in reversed(history):
        if turn.get("role") != "assistant":
            continue
        head = str(turn.get("content") or "").lstrip().replace("\n", "")[:k]
        if head:
            outs.append(head)
        if len(outs) >= n:
            break
    return outs


def _dedupe_filler_prefix_history(history: list[dict], prefix: str) -> list[dict]:
    """
    历史投影去同质（问题7 (a)）：只改注入 prompt 的历史副本，绝不写回 short_term 存储。

    近场 assistant 回复连续以填充词前缀 P 开头时，模型会从上下文里"学到"每句都要 P 开头——
    一条软提示对抗不了整段历史的示范效应。这里保留最早一条完整的 P，其余各条剥掉开头的 P，
    使投影给模型看的历史里不再是"每句都嗯。"。

    被剥掉前缀的消息额外带 `_raw_content` 记录原始文本（未去同质），供 pipeline 侧输出端
    校验重试（问题7 (c)）复原同一份检测所需的原始 assistant 历史；`_raw_content` 与 `_layer` 等
    内部字段一样在 `sanitize_messages()` 出口统一剥离，不会发给供应商，也不写回磁盘。
    """
    projected: list[dict] = []
    kept_one = False
    for msg in history:
        content = msg.get("content")
        if (
            msg.get("role") == "assistant"
            and isinstance(content, str)
            and content.lstrip().startswith(prefix)
        ):
            if not kept_one:
                kept_one = True
                projected.append(msg)
            else:
                lstripped = content.lstrip()
                leading_ws = content[: len(content) - len(lstripped)]
                new_msg = dict(msg)
                new_msg["content"] = leading_ws + lstripped[len(prefix):]
                new_msg["_raw_content"] = content
                projected.append(new_msg)
        else:
            projected.append(msg)
    return projected
