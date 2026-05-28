"""
Dream afterglow loader — reality-side only outlet for dream residue.

Reads from dreams/summaries/*.summary.json.
Injects a short-TTL prompt layer framed as "共有梦的余韵".

Contract:
- Independent loader; never read by reflect / consolidate / retrieve /
  any reality memory loader.
- Does NOT touch mood_state (no mood nudge — mood coherence via text only).
- Short TTL (default 8h) matching "短暂更柔/别扭/失落" emotional window.
- Layer prompt explicitly prohibits continuing dream RP language;
  reality sanitizer acts as fallback.
- exit_type=hard_exit → afterglow=hurt_reluctance (narrative difference only,
  no system penalty).
"""

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_AFTERGLOW_TTL_SECONDS: float = 8 * 3600  # 8 hours

_GENTLE_FRAME = (
    "【梦的余韵】\n"
    "你们最近有过一段共同的梦。以下是那段梦留下的情绪余波，"
    "不是现实发生的事，也不是可以继续的梦境场景。\n"
)

_HURT_FRAME = (
    "【梦的余韵·中断】\n"
    "你们有过一段共同的梦，但梦被强行中断了。"
    "以下是那段中断的梦留下的情绪残余，不是现实事件。\n"
)

_PROHIBIT_DREAM_RP = (
    "\n（现在是现实对话。不能继续梦境 RP 语气，不能重新进入梦境描写，不能假装还在梦里。）"
)


def load_afterglow(uid: str) -> str:
    """
    Return active afterglow text for injection into reality prompt layer 6f.
    Returns empty string if no active afterglow within TTL.
    """
    best = _find_best_summary(uid)
    if best is None:
        return ""
    return _format_afterglow(best)


def _find_best_summary(uid: str) -> dict[str, Any] | None:
    summaries_dir = _get_summaries_dir()
    if not summaries_dir.exists():
        return None

    candidates = list(summaries_dir.glob("dream_*.summary.json"))
    if not candidates:
        return None

    now = time.time()
    best: dict[str, Any] | None = None
    best_ts = 0.0

    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(data.get("uid", "")) != str(uid):
            continue
        created_at = float(data.get("created_at") or 0)
        if now - created_at > _AFTERGLOW_TTL_SECONDS:
            continue
        if created_at > best_ts:
            best_ts = created_at
            best = data

    return best


def _format_afterglow(summary: dict[str, Any]) -> str:
    afterglow_type = summary.get("afterglow", "gentle_residue")
    frame = _HURT_FRAME if afterglow_type == "hurt_reluctance" else _GENTLE_FRAME

    # Depth-defense: strip world-specific proprietary terms before injecting into reality
    world_id = summary.get("world_id", "reality_derived")
    try:
        from core.dream.world_loader import strip_vocab as _strip
        def _sv(text: str) -> str:
            return _strip(text, world_id)
    except Exception:
        def _sv(text: str) -> str:
            return text

    parts: list[str] = [frame]

    if s := summary.get("summary"):
        parts.append(f"情绪摘要：{_sv(s)}")

    if tags := summary.get("emotional_tags"):
        if isinstance(tags, list) and tags:
            parts.append("情绪色调：" + "、".join(_sv(str(t)) for t in tags[:4]))

    if frags := summary.get("symbolic_fragments"):
        if isinstance(frags, list) and frags:
            parts.append("残留意象：" + "、".join(_sv(str(f)) for f in frags[:3]))

    parts.append(_PROHIBIT_DREAM_RP)
    return "\n".join(parts)


def _get_summaries_dir() -> Path:
    from core.sandbox import get_paths
    return get_paths().dreams_summaries_dir()
