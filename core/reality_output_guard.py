"""
core/reality_output_guard — shared reality output guard for all channels.

Applies basic AI-side output filters that are channel-agnostic:
  1. Remove <tool_call>…</tool_call> residue tags (xml_fallback artefacts)
  2. Remove character-name prefix  (叶瑄：, 叶瑄:, [叶瑄], 叶瑄 说：)
  3. Filter AI self-disclosure sentences (作为一个AI…)

Design constraints:
  - Pure text in → text out.  No QQ, no channels, no memory, no Dream.
  - Does NOT split into segments (QQ-only concern).
  - Does NOT scrub action/narration descriptions (reality_output_scrubber.py).
  - Does NOT strip render tags (response_processor.strip_render_tags).
  - Does NOT touch dream_pipeline — Dream content bypasses this module entirely.

Call sites:
  - admin/routers/chat.py  run_owner_chat_turn()   (desktop + mobile)
  - admin/routers/chat.py  desktop_wake Path B

QQ path (main.py) continues to call response_processor.process() which
applies the same three filters internally — no logic is duplicated.
"""

from core.response_processor import (
    _remove_tool_call_tags,
    _remove_character_prefix,
    _filter_self_censor,
)


def clean_reality_reply_text(
    text: str,
    character_name: str | None = None,
) -> str:
    """
    Apply shared output guard to a raw LLM reply.

    Steps applied in order:
      1. Strip <tool_call>…</tool_call> residue tags.
      2. Strip character-name prefix (only when character_name is provided).
      3. Filter AI self-disclosure sentences.

    Returns the cleaned text (may be empty string when the entire reply was
    filtered — caller should treat this as a generation failure).

    Safe to call with empty or None-like text (returns the input unchanged).
    Does not raise.
    """
    if not text:
        return text
    try:
        text = _remove_tool_call_tags(text)
        if character_name:
            text = _remove_character_prefix(text, character_name)
        text = _filter_self_censor(text)
        return text.strip()
    except Exception:
        # Guard must never crash the caller — return original on unexpected error.
        return text
