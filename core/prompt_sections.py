"""Presentation-only source sections; leaf policy and receipts stay intact."""

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
        materials[0]['content'] = '【对话历史】\n' + materials[0]['content']
        materials[-1]['content'] += '\n【/对话历史】'
        arranged[history:history] = materials
    return arranged
