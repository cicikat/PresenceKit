"""Weekly interest seeding trigger (Brief 58)."""
from __future__ import annotations

import json
import logging
import re
from collections import Counter

logger = logging.getLogger(__name__)

DOMAIN_WORDS = {
    "writing": ("写", "诗", "小说", "文", "日记"),
    "music": ("音乐", "歌", "琴", "旋律"),
    "drawing": ("画", "绘", "摄影", "视觉"),
}
DOMAIN_CANDIDATE_NAMES = {
    "writing": "写点东西",
    "music": "玩玩音乐",
    "drawing": "学着画画",
    "other": "试试新爱好",
}
TRAIT_DOMAIN_MAP = {"creativity": "writing", "aesthetic": "drawing", "patience": "music", "curiosity": "other"}


def _config() -> dict:
    from core.config_loader import get_config
    return get_config().get("practice", {}) or {}


def _domain(text: str) -> str:
    for domain, words in DOMAIN_WORDS.items():
        if any(w in text for w in words): return domain
    return "other"


def collect_candidates(uid: str, char_id: str) -> list[dict]:
    candidates: list[dict] = []
    try:
        from core.memory.event_log import get_recent_days
        text = get_recent_days(uid, days=30, char_id=char_id)
        counts = Counter({domain: sum(text.count(word) for word in words) for domain, words in DOMAIN_WORDS.items()})
        for domain, count in counts.most_common(2):
            if count:
                candidates.append({"name": DOMAIN_CANDIDATE_NAMES[domain], "domain": domain, "origin": "topic_stats"})
    except Exception: pass
    try:
        from core.memory.user_profile import load
        for fact in load(uid, char_id=char_id).get("important_facts", []):
            if isinstance(fact, dict) and str(fact.get("tag", "")).startswith("pref."):
                text = str(fact.get("text", "")).strip()
                if text: candidates.append({"name": text[:40], "domain": _domain(text), "origin": "user_pref_mirror"})
    except Exception: pass
    try:
        from core.sandbox import get_paths
        raw = json.loads(get_paths().trait_state(char_id=char_id).read_text(encoding="utf-8"))
        values = raw.get("traits", raw)
        if isinstance(values, dict) and values:
            key = min(values, key=lambda k: float(values[k].get("score", values[k]) if isinstance(values[k], dict) else values[k]))
            candidates.append({"name": f"探索{key}", "domain": TRAIT_DOMAIN_MAP.get(key, "other"), "origin": "trait_underrepresented"})
    except Exception: pass
    seen = set(); result = []
    for c in candidates:
        key = c["name"].casefold()
        if key not in seen: seen.add(key); result.append(c)
    return result


def _parse_json(text: str) -> dict | None:
    try:
        match = re.search(r"\{.*\}", text, re.S)
        value = json.loads(match.group(0) if match else text)
        return value if isinstance(value, dict) else None
    except Exception: return None


async def choose_candidate(candidates: list[dict], existing: list[dict], char_id: str) -> dict | None:
    if not candidates: return None
    from core import llm_client
    from core.character_loader import load
    char = load(char_id)
    prompt = (
        f"你在替角色{char.name}遴选一个真正愿意慢慢学习的新兴趣。"
        f"角色摘要：{char.personality[:500]}\n候选：{json.dumps(candidates, ensure_ascii=False)}\n"
        f"已有兴趣：{json.dumps([x.get('name') for x in existing], ensure_ascii=False)}\n"
        '只输出 JSON：{"pick":候选name或null,"domain":"writing|music|drawing|other","rationale":"一句理由"}'
    )
    raw = await llm_client.chat([{"role": "user", "content": prompt}], call_category="chat", char_id=char_id)
    return _parse_json(raw)


async def _check_interest_seed() -> None:
    if not _config().get("enabled", False): return
    from core.scheduler.loop import _is_ready, _mark, _owner_id
    if not _is_ready("interest_seed"): return
    _mark("interest_seed")
    uid = _owner_id()
    if not uid: return
    from core.scheduler.triggers.garden_water import _active_char_id
    char_id = _active_char_id()
    if not char_id: return
    from core.scheduler.rhythm import has_real_interaction_history
    if not has_real_interaction_history(uid, char_id=char_id):
        logger.debug("[scheduler] interest_seed 冷启动 skip：真实交互轮数不足")
        return
    from core.growth import interest_state
    await interest_state.apply_lifecycle(char_id=char_id, uid=uid)
    existing = interest_state.active_interests(char_id)
    if len(existing) >= interest_state.MAX_ACTIVE_INTERESTS: return
    candidates = collect_candidates(uid, char_id)
    choice = await choose_candidate(candidates, existing, char_id)
    if not choice or not choice.get("pick"): return
    candidate = next((c for c in candidates if c["name"] == choice["pick"]), None)
    if candidate is None: return
    entry = await interest_state.add_interest(candidate["name"], choice.get("domain") or candidate["domain"], candidate["origin"], char_id=char_id, rationale=str(choice.get("rationale", "")), uid=uid)
    if entry:
        try:
            from core.memory import action_trace
            action_trace.record(uid, char_id, tool="interest_seed", origin="assistant_loop", status="ok", result_digest=f"最近对{entry['name']}起了兴趣：{str(choice.get('rationale',''))[:40]}")
        except Exception: pass
