"""
core/reality_output_guard — shared reality output guard for all channels.

Applies basic AI-side output filters that are channel-agnostic:
  1. Remove <tool_call>…</tool_call> residue tags (xml_fallback artefacts)
  2. Remove character-name prefix  (角色：, 角色:, [角色], 角色 说：)
  3. Filter AI self-disclosure sentences (作为一个AI…)
  4. Optionally enforce paragraph breaks on long single-paragraph replies

Design constraints:
  - Pure text in → text out.  No QQ, no channels, no memory, no Dream.
  - Does NOT split into multiple messages (QQ-only concern).
  - Does NOT scrub action/narration descriptions (reality_output_scrubber.py).
  - Does NOT strip render tags (response_processor.strip_render_tags).
  - Does NOT touch dream_pipeline — Dream content bypasses this module entirely.

Call sites:
  - admin/routers/chat.py  run_owner_chat_turn()   (desktop + mobile)

QQ path (main.py) continues to call response_processor.process() which
applies the same three filters internally — no logic is duplicated.
"""

from core.response_processor import (
    _remove_tool_call_tags,
    _remove_character_prefix,
    _filter_self_censor,
)
from core.output.segment_enforcer import (
    enforce_paragraph_breaks,
    get_segment_enforce_settings,
)


def _clean_reality_reply_text(
    text: str,
    character_name: str | None = None,
    *,
    enforce_segments: bool = True,
) -> str:
    """
    Apply shared output guard to a raw LLM reply.

    Steps applied in order:
      1. Strip <tool_call>…</tool_call> residue tags.
      2. Strip character-name prefix (only when character_name is provided).
      3. Filter AI self-disclosure sentences.
      4. Optionally add a paragraph break to the outgoing copy.

    Returns the cleaned text (may be empty string when the entire reply was
    filtered — caller should treat this as a generation failure).

    Safe to call with empty or None-like text (returns the input unchanged).
    Does not raise.
    """
    if not text:
        return text
    try:
        from core.control_markers import strip_control_markers
        text = strip_control_markers(text)
        text = _remove_tool_call_tags(text)
        if character_name:
            text = _remove_character_prefix(text, character_name)
        text = _filter_self_censor(text)
        text = text.strip()
        segment_enabled, segment_min_len = get_segment_enforce_settings()
        if enforce_segments and segment_enabled:
            text = enforce_paragraph_breaks(text, min_len=segment_min_len)
        return text
    except Exception:
        # Guard must never crash the caller — return original on unexpected error.
        return text


def clean_reality_reply_text(
    text: str,
    character_name: str | None = None,
) -> str:
    """Apply the shared guard to the visible copy, including configured enforcement."""
    return _clean_reality_reply_text(
        text,
        character_name,
        enforce_segments=True,
    )


def clean_reality_reply_text_for_memory(
    text: str,
    character_name: str | None = None,
) -> str:
    """Apply reality cleanup while preserving the model's original paragraph shape."""
    return _clean_reality_reply_text(
        text,
        character_name,
        enforce_segments=False,
    )
