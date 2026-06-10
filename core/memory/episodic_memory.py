"""
episodic_memory — 情景记忆系统。
存储角色视角的情节单元，支持标签检索+强度衰减。
与event_log并行，不替换它。
"""

import json
import logging
import math
import time
from pathlib import Path

from core.memory.scope import MemoryScope, require_character_id
from core.memory.path_resolver import resolve_path
from core.sandbox import safe_user_id
from core.safe_write import safe_write_json
from core.llm_output_validator import record_failure

logger = logging.getLogger(__name__)


def _mem_read_file(user_id: str, *, char_id: str = "yexuan") -> Path:
    require_character_id(char_id)
    uid = safe_user_id(user_id)
    scope = MemoryScope.reality_scope(uid, char_id)
    return resolve_path(scope, "episodic")


def _mem_write_file(user_id: str, *, char_id: str = "yexuan") -> Path:
    require_character_id(char_id)
    uid = safe_user_id(user_id)
    scope = MemoryScope.reality_scope(uid, char_id)
    p = resolve_path(scope, "episodic")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _index_read_file(user_id: str, *, char_id: str = "yexuan") -> Path:
    require_character_id(char_id)
    uid = safe_user_id(user_id)
    scope = MemoryScope.reality_scope(uid, char_id)
    return resolve_path(scope, "memory_index")


