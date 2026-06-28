"""
Dream context snapshot — frozen once at dream entry.

Constraints (BY CONSTRUCTION):
- Assembled once, written into dream_state["context_snapshot"].
- Dream turns read from the snapshot only; they never call fetch_context,
  retrieve, user_identity.load, or mood_state.get.
- memory_access controls what goes into the snapshot, NOT whether live
  memory access is allowed (it never is).

memory_access tiers:
  card_only            → relationship_state + entry_reason only
  relationship_summary → + recent_reality_context + profile_impression
  full_snapshot        → all of the above + episodic_summary + mid_term_context
"""

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


async def build_snapshot(user_id: str, entry_reason: str = "", *, char_id: str = "yexuan", char_name: str = "(角色未加载)") -> dict[str, Any]:
    """
    Assemble and return the frozen dream context snapshot.

    Called once at dream entry. The caller writes the result into
    dream_state["context_snapshot"] and never refreshes it during the dream.
    """
    from core.dream.dream_settings import load as _load_settings, MemoryAccess

    settings = _load_settings(user_id)
    memory_access: str = settings.get("memory_access", MemoryAccess.relationship_summary.value)

    snapshot: dict[str, Any] = {
        "created_at": time.time(),
        "user_id": user_id,
        f"{char_id}_awareness": "lucid_shared",
        "boundary": "dream_only",
        "entry_reason": entry_reason,
        "memory_access": memory_access,
    }

    # A short-lived, one-shot bridge from the reality-side Dream Seed activity.
    # It is deliberately consumed at Dream entry and only affects entry_reason.
    try:
        from core.activity.dream_seed import consume_seed as _consume_seed
        seed = _consume_seed(user_id, char_id=char_id)
        if seed:
            prefix = f"今晚的梦境设定：{seed}"
            snapshot["entry_reason"] = (
                f"{prefix}\n{snapshot['entry_reason']}"
                if snapshot["entry_reason"] else prefix
            )
    except Exception as exc:
        logger.warning("[dream_context] dream_seed inject failed: %s", exc)

    # relationship_state — always included regardless of tier
    try:
        from core import user_relation
        snapshot["relationship_state"] = user_relation.get_relation(user_id)
    except Exception as e:
        logger.warning(f"[dream_context] relationship_state failed: {e}")
        snapshot["relationship_state"] = {}

    if memory_access == MemoryAccess.card_only.value:
        # Minimal sandbox mode: no memory context whatsoever
        snapshot["recent_reality_context"] = ""
        snapshot["recent_reality_gist"] = ""
        snapshot["episodic_summary"] = ""
        snapshot["mid_term_context"] = ""
        snapshot["profile_impression"] = ""
        return snapshot

    # relationship_summary and full_snapshot both include recent context + profile
    try:
        from core.memory import short_term
        history = short_term.load_for_prompt(user_id, char_id=char_id)
        _rrc = _summarize_recent(history, char_name=char_name)
        snapshot["recent_reality_context"] = _rrc
        snapshot["recent_reality_gist"] = _make_gist(_rrc)
    except Exception as e:
        logger.warning(f"[dream_context] recent_reality_context failed: {e}")
        snapshot["recent_reality_context"] = ""
        snapshot["recent_reality_gist"] = ""

    try:
        from core.memory import user_profile
        profile = user_profile.load(user_id, char_id=char_id)
        snapshot["profile_impression"] = _extract_impression(profile)
    except Exception as e:
        logger.warning(f"[dream_context] profile_impression failed: {e}")
        snapshot["profile_impression"] = ""

    if memory_access == MemoryAccess.full_snapshot.value:
        # Full tier: also include episodic memory and mid-term context
        try:
            from core.memory.episodic_memory import retrieve, format_for_prompt
            from core.memory.mood_state import get_current as _get_mood
            episodes = retrieve(user_id=user_id, topic="", top_k=3, char_id=char_id)
            snapshot["episodic_summary"] = format_for_prompt(
                episodes,
                char_name=char_name,
                current_emotion=_get_mood(),
            )
        except Exception as e:
            logger.warning(f"[dream_context] episodic failed: {e}")
            snapshot["episodic_summary"] = ""

        try:
            from core.memory import mid_term
            snapshot["mid_term_context"] = mid_term.format_for_prompt(user_id, char_id=char_id)
        except Exception as e:
            logger.warning(f"[dream_context] mid_term failed: {e}")
            snapshot["mid_term_context"] = ""
    else:
        snapshot["episodic_summary"] = ""
        snapshot["mid_term_context"] = ""

    # ── user_hidden_state_snapshot (Phase 4 — read-only bucket projection) ─────
    # Loaded once at dream entry and frozen into context_snapshot.
    # Dream turns MUST NOT write back to hidden state via this snapshot.
    # DREAM_DIRECT_WRITABLE = frozenset() — no field may be written from Dream.
    # Fail-closed: any error → empty dict; Dream is never blocked.
    try:
        from core.memory.user_hidden_state_store import load_dream_snapshot as _load_hs
        _hs_now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        snapshot["user_hidden_state_snapshot"] = _load_hs(user_id, _hs_now, char_id=char_id)
    except Exception as _hs_exc:
        logger.warning(f"[dream_context] user_hidden_state_snapshot failed: {_hs_exc}")
        snapshot["user_hidden_state_snapshot"] = {}

    return snapshot


def _summarize_recent(history: list[dict], *, char_name: str = "(角色未加载)") -> str:
    """Condense last few history turns into a short context string."""
    tail = history[-6:] if len(history) > 6 else history
    lines = []
    for h in tail:
        role = "用户" if h.get("role") == "user" else char_name
        content = (h.get("content") or "")[:60]
        lines.append(f"{role}：{content}")
    return "\n".join(lines)


def _make_gist(recent_context: str) -> str:
    """Extract a one-line gist from recent_reality_context for use after full-context turns expire."""
    if not recent_context:
        return "聊天"
    lines = [l.strip() for l in recent_context.split("\n") if l.strip()]
    if not lines:
        return "聊天"
    last = lines[-1]
    if "：" in last:
        last = last.split("：", 1)[1].strip()
    return (last[:40] + "…") if len(last) > 40 else (last or "聊天")


def _extract_impression(profile: dict) -> str:
    """Extract a brief impression string from user profile."""
    if not profile:
        return ""
    parts = []
    if traits := profile.get("traits"):
        if isinstance(traits, list):
            parts.append("用户特征：" + "、".join(str(t) for t in traits[:5]))
    if state := profile.get("current_state"):
        parts.append(f"当前状态：{state}")
    if nickname := profile.get("nickname"):
        parts.append(f"称呼：{nickname}")
    return "；".join(parts)
