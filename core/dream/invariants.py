"""Cross-world identity observations; deliberately never prompt-facing."""
from __future__ import annotations
import json
import logging
import time
from pathlib import Path
from typing import Any
from core.data_paths import DEFAULT_CHAR_ID
from core.safe_write import safe_write_json

logger = logging.getLogger(__name__)
FORBIDDEN_WORDS = ("\u7231", "\u6c38\u8fdc", "\u6df1\u7231", "\u547d\u4e2d\u6ce8\u5b9a")
HIGH_CONVERGENCE_COUNT = 3
_SYSTEM = """你是跨世界身份稳定性观察器，只做测量，不做人格结论。
从梦境对话中提炼 0–2 条「情境类型 → 反应模式」：
- 情境类型和反应模式都必须世界无关，不得包含世界专有名词或设定词；
- 反应模式必须描述具体行为，不得使用抽象情感结论；
- 没有合格观察时返回空数组。
只输出 JSON，schema 仅为：
{"items":[{"situation":"...","response":"..."}]}"""

def _path(uid: str, char_id: str) -> Path:
    from core.sandbox import get_paths, safe_user_id
    return get_paths().dreams_invariants_dir(char_id=char_id) / f"{safe_user_id(uid)}.json"

def load(uid: str, *, char_id: str = DEFAULT_CHAR_ID) -> list[dict[str, Any]]:
    try:
        data = json.loads(_path(uid, char_id).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []

def save(uid: str, entries: list[dict[str, Any]], *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    return safe_write_json(_path(uid, char_id), entries)

def valid_items(payload: Any) -> list[dict[str, str]]:
    raw = payload.get("items", []) if isinstance(payload, dict) else []
    result: list[dict[str, str]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict): continue
        situation = str(item.get("situation") or "").strip()
        response = str(item.get("response") or "").strip()
        if not situation or not response or len(situation) > 120 or len(response) > 180: continue
        if any(word in situation + response for word in FORBIDDEN_WORDS): continue
        result.append({"situation": situation, "response": response})
        if len(result) == 2: break
    return result

def _archive_turns(dream_id: str, char_id: str) -> list[dict[str, Any]]:
    from core.sandbox import get_paths
    path = get_paths().dreams_archive_dir(char_id=char_id) / f"dream_{dream_id}.jsonl"
    try: return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except Exception: return []

async def observe(uid: str, dream_id: str, *, world_id: str, char_id: str = DEFAULT_CHAR_ID) -> None:
    """Extract and aggregate observations; failure never blocks dream exit."""
    try:
        turns = _archive_turns(dream_id, char_id)
        dialogue = "\n".join(f"[{t.get('role', '?')}] {str(t.get('content') or '')[:220]}" for t in turns)
        if not dialogue: return
        from core import llm_client
        raw = await llm_client.chat([{"role": "system", "content": _SYSTEM}, {"role": "user", "content": dialogue[:1800]}], max_tokens_override=280)
        payload = json.loads(str(raw).replace("```json", "").replace("```", "").strip())
        for item in valid_items(payload):
            await merge(uid, item, dream_id=dream_id, world_id=world_id, char_id=char_id)
    except Exception as exc:
        logger.warning("[invariants] observation skipped uid=%s dream=%s: %s", uid, dream_id, exc)

async def _relation(candidate: dict[str, str], existing: dict[str, Any]) -> str:
    from core import llm_client
    prompt = ("判断 A/B 两条观察是否属于同一反应模式。只回复 same、contradicts 或 different。\n"
              f"A: {existing.get('situation')} -> {existing.get('response')}\nB: {candidate['situation']} -> {candidate['response']}")
    answer = str(await llm_client.chat([{"role": "user", "content": prompt}], max_tokens_override=12)).strip().lower()
    return answer if answer in {"same", "contradicts", "different"} else "different"

async def merge(uid: str, candidate: dict[str, str], *, dream_id: str, world_id: str, char_id: str = DEFAULT_CHAR_ID) -> None:
    entries, now = load(uid, char_id=char_id), time.time()
    for entry in entries:
        relation = await _relation(candidate, entry)
        if relation == "same":
            entry["count"] = int(entry.get("count") or 0) + 1
            entry["worlds_seen"] = sorted(set(entry.get("worlds_seen") or []) | {world_id})
            entry["last_seen"] = now
            save(uid, entries, char_id=char_id); return
        if relation == "contradicts" and int(entry.get("count") or 0) >= HIGH_CONVERGENCE_COUNT:
            conflicts = entry.setdefault("contradicted_by", [])
            if not any(str(x.get("dream_id")) == dream_id for x in conflicts if isinstance(x, dict)):
                conflicts.append({"dream_id": dream_id, "summary": candidate["response"]})
            entry["last_seen"] = now
            save(uid, entries, char_id=char_id); return
    entries.append({"situation": candidate["situation"], "response": candidate["response"], "count": 1, "worlds_seen": [world_id], "first_seen": now, "last_seen": now, "contradicted_by": []})
    save(uid, entries, char_id=char_id)

def select_for_postcard(uid: str, *, char_id: str = DEFAULT_CHAR_ID) -> dict[str, Any] | None:
    """Only the archive postcard product may cite one strong observation."""
    eligible = [e for e in load(uid, char_id=char_id) if int(e.get("count") or 0) >= HIGH_CONVERGENCE_COUNT and len(e.get("worlds_seen") or []) >= 2]
    return max(eligible, key=lambda e: int(e.get("count") or 0), default=None)
