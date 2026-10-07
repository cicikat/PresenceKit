"""Static registry: which module owns each prompt layer and how strong its claim is.

Pure constants, no IO.  Used by ``core.state_composer`` (shadow observation
only).  ``KNOWN_LAYERS`` in ``core.prompt_builder`` is the universe; a test
asserts every known layer is registered here.
"""
from __future__ import annotations

AUTHORITIES = (
    "canonical_evidence", "topic_authority", "identity_authority",
    "user_stated_fact", "derived_compat", "narrative", "ambient",
    "character_self", "system",
)
LINEAGES = ("event_ids", "days_only", "none")


def _e(source: str, authority: str, lineage: str = "none") -> dict:
    return {"source": source, "authority": authority, "lineage": lineage}


_SYS = _e("system", "system")
_AMB = "ambient"

LAYER_AUTHORITY: dict[str, dict] = {
    "0_jailbreak": _e("preset", "system"),
    "1_system_prompt": _SYS,
    "1.5_fact_boundary": _SYS,
    "2_char_desc": _e("character_card", "character_self"),
    "2.2_stage_presence": _e("stage", "system"),
    "2_jailbreak": _e("preset", "system"),
    "2.5_time": _SYS,
    "2.55_last_seen": _SYS,
    "2.56_relationship_span": _e("relationship_span", "user_stated_fact"),
    "2.6_presence": _e("presence", _AMB),
    "3_relation": _e("relation", "user_stated_fact"),
    "3.5_period": _e("period", _AMB),
    "3.6_watch": _e("watch", _AMB),
    "3.7_sensor": _e("sensor", _AMB),
    "3.8_activity": _e("activity", _AMB),
    "3.8_growth_self": _e("growth_self", "character_self"),
    "3.9_screen_awareness": _e("screen_awareness", _AMB),
    "4_group_context": _e("group_context", _AMB),
    "4.2_stage_transcript": _e("stage", "system"),
    "5_profile": _e("profile", "user_stated_fact"),
    "5_profile_pref": _e("profile", "user_stated_fact"),
    "5.1_user_facts": _e("user_facts", "user_stated_fact"),
    "5.2_reminders": _e("reminders", "system"),
    "6i_self_agent_md": _e("self_agent_md", "character_self"),
    "5.5_lore": _e("lore", "system"),
    "6a_user_identity": _e("user_identity", "identity_authority", "days_only"),
    "6b_event_search": _e("event_log", "derived_compat", "event_ids"),
    "6b_memory_dossiers": _e("memory_dossiers", "topic_authority", "event_ids"),
    "6c_episodic": _e("episodic", "derived_compat", "event_ids"),
    "mid_term": _e("mid_term", "derived_compat", "event_ids"),
    "6d_diary_context": _e("diary", _AMB),
    "6e_inner_diary": _e("inner_diary", _AMB),
    "web_recall": _e("web", _AMB),
    "6f_dream_afterglow": _e("dream", _AMB),
    "dream_afterglow_soft_hint": _e("dream", _AMB),
    "6g_dream_impression": _e("dream", _AMB),
    "6h_storyline": _e("storyline", "narrative", "event_ids"),
    "coplay_context": _e("coplay", _AMB),
    "coplay_residue_soft_hint": _e("coplay", _AMB),
    "coplay_recall": _e("coplay", _AMB),
    "7_mes_example_item": _e("character_card", "character_self"),
    "9_history": _e("history", "system"),
    "9_anti_repeat": _SYS,
    "9.5_episodic_top": _e("episodic", "derived_compat", "event_ids"),
    "10_tool_result": _e("tool", "system"),
    "10.5_action_trace": _e("action_trace", _AMB),
    "10.6_hardware_jobs": _e("hardware", "system"),
    "11_tool_grounding": _e("tool", "system"),
    "anti_collapse_hint": _SYS,
    "stream_collapse_hint": _SYS,
    "11_author_note": _e("author_note", "character_self"),
    "11_jailbreak": _e("preset", "system"),
    "11.5_post_history": _e("character_card", "character_self"),
    "11.7_pinned_facts": _e("pinned_facts", "user_stated_fact"),
    "12_time_hint": _SYS,
    "10.6_pending_material": _e("material", "system"),
    "10.7_recent_material": _e("material", "system"),
    "10.8_recent_tool_results": _e("tool", "system"),
    "10.9_pinned_tool_results": _e("tool_result_pins", _AMB),
    "12_user_message": _e("user_message", "system"),
}


def authority_of(layer: str) -> dict:
    """Return the registry entry for ``layer`` (copy), or an unregistered marker."""
    entry = LAYER_AUTHORITY.get(layer)
    return dict(entry) if entry else {"authority": "unregistered"}