def _index_write_file(user_id: str, *, char_id: str = "yexuan") -> Path:
    require_character_id(char_id)
    uid = safe_user_id(user_id)
    scope = MemoryScope.reality_scope(uid, char_id)
    p = resolve_path(scope, "memory_index")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_memories(user_id: str, *, char_id: str = "yexuan") -> list:
    require_character_id(char_id)  # guard before try — ValueError must not be swallowed
    try:
        return json.loads(_mem_read_file(user_id, char_id=char_id).read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_memories(user_id: str, memories: list, *, char_id: str = "yexuan") -> None:
    safe_write_json(_mem_write_file(user_id, char_id=char_id), memories)


def load_unconsolidated(user_id: str, *, char_id: str = "yexuan") -> list[dict]:
    """供 consolidate 类慢任务读取待处理 episodic 的接口。

    返回所有 consolidated_at 为 None 的条目，按 timestamp 升序排列，
    便于增量处理时从最旧的记忆开始合并。
    不带检索语义（不评分、不做 topic 匹配），也不更新 strength 或 retrieval_count。
    """
    raw = [m for m in _load_memories(user_id, char_id=char_id) if m.get("consolidated_at") is None]
    return sorted(raw, key=lambda m: m.get("timestamp", 0))


def _load_index(user_id: str, *, char_id: str = "yexuan") -> dict:
    require_character_id(char_id)  # guard before try — ValueError must not be swallowed
    try:
        return json.loads(_index_read_file(user_id, char_id=char_id).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_index(user_id: str, index: dict, *, char_id: str = "yexuan") -> None:
    _index_write_file(user_id, char_id=char_id).write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _is_similar(a: str, b: str, threshold: float = 0.6) -> bool:
    if not a or not b:
        return False
    shorter = a if len(a) <= len(b) else b
    longer = b if len(a) <= len(b) else a
    overlap = sum(1 for ch in shorter if ch in longer)
    return overlap / max(len(shorter), 1) >= threshold


def _texture_similarity(a: str, b: str) -> float:
    """基于字符 bigram 的 Jaccard 相似度，返回 [0, 1]。"""
    if not a or not b or len(a) < 2 or len(b) < 2:
        return 0.0
    set_a = {a[i:i+2] for i in range(len(a) - 1)}
    set_b = {b[i:i+2] for i in range(len(b) - 1)}
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def _rebuild_index(user_id: str, memories: list, *, char_id: str = "yexuan") -> None:
    """按标签建倒排索引：tag -> [memory_id, ...]"""
    index = {}
    for mem in memories:
        keywords = mem.get("topic_keywords") or mem.get("tags", [])
        for tag in keywords:
            index.setdefault(tag, [])
            if mem["id"] not in index[tag]:
                index[tag].append(mem["id"])
    _save_index(user_id, index, char_id=char_id)


def write_episode(user_id: str, episode: dict, *, char_id: str = "yexuan") -> None:
    """
    写入一条情景记忆。
    episode格式（新字段）：
    {
      "id": "ep_timestamp",
      "timestamp": float,
      "raw_facts": ["用户说了什么", "用了什么词"],       # 事实层，用于召回
      "topic_keywords": ["关键词1", "关键词2"],           # 话题关键词，用于索引
      "emotion_peak": "gentle",
      "emotion_texture": "情绪质感描述",
      "emotion_arc": "情绪流动方向",
      "user_state": "stressed_about_work",
      "narrative_summary": "角色回忆时用的自然语言摘要", # 叙事层，用于注入
      "strength": 0.8,
      "retrieval_count": 0,
      "last_retrieved": null,
      "summary": "...",       # 旧，兼容保留
      "yexuan_feeling": "...", # 旧，兼容保留
      "tags": [...]            # 旧，兼容保留
    }
    """
    memories = _load_memories(user_id, char_id=char_id)

    if not isinstance(episode, dict):
        record_failure("episodic_memory", str(episode), user_id)
        return

    # 去重：与最近10条做narrative_summary相似度检查，兼容旧记忆
    new_summary = episode.get("narrative_summary") or episode.get("summary", "")
    for existing in memories[-10:]:
        existing_summary = existing.get("narrative_summary") or existing.get("summary", "")
        if _is_similar(new_summary, existing_summary):
            logger.info(f"[episodic] 重复记忆跳过: {new_summary}")
            return

    # 上限控制：超过200条时只从非核心记忆里删掉 strength 最低的20条。
    # 核心记忆宁可让总数暂时超过上限，也不能因自动 cap 被删除。
    MAX_MEMORIES = 200
    if len(memories) >= MAX_MEMORIES:
        core_count = sum(1 for m in memories if m.get("is_core"))
        normal = [m for m in memories if not m.get("is_core")]
        normal.sort(key=lambda m: m.get("strength", 0))
        remove_count = min(20, len(normal))
        remove_ids = {id(m) for m in normal[:remove_count]}
        memories[:] = [m for m in memories if id(m) not in remove_ids]
        logger.info(
            f"[episodic] 记忆库裁剪至{len(memories)}条，保留核心{core_count}条"
        )

    # 双轨strength修正：LLM给初始值，规则叠加校正
    s = episode.get("strength", 0.5)
    ep = episode.get("emotion_peak", "neutral")
    tags = episode.get("topic_keywords") or episode.get("tags", [])

    if ep in ("sad", "angry"):
        s = min(1.0, s + 0.1)
    if ep in ("happy", "surprised"):
        s = min(1.0, s + 0.05)
    if len(tags) >= 4:
        s = min(1.0, s + 0.05)
    conflict_tags = {"吵架", "道歉", "哭", "生气", "误会", "和好"}
    if any(t in conflict_tags for t in tags):
        s = min(1.0, s + 0.2)
    first_tags = {"第一次", "初次", "第一回", "生日", "纪念"}
    if any(t in first_tags for t in tags):
        s = min(1.0, s + 0.15)
        episode["is_core"] = True

    episode["strength"] = round(s, 3)

    # 字段校验与默认填充
    if not episode.get("id") or not isinstance(episode.get("id"), str):
        record_failure("episodic_memory", str(episode), user_id)
        return
    if not isinstance(episode.get("timestamp"), (int, float)):
        episode["timestamp"] = time.time()
    if not isinstance(episode.get("summary"), str):
        episode["summary"] = ""
    _KNOWN_EMOTIONS = {
        "neutral", "gentle", "thinking", "happy", "sad",
        "surprised", "angry", "sleepy", "yandere",
    }
    if not isinstance(episode.get("emotion_peak"), str):
        episode["emotion_peak"] = "neutral"
    elif episode["emotion_peak"] not in _KNOWN_EMOTIONS:
        logger.info(f"[episodic] 未知 emotion_peak 新值: {episode['emotion_peak']}，放行")
    episode["strength"] = max(0.0, min(1.0, episode["strength"]))
    logger.info(f"episodic_strength_init uid={user_id} strength={episode['strength']:.3f}")
    if not isinstance(episode.get("tags"), list):
        episode["tags"] = []
    if not isinstance(episode.get("retrieval_count"), int):
        episode["retrieval_count"] = 0

    memories.append(episode)
    _save_memories(user_id, memories, char_id=char_id)
    _rebuild_index(user_id, memories, char_id=char_id)
    logger.info(f"[episodic] 写入情景记忆: {episode['id']}")


def retrieve(
    user_id: str,
    topic: str = "",
    top_k: int = 3,
    *,
    char_id: str = "yexuan",
    allow_strengthen: bool = True,
) -> list:
    """
    按话题标签+情绪检索最相关的情景记忆，检索后强化strength。
    返回list[dict]，按相关性排序。

    allow_strengthen: 控制是否执行召回后写回（strength += 0.15 / nudge_from_memory）。
      N2-A: fetch_context（读路径）调用时必须传 allow_strengthen=False，
      避免"召回→增强→更易召回"的永动机效应。
      post_process / 写路径可保持默认 True（向后兼容）。
    """
    memories = _load_memories(user_id, char_id=char_id)
    if not memories:
        return []

    from core.memory.mood_state import get_current as _get_mood, get_intensity as _get_intensity
    _current_mood = _get_mood()
    _mood_intensity = _get_intensity()

    index = _load_index(user_id, char_id=char_id)
    now = time.time()

    # 候选集：同时匹配 topic_keywords 和 raw_facts，兼容旧记忆
    candidate_ids = set()
    hit_counts = {}  # mem_id -> 命中的 topic_word 数
    if topic:
        topic_words = topic.split()
        for mem in memories:
            keywords_text = " ".join(mem.get("topic_keywords") or mem.get("tags", []))
            facts_text = " ".join(mem.get("raw_facts", []))
            haystack = keywords_text + " " + facts_text
            hits = sum(1 for kw in topic_words if kw and kw in haystack)
            if hits > 0:
                candidate_ids.add(mem["id"])
                hit_counts[mem["id"]] = hits

    # 无匹配时全量参与评分
    if not candidate_ids:
        candidate_ids = {m["id"] for m in memories}

    # 评分
    scored = []
    for mem in memories:
        if mem["id"] not in candidate_ids:
            continue

        days = (now - mem["timestamp"]) / 86400
        decay = max(0.3, math.exp(-0.05 * days))  # 地板 0.3，防止高强度旧记忆被时间洗没
        strength = mem.get("strength", 0.5)
        if mem.get("emotion_peak") == _current_mood:
            emotion_bonus = 0.15 + _mood_intensity * 0.15
        else:
            emotion_bonus = 0.0
        # query relevance：命中越多越相关，3 个及以上视为完全命中
        relevance_bonus = 0.2 * min(hit_counts.get(mem["id"], 0) / 3, 1.0)
        score = strength * decay + emotion_bonus + relevance_bonus
        scored.append((score, mem))

    # 浮起阈值：分数太低的记忆不注入，宁可不说也不强行关联
    MIN_SCORE = 0.15
    scored = [(score, mem) for score, mem in scored if score >= MIN_SCORE]
    scored.sort(key=lambda x: x[0], reverse=True)

    sorted_results = [mem for _, mem in scored]
    pool_size = min(top_k * 2, len(sorted_results))
    candidates = sorted_results[:pool_size]

    if len(candidates) <= top_k:
        result = candidates
    else:
        # 第一条永远是最高分，保证相关性下限
        selected = [candidates[0]]
        pool = candidates[1:]

        while len(selected) < top_k and pool:
            best, best_novelty = None, -1.0
            for c in pool:
                c_tex = c.get("emotion_texture", "") or ""
                max_sim = 0.0
                for s in selected:
                    s_tex = s.get("emotion_texture", "") or ""
                    # texture 缺失视作完全不像，不参与筛选也不被惩罚
                    if not c_tex or not s_tex:
                        continue
                    sim = _texture_similarity(c_tex, s_tex)
                    if sim > max_sim:
                        max_sim = sim
                novelty = 1.0 - max_sim
                if novelty > best_novelty:
                    best, best_novelty = c, novelty
            if best is None:
                break
            selected.append(best)
            pool.remove(best)

        result = selected

    # 检索后强化（N2-A: allow_strengthen=False 时跳过，读路径不写回）
    if allow_strengthen:
        ids_to_strengthen = {m["id"] for m in result}
        changed = False
        for mem in memories:
            if mem["id"] in ids_to_strengthen:
                mem["strength"] = min(1.0, mem.get("strength", 0.5) + 0.15)
                mem["retrieval_count"] = mem.get("retrieval_count", 0) + 1
                mem["last_retrieved"] = now
                changed = True

        if changed:
            _save_memories(user_id, memories, char_id=char_id)

        from core.memory.mood_state import nudge_from_memory
        for mem in result:
            nudge_from_memory(
                mem.get("emotion_peak", "neutral"),
                mem.get("strength", 0.5)
            )
    else:
        logger.debug(
            "[episodic.retrieve] allow_strengthen=False，跳过 strength 写回 uid=%s", user_id
        )

    return result


def decay_all(user_id: str) -> None:
    """每日衰减，按情绪强度和被提及次数差异化处理。核心记忆不衰减。"""
    memories = _load_memories(user_id)
    now = time.time()
    for mem in memories:
        if mem.get("is_core"):
            continue
        days = (now - mem["timestamp"]) / 86400
        ep = mem.get("emotion_peak", "neutral")
        retrieval = mem.get("retrieval_count", 0)

        if ep in ("sad", "angry"):
            base_rate = 0.015
        elif ep == "neutral":
            base_rate = 0.05
        else:
            base_rate = 0.03

        recall_factor = max(0.3, 1.0 - retrieval * 0.1)
        rate = base_rate * recall_factor

        mem["strength"] = max(0.05, mem.get("strength", 0.5) * math.exp(-rate * days))

    _save_memories(user_id, memories)


def format_for_prompt(
    memories: list,
    char_name: str = None,
    current_emotion: str = "neutral",
) -> str:
    """把情景记忆列表格式化成prompt注入文本，带时间锚点和情绪染色。"""
    if char_name is None:
        from core.config_loader import _char_name
        char_name = _char_name()
    if not memories:
        return ""

    now = time.time()
    lines = [f"{char_name}脑海里浮现的片段："]

    for mem in memories:
        summary = mem.get("narrative_summary") or mem.get("summary", "")
        if not summary:
            continue

        days = (now - mem["timestamp"]) / 86400
        if days < 1:
            time_str = "今天"
        elif days < 3:
            time_str = "前几天"
        elif days < 7:
            time_str = "上周"
        elif days < 30:
            time_str = f"大约{int(days)}天前"
        else:
            time_str = f"{int(days // 30)}个月前"

        texture = mem.get("emotion_texture", "")
        arc = mem.get("emotion_arc", "")
        if texture:
            feeling_str = f"，{texture}" if current_emotion not in ("sad", "gentle") else f"——{texture}"
        else:
            feeling_str = ""

        arc_str = f"（{arc}）" if arc else ""

        core_mark = "【重要】" if mem.get("is_core") else ""
        lines.append(f"- {core_mark}{time_str}，{summary}{feeling_str}{arc_str}")

    return "\n".join(lines)


def retrieve_fallback(user_id: str, recent_history: list[str], top_k: int = 1, *, char_id: str = "yexuan") -> list[dict]:
    """
    tag 未命中时的兜底召回。不依赖 query，按强度+时间挑近期高强度记忆。
    筛选条件：7天内、strength >= 0.6、不在最近 short_term 内容里。
    """
    memories = _load_memories(user_id, char_id=char_id)
    now = time.time()
    candidates = []
    for m in memories:
        age_days = (now - m.get("timestamp", now)) / 86400
        if age_days > 7:
            continue
        if m.get("strength", 0) < 0.6:
            continue
        summary = m.get("narrative_summary") or m.get("summary", "")
        if any(_is_similar(summary, h) for h in recent_history if h):
            continue
        # 衰减加地板，与主 retrieve 保持一致
        decay = max(0.5, 1.0 / (age_days + 1))
        score = m.get("strength", 0.5) * decay
        candidates.append((score, m))
    if candidates:
        vals = [c[0] for c in candidates]
        logger.info(
            f"episodic_fallback uid={user_id} pool={len(vals)} "
            f"min={min(vals):.3f} max={max(vals):.3f} "
            f"selected={len([v for v in vals if v >= 0.4])}"
        )
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in candidates[:top_k]]



def cleanup(user_id: str, max_count: int = 200) -> None:
    """手动清理存量记忆，保留strength最高的max_count条。核心记忆不删。"""
    memories = _load_memories(user_id)
    if len(memories) <= max_count:
        logger.info(f"[episodic] 无需清理，当前{len(memories)}条")
        return

    core = [m for m in memories if m.get("is_core")]
    normal = [m for m in memories if not m.get("is_core")]
    normal.sort(key=lambda m: m.get("strength", 0), reverse=True)

    keep_normal = max_count - len(core)
    kept = core + normal[:keep_normal]
    removed = len(memories) - len(kept)

    _save_memories(user_id, kept)
    _rebuild_index(user_id, kept)
    logger.info(f"[episodic] 清理完成，删除{removed}条，保留{len(kept)}条")
