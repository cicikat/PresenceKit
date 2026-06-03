"""
core/reality_output_scrubber — reality context sanitizer (memory / prompt side).

Strips action descriptions, stage directions, and non-dialogue narration so that
they cannot pollute the next-turn prompt, short_term history, event_log, or any
downstream memory consolidation (mid_term → episodic → identity).

Correct call sites (memory / context path):
  - capture_turn          → short_term + event_log write
  - record_assistant_turn → memory_text passed to post_process
  - admin/routers/chat    → memory side of desktop_wake
  - main.py               → memory_reply passed to pipeline.post_process (QQ)

NOT for visible UI output.  Visible replies should use only strip_render_tags
(which removes XML/NMP tags) so that action descriptions remain for chat texture.

NOT imported by dream_pipeline — Dream content bypasses this module entirely.
Two Dream invariants held by construction:
  1. dream_pipeline.py never calls this module.
  2. This module is never called from any Dream route.
"""

import re

# ── Whole-line pattern matchers ───────────────────────────────────────────────

# 整行中文括号动作：（…）
_CJK_BRACKET_RE = re.compile(r"^（[^）\n]+）$")

# 整行英文括号动作：(…)
_EN_BRACKET_RE = re.compile(r"^\([^)\n]+\)$")

# 整行 Markdown do：*…*
_MD_DO_RE = re.compile(r"^\*[^*\n]+\*$")

# 整行 Markdown feel：_…_
_MD_FEEL_RE = re.compile(r"^_[^_\n]+_$")

# 整行 env 旁白：> …
_MD_ENV_RE = re.compile(r"^> .+$")

# 第三人称旁白 / 动作句开头
_NARRATION_START_RE = re.compile(
    r"^(他|她|动作|沉默|停顿|视线|目光|呼吸|微微|缓缓|轻轻|慢慢)"
)

# 明显动作词（行内出现即视为动作句，代码块内豁免）
# 注：`摸` 和 `抱` 作通用过滤保留，但 `抱歉` 通过负向前瞻排除
_ACTION_WORD_RE = re.compile(
    r"抬起|低头|靠近|尾巴|扫过|看你一眼|守着|趴|蹭|贴近|伸手|垂眸|眯眼"
    r"|摸"           # 轻摸/摸了/摸着等动作形式
    r"|抱(?!歉|怨|负)"  # 抱住/抱了/抱你 — 排除 抱歉/抱怨/抱负
)

# 代码块 fence
_CODE_FENCE_RE = re.compile(r"^```")


def scrub_reality_prompt_context_text(
    text: str | None,
    *,
    segments: list[dict] | None = None,
) -> str | None:
    """Alias for scrub_reality_output_text — use this name at prompt/memory call sites."""
    return scrub_reality_output_text(text, segments=segments)


def scrub_reality_output_text(
    text: str | None,
    *,
    segments: list[dict] | None = None,
) -> str | None:
    """
    Remove action / stage-direction content from a reality-chat reply.

    Segment path — when *segments* provided: keep only type=="say" texts,
                   then run line-based scrub on the joined result.
                   All other types (do / feel / env / narration / unknown)
                   are discarded.
    Line path    — when no *segments*: apply line-based scrub directly to
                   *text* without internal NMP parsing.

    Returns None when nothing survives — callers should:
      - Use "我在。" fallback for user-visible replies.
      - Skip memory writes for short_term / event_log.
    """
    if text is None:
        return None

    if segments is not None:
        say_parts = [s["text"] for s in segments if s.get("type") == "say"]
        if say_parts:
            text = "\n".join(say_parts)
        # No say segments → fall back to line scrub on original text as-is

    return _scrub_lines(text)


# ─────────────────────────────────────────────────────────────────────────────

def _scrub_lines(text: str) -> str | None:
    """Line-level filter.  Returns None when nothing non-empty remains."""
    lines = text.split("\n")
    kept: list[str] = []
    in_code_block = False

    for line in lines:
        s = line.strip()

        # Code fence: toggle protection state and always keep fence lines
        if _CODE_FENCE_RE.match(s):
            in_code_block = not in_code_block
            kept.append(line)
            continue

        # Inside code block: preserve unconditionally
        if in_code_block:
            kept.append(line)
            continue

        # Blank line: preserve for paragraph spacing
        if not s:
            kept.append(line)
            continue

        # Drop whole-line bracket / Markdown / env markers
        if (
            _CJK_BRACKET_RE.match(s)
            or _EN_BRACKET_RE.match(s)
            or _MD_DO_RE.match(s)
            or _MD_FEEL_RE.match(s)
            or _MD_ENV_RE.match(s)
        ):
            continue

        # Drop third-person narration starters
        if _NARRATION_START_RE.match(s):
            continue

        # Drop lines containing obvious physical action words
        if _ACTION_WORD_RE.search(s):
            continue

        kept.append(line)

    result = "\n".join(kept).strip()
    return result or None
