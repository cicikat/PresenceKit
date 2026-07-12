"""Global bilateral character impressions derived from reality Stage turns.

This store is deliberately limited to character-to-character relationships.
The owner never enters this store: owner relationships belong to identity and
impression memory, not the shared Stage relation layer.
"""
from __future__ import annotations

import json
import logging
import time

from core.safe_write import safe_write_json

logger = logging.getLogger(__name__)

RELATION_COOLDOWN_SECONDS = 6 * 60 * 60
RELATION_SUMMARY_MAX_CHARS = 60


def _pair(char_a: str, char_b: str) -> tuple[str, str]:
    first, second = sorted((str(char_a), str(char_b)))
    if first == second:
        raise ValueError("character relation requires two distinct characters")
    return first, second


def _path(char_a: str, char_b: str):
    from core.sandbox import get_paths

    first, second = _pair(char_a, char_b)
    return get_paths().char_relation(char_a=first, char_b=second)


def _empty_relation(char_a: str, char_b: str) -> dict:
    first, second = _pair(char_a, char_b)
    return {
        "char_a": first,
        "char_b": second,
        "a_of_b": {"summary": "", "valence": 0.0, "updated_at": ""},
        "b_of_a": {"summary": "", "valence": 0.0, "updated_at": ""},
        "interaction_count": 0,
        "last_interaction_ts": 0.0,
    }


def load_relation(char_a: str, char_b: str) -> dict | None:
    """Load a canonical pair record, or None when no relation has been learned."""
    try:
        path = _path(char_a, char_b)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        first, second = _pair(char_a, char_b)
        if data.get("char_a") != first or data.get("char_b") != second:
            return None
        return data
    except Exception:
        logger.debug("[stage.char_relations] relation load suppressed", exc_info=True)
        return None


def _save_relation(relation: dict) -> bool:
    return safe_write_json(_path(relation["char_a"], relation["char_b"]), relation)


def _envelope_from_payload(raw: dict | None):
    from core.write_envelope import SourceType, WriteEnvelope

    raw = raw or {}
    try:
        source = SourceType(str(raw.get("source", "unknown")))
    except ValueError:
        source = SourceType.UNKNOWN
    return WriteEnvelope(
        source=source,
        can_write_memory=bool(raw.get("can_write_memory", False)),
        is_test=bool(raw.get("is_test", False)),
        is_debug=bool(raw.get("is_debug", False)),
    )


def _clip_summary(value: object) -> str:
    return str(value or "").strip().replace("\n", " ")[:RELATION_SUMMARY_MAX_CHARS]


def _clamp_valence(value: object) -> float:
    try:
        return max(-1.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _parse_relation_response(raw: str, char_a: str, char_b: str) -> dict | None:
    try:
        text = (raw or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return None
        a_of_b, b_of_a = parsed.get("a_of_b"), parsed.get("b_of_a")
        if not isinstance(a_of_b, dict) or not isinstance(b_of_a, dict):
            return None
        return {
            "a_of_b": {
                "summary": _clip_summary(a_of_b.get("summary")),
                "valence": _clamp_valence(a_of_b.get("valence")),
            },
            "b_of_a": {
                "summary": _clip_summary(b_of_a.get("summary")),
                "valence": _clamp_valence(b_of_a.get("valence")),
            },
        }
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _relation_prompt(char_a: str, char_b: str, excerpt: str, old: dict) -> str:
    from core.character_name_provider import get_char_name

    name_a, name_b = get_char_name(char_a), get_char_name(char_b)
    old_a = old.get("a_of_b", {}).get("summary", "")
    old_b = old.get("b_of_a", {}).get("summary", "")
    return (
        "根据两位角色在群聊中的直接互动，更新他们彼此的第三人称印象。"
        "不推断未发生的事；每条摘要最多60个字，保留说话人归属。\n"
        f"旧印象：{name_a}对{name_b}：{old_a or '无'}；"
        f"{name_b}对{name_a}：{old_b or '无'}。\n"
        f"本轮直接互动：\n{excerpt}\n"
        "只输出 JSON："
        '{"a_of_b":{"summary":"", "valence":0},'
        '"b_of_a":{"summary":"", "valence":0}}；'
        f"其中 a={name_a}，b={name_b}，valence 范围为 -1 到 1。"
    )


def _append_provenance(
    uid: str, char_id: str, other_id: str, before: str, after: str, *, trigger_signal: str = "stage_interaction"
) -> None:
    if before == after:
        return
    try:
        from core.memory.provenance_log import append

        append(
            uid,
            char_id,
            artifact="char_relation",
            field=other_id,
            before_gist=before,
            after_gist=after,
            trigger_signal=trigger_signal,
        )
    except Exception:
        logger.debug("[stage.char_relations] provenance suppressed", exc_info=True)


async def handler_update_char_relations(payload: dict) -> None:
    """Slow-queue handler; LLM failure only records the interaction count."""
    envelope = _envelope_from_payload(payload.get("write_envelope"))
    if not envelope.can_write_memory:
        return
    char_a, char_b = _pair(payload["char_a"], payload["char_b"])
    uid = str(payload["uid"])
    now = float(payload.get("timestamp") or time.time())
    relation = load_relation(char_a, char_b) or _empty_relation(char_a, char_b)
    relation["interaction_count"] = int(relation.get("interaction_count", 0)) + 1
    last_ts = float(relation.get("last_interaction_ts", 0.0) or 0.0)
    relation["last_interaction_ts"] = now
    if now - last_ts < RELATION_COOLDOWN_SECONDS:
        _save_relation(relation)
        return

    try:
        from core import llm_client

        raw = await llm_client.chat(
            [{"role": "user", "content": _relation_prompt(char_a, char_b, str(payload.get("excerpt", "")), relation)}],
            call_category="consolidation",
            max_tokens_override=240,
        )
        updated = _parse_relation_response(raw, char_a, char_b)
    except Exception:
        logger.info("[stage.char_relations] LLM update failed; retaining old relation", exc_info=True)
        updated = None
    if updated is None:
        _save_relation(relation)
        return

    old_a = str(relation.get("a_of_b", {}).get("summary", ""))
    old_b = str(relation.get("b_of_a", {}).get("summary", ""))
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    relation["a_of_b"] = {**updated["a_of_b"], "updated_at": timestamp}
    relation["b_of_a"] = {**updated["b_of_a"], "updated_at": timestamp}
    if _save_relation(relation):
        _append_provenance(uid, char_a, char_b, old_a, relation["a_of_b"]["summary"])
        _append_provenance(uid, char_b, char_a, old_b, relation["b_of_a"]["summary"])


async def enqueue_relation_updates(group_id: str, turn_id: str) -> int:
    """Queue one relation update for every direct AI-to-AI reply pair in a turn."""
    from core.post_process import slow_queue
    from core.stage.store import load_stage, load_transcript
    from core.write_envelope import stamp_user_chat

    stage = load_stage(group_id)
    if stage is None or stage.domain != "reality":
        return 0
    entries = [entry for entry in load_transcript(group_id) if entry.turn_id == turn_id]
    pairs: dict[tuple[str, str], list[str]] = {}
    roster = set(stage.roster)
    for entry in entries:
        if entry.speaker_id in roster and entry.triggered_by in roster and entry.speaker_id != entry.triggered_by:
            pair = _pair(entry.speaker_id, entry.triggered_by)
            pairs.setdefault(pair, []).append(
                f"{entry.triggered_by} → {entry.speaker_id}：{entry.content}"
            )
    envelope = stamp_user_chat()
    envelope_payload = {"source": envelope.source.value, "can_write_memory": envelope.can_write_memory}
    for (char_a, char_b), lines in pairs.items():
        slow_queue.enqueue("update_char_relations", {
            "uid": stage.owner_uid,
            "char_a": char_a,
            "char_b": char_b,
            "excerpt": "\n".join(lines),
            "timestamp": time.time(),
            "write_envelope": envelope_payload,
        })
    return len(pairs)


def delete_relation(char_a: str, char_b: str, *, uid: str) -> bool:
    """Explicitly forget one global relation and log it for both characters."""
    relation = load_relation(char_a, char_b)
    if relation is None:
        return False
    try:
        _path(char_a, char_b).unlink()
    except OSError:
        return False
    first, second = _pair(char_a, char_b)
    _append_provenance(
        uid, first, second, str(relation.get("a_of_b", {}).get("summary", "")), "",
        trigger_signal="explicit_forget",
    )
    _append_provenance(
        uid, second, first, str(relation.get("b_of_a", {}).get("summary", "")), "",
        trigger_signal="explicit_forget",
    )
    return True
