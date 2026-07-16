"""
episodic_memory — 情景记忆系统。
存储角色视角的情节单元，支持标签检索+强度衰减。
与event_log并行，不替换它。
"""

import json
import logging
import math
import re
import time
from datetime import datetime
from pathlib import Path

from core.memory.scope import MemoryScope, require_character_id
from core.memory.path_resolver import resolve_path
from core.sandbox import safe_user_id
from core.safe_write import safe_write_json
from core.llm_output_validator import record_failure
from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)

# Brief 47: 召回增强收益递减，boost = _RETRIEVAL_BOOST_BASE / (1 + retrieval_count_before)
_RETRIEVAL_BOOST_BASE = 0.15


class EpisodicCorruptError(Exception):
    """Raised when episodic.json exists but cannot be parsed. Prevents silent memory wipe."""


def _mem_read_file(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    require_character_id(char_id)
    uid = safe_user_id(user_id)
    scope = MemoryScope.reality_scope(uid, char_id)
    return resolve_path(scope, "episodic")


def _mem_write_file(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    require_character_id(char_id)
    uid = safe_user_id(user_id)
    scope = MemoryScope.reality_scope(uid, char_id)
    p = resolve_path(scope, "episodic")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _index_read_file(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    require_character_id(char_id)
    uid = safe_user_id(user_id)
    scope = MemoryScope.reality_scope(uid, char_id)
    return resolve_path(scope, "memory_index")


def _index_write_file(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    require_character_id(char_id)
    uid = safe_user_id(user_id)
    scope = MemoryScope.reality_scope(uid, char_id)
    p = resolve_path(scope, "memory_index")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_memories(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> list:
    require_character_id(char_id)  # guard before try — ValueError must not be swallowed
    p = _mem_read_file(user_id, char_id=char_id)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.error(
            "[episodic] 加载失败（疑似损坏），拒绝按空处理 uid=%s path=%s err=%s",
            user_id, p, e,
        )
        raise EpisodicCorruptError(str(p)) from e


def _save_memories(user_id: str, memories: list, *, char_id: str = DEFAULT_CHAR_ID) -> None:
    p = _mem_write_file(user_id, char_id=char_id)
    # Guard: refuse to overwrite a non-empty file with an empty list — almost certainly
    # means an upstream _load_memories failure leaked through.
    try:
        if (not memories) and p.exists() and p.stat().st_size > 1024:
            logger.error("[episodic] 拒绝用空列表覆写非空记忆文件 uid=%s", user_id)
            return
    except Exception:
        pass
    safe_write_json(p, memories)


def load_unconsolidated(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> list[dict]:
    """供 consolidate 类慢任务读取待处理 episodic 的接口。

    返回所有 consolidated_at 为 None 的条目，按 timestamp 升序排列，
    便于增量处理时从最旧的记忆开始合并。
    不带检索语义（不评分、不做 topic 匹配），也不更新 strength 或 retrieval_count。
    """
    raw = [m for m in _load_memories(user_id, char_id=char_id) if m.get("consolidated_at") is None]
    return sorted(raw, key=lambda m: m.get("timestamp", 0))


def _load_index(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> dict:
    require_character_id(char_id)  # guard before try — ValueError must not be swallowed
    p = _index_read_file(user_id, char_id=char_id)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.error("[episodic] 索引损坏，从记忆重建 uid=%s path=%s err=%s", user_id, p, e)
    # index is derived data — rebuild from memories rather than returning empty and killing hop-2
    try:
        memories = _load_memories(user_id, char_id=char_id)
        _rebuild_index(user_id, memories, char_id=char_id)
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_index(user_id: str, index: dict, *, char_id: str = DEFAULT_CHAR_ID) -> None:
    safe_write_json(_index_write_file(user_id, char_id=char_id), index)


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


def _rebuild_index(user_id: str, memories: list, *, char_id: str = DEFAULT_CHAR_ID) -> None:
    """按标签建倒排索引：tag -> [memory_id, ...]"""
    index = {}
    for mem in memories:
        keywords = mem.get("topic_keywords") or mem.get("tags", [])
        for tag in keywords:
            index.setdefault(tag, [])
            if mem["id"] not in index[tag]:
                index[tag].append(mem["id"])
    _save_index(user_id, index, char_id=char_id)


def write_episode(user_id: str, episode: dict, *, char_id: str = DEFAULT_CHAR_ID) -> None:
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
      "status": "open",
      "resolved_at": null,
      "resolved_by": null,
      "temporal_ref": "none",
      "event_time": null,
      "expires_at": null,
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

    # 血缘 exact-dup：新 episode 的 source_mid_ids 与任意存量重叠 → 跳过
    new_mids = set(episode.get("source_mid_ids") or [])
    if new_mids:
        for existing in memories:
            if new_mids & set(existing.get("source_mid_ids") or []):
                logger.info(f"[episodic] 血缘重复跳过: {episode.get('id')} ∩ {existing.get('id')}")
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
        evicted = normal[:remove_count]
        remove_ids = {id(m) for m in evicted}
        memories[:] = [m for m in memories if id(m) not in remove_ids]
        logger.info(
            f"[episodic] 记忆库裁剪至{len(memories)}条，保留核心{core_count}条"
        )
        if evicted:
            # 遗忘=降级而非删除（Brief 46 §1，Brief 80 §3 起改走 storyline inbox）：
            # 被裁条目先入队暂存进 storyline_inbox，等周频聚合统一消费，
            # 再从 episodic.json 删除（上面已完成删除，入队携带全文快照）。
            from core.post_process import slow_queue
            slow_queue.enqueue("storyline_evicted_input", {
                "uid": user_id,
                "char_id": char_id,
                "episodes": evicted,
                "scope": MemoryScope.reality_scope(str(user_id), char_id).to_payload(),
            })

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
    if episode.get("status") not in ("open", "resolved", "elapsed"):
        episode["status"] = "open"
    if not isinstance(episode.get("resolved_at"), (int, float)):
        episode["resolved_at"] = None
    if not isinstance(episode.get("resolved_by"), str):
        episode["resolved_by"] = None
    if episode.get("temporal_ref") not in ("future", "past", "none"):
        episode["temporal_ref"] = "none"
    if not isinstance(episode.get("event_time"), (int, float)):
        episode["event_time"] = None
    if not isinstance(episode.get("expires_at"), (int, float)):
        episode["expires_at"] = None
    if not isinstance(episode.get("occurred_at"), (int, float)):
        episode["occurred_at"] = episode.get("timestamp", time.time())

    memories.append(episode)
    _save_memories(user_id, memories, char_id=char_id)
    _rebuild_index(user_id, memories, char_id=char_id)
    logger.info(f"[episodic] 写入情景记忆: {episode['id']}")


def retrieve(
    user_id: str,
    topic: str = "",
    top_k: int = 3,
    *,
    char_id: str = DEFAULT_CHAR_ID,
    char_name: str = "",
    allow_strengthen: bool = True,
    return_trace: bool = False,
    query_vec: list | None = None,
    sem_hits: list | None = None,
    since_ts: float | None = None,
    until_ts: float | None = None,
) -> list | tuple:
    """
    按话题标签+情绪检索最相关的情景记忆，检索后强化strength。
    返回list[dict]，按相关性排序。

    since_ts / until_ts（Brief 48，查询侧时间意图）：非 None 时按 occurred_at（缺失回退
    timestamp）过滤候选，[since_ts, until_ts) 半开区间。过滤发生在关键词/语义候选之后、
    评分之前；过滤后为空时改用时间范围内的全量记忆参与评分（"上周聊了什么"这类
    time-only 查询没有关键词也要能召回）。默认 None=现行为，不受影响。

    allow_strengthen: 控制是否执行召回后写回（strength += 递减 boost / nudge_from_memory；
      Brief 47：boost = _RETRIEVAL_BOOST_BASE / (1 + retrieval_count)，收益递减防永动机）。
      N2-A: fetch_context（读路径）调用时必须传 allow_strengthen=False，
      避免"召回→增强→更易召回"的永动机效应。
      post_process / 写路径可保持默认 True（向后兼容）。

    return_trace: 若 True，返回 (result, trace_items) 而非单独的 result。
      trace_items: list[dict] — 所有通过 MIN_SCORE 的候选项明细（score, selected 等）。

    query_vec / sem_hits（Brief 36，executor 化收尾）：sem_hits 是调用方已经
      通过 vector_store.query_async(..., sources=["episodic"]) 异步查询好的
      (source_id, distance, ts) 列表；query_vec 仅作为"是否启用语义候选扩展"的
      开关保留。retrieve() 本身不再直接调用同步的 vector_store.query()，所有
      sqlite IO 都留在调用方的 query_async（单 worker executor），retrieve()
      保持同步签名。
    """
    try:
        memories = _load_memories(user_id, char_id=char_id)
    except EpisodicCorruptError:
        logger.error("[episodic.retrieve] 文件损坏，本轮跳过 episodic uid=%s", user_id)
        return ([], []) if return_trace else []
    if not memories:
        return ([], []) if return_trace else []

    from core.memory.mood_state import get_current as _get_mood, get_intensity as _get_intensity
    _current_mood = _get_mood()
    _mood_intensity = _get_intensity()

    index = _load_index(user_id, char_id=char_id)
    now = time.time()

    # 候选集：n-gram 分别命中 topic_keywords / raw_facts；keyword 为主信号，facts 为弱辅证
    candidate_ids = set()
    matched_map: dict = {}      # mem_id -> set(全部命中 gram)
    kw_matched_map: dict = {}   # mem_id -> set(命中 topic_keywords 的 gram)
    df: dict = {}
    query_grams: set = set()
    idf: dict = {}
    exact_kw_map: dict = {}     # mem_id -> set(精确命中 topic_keywords 条目的 gram)
    if topic:
        from core.text_match import ngram_tokens
        _clean = topic.replace(char_name, "  ") if char_name else topic
        query_grams = ngram_tokens(_clean, stopwords={char_name} if char_name else None)
        for mem in memories:
            keywords_text = " ".join(mem.get("topic_keywords") or mem.get("tags", []))
            facts_text = " ".join(mem.get("raw_facts", []))
            kw_hit = {g for g in query_grams if g in keywords_text}
            fact_hit = {g for g in query_grams if g in facts_text}
            matched = kw_hit | fact_hit
            if matched:
                matched_map[mem["id"]] = matched
                kw_matched_map[mem["id"]] = kw_hit
                for g in matched:
                    df[g] = df.get(g, 0) + 1

        N = max(1, len(memories))
        idf = {g: math.log((N + 1) / (c + 1)) + 1.0 for g, c in df.items()}
        SPECIFIC_DF_FRAC = 0.10
        _specific_cap = max(1, int(SPECIFIC_DF_FRAC * N))
        specific = {g for g, c in df.items() if c <= _specific_cap}

        # 精确命中：查询 gram 与某条 topic_keywords 条目完全相等——策划过的 tag 精确
        # 命中不该受小语料 DF 特异性过滤挡（小语料下常见词也会被判定为"不 specific"）。
        exact_kw_map: dict = {}
        for mem in memories:
            exact = query_grams & set(mem.get("topic_keywords") or [])
            if exact:
                exact_kw_map[mem["id"]] = exact

        for mid, matched in matched_map.items():
            kwm = kw_matched_map.get(mid, set())
            # 主证据：命中关键词里的具体词，命中≥2个关键词，或精确命中某个 topic_keywords 条目
            if (kwm & specific) or (len(kwm) >= 2) or exact_kw_map.get(mid):
                candidate_ids.add(mid)
            # 弱辅证：纯 facts 命中要更强（≥2个不同具体词）才入选，挡掉"到了/一起"这类单词偶然命中
            elif len(matched & specific) >= 2:
                candidate_ids.add(mid)

    # ── X2: semantic candidate extension ─────────────────────────────────────
    # Build sem_sim_map {ep_id -> similarity} from vector store hits,
    # then add semantic-only hits (no keyword overlap) to the candidate pool.
    # Brief 36: hits are pre-fetched by the caller via vector_store.query_async()
    # (single worker executor) and handed in as sem_hits — retrieve() itself no
    # longer performs the (blocking, event-loop-thread) sync vector_store.query().
    sem_sim_map: dict[str, float] = {}
    if query_vec is not None:
        try:
            from core.memory.vector_store import dist_to_sim as _d2s
            _mem_by_id = {m["id"]: m for m in memories}
            for _src_id, _dist, _ts in (sem_hits or []):
                sem_sim_map[_src_id] = _d2s(_dist)
                if _src_id not in candidate_ids and _src_id in _mem_by_id:
                    candidate_ids.add(_src_id)
        except Exception as _se:
            logger.debug("[episodic.retrieve] semantic lookup failed: %s", _se)

    # ── Brief 48: 查询侧时间过滤 ──────────────────────────────────────────────
    # 在关键词/语义候选之后、评分之前过滤；过滤后为空则退化为"时间范围内全量记忆"，
    # 让纯 time-only 查询（无关键词命中）也能召回。
    if since_ts is not None or until_ts is not None:
        def _occurred_at(mem: dict) -> float:
            ts = mem.get("occurred_at")
            return ts if isinstance(ts, (int, float)) else mem.get("timestamp", 0)

        def _in_range(ts: float) -> bool:
            if since_ts is not None and ts < since_ts:
                return False
            if until_ts is not None and ts >= until_ts:
                return False
            return True

        time_candidate_ids = {m["id"] for m in memories if _in_range(_occurred_at(m))}
        filtered_ids = candidate_ids & time_candidate_ids
        candidate_ids = filtered_ids if filtered_ids else time_candidate_ids

    # 无真实词面命中且无语义命中：主路径不强行倒高强度记忆，交给 fallback。
    if topic and not candidate_ids:
        logger.debug("[episodic.retrieve] 无足够词面证据，主召回返回空，交给 fallback uid=%s", user_id)
        return ([], []) if return_trace else []

    # 评分
    REL_SCALE = 5.0   # idf_sum 归一化尺度（约"一个稀有词≈满分"），据 trace 可调
    MIN_SCORE = 0.15  # 浮起阈值：分数太低的记忆不注入，宁可不说也不强行关联
    rel_map: dict = {}   # mem_id -> relevance_norm，供 trace 段读取
    scored = []
    from core.memory.vector_store import score_recall as _score_recall
    for mem in memories:
        if mem["id"] not in candidate_ids:
            continue
        # 已解决事件默认不再召回；若未来需要偶尔浮起，可改为对 score 乘低权重。
        if mem.get("status", "open") in ("resolved", "elapsed"):
            continue

        days = (now - mem["timestamp"]) / 86400
        decay = max(0.3, math.exp(-0.05 * days))  # 地板 0.3，防止高强度旧记忆被时间洗没
        strength = mem.get("strength", 0.5)
        if mem.get("emotion_peak") == _current_mood:
            emotion_bonus = 0.15 + _mood_intensity * 0.15
        else:
            emotion_bonus = 0.0
        FACTS_WEIGHT = 0.3   # 纯 facts 命中的折扣（据 trace 可调）
        _kwm = kw_matched_map.get(mem["id"], set())
        _fact_only = matched_map.get(mem["id"], set()) - _kwm
        idf_sum = sum(idf.get(g, 0.0) for g in _kwm) + FACTS_WEIGHT * sum(idf.get(g, 0.0) for g in _fact_only)
        relevance_norm = min(1.0, idf_sum / REL_SCALE)
        if exact_kw_map.get(mem["id"]):
            # 精确命中给个下限，避免小语料 idf 归一化把策划过的 tag 精确命中压得太低
            relevance_norm = max(relevance_norm, 0.5)
        rel_map[mem["id"]] = relevance_norm
        sem_sim = sem_sim_map.get(mem["id"], 0.0)
        base_score = _score_recall(sem_sim, relevance_norm, strength, decay) + emotion_bonus
        expires_at = mem.get("expires_at")
        is_expired = isinstance(expires_at, (int, float)) and now > expires_at
        # 到期降权只影响排序，不应在 MIN_SCORE 判定前就把降权后的分数当依据——
        # 否则"到期事件降权仍可召回"的设计意图等价于被直接排除。
        rank_score = base_score * 0.3 if is_expired else base_score
        if base_score >= MIN_SCORE:
            scored.append((rank_score, mem))

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

    # ── hop-2：关键词二跳（保守、flag 可关）────────────────────────────────
    from core.config_loader import get_config
    _two_hop = get_config().get("episodic", {}).get("two_hop_enabled", False)
    hop2_added: list = []
    if _two_hop and topic and result:
        HOP2_MAX = 2
        HOP2_DECAY = 0.4
        mem_by_id = {m["id"]: m for m in memories}
        _seed_ids = {m["id"] for m in result}
        _already = _seed_ids | candidate_ids

        _N = max(1, len(memories))
        _KW_SHARE_CAP = max(2, int(0.10 * _N))
        cand_scores: dict = {}
        for seed in result:
            seed_rel = rel_map.get(seed["id"], 0.5)
            for kw in (seed.get("topic_keywords") or []):
                linked = index.get(kw, [])
                if not (1 < len(linked) <= _KW_SHARE_CAP):
                    continue
                for mid in linked:
                    if mid in _already or mid not in mem_by_id:
                        continue
                    m = mem_by_id[mid]
                    if m.get("status", "open") in ("resolved", "elapsed"):
                        continue
                    if m.get("strength", 0) < 0.5:
                        continue
                    cand_scores[mid] = cand_scores.get(mid, 0.0) + \
                        seed_rel * HOP2_DECAY * m.get("strength", 0.5)

        ranked = sorted(cand_scores.items(), key=lambda kv: kv[1], reverse=True)
        for mid, sc in ranked:
            if len(hop2_added) >= HOP2_MAX:
                break
            if sc < MIN_SCORE:
                continue
            m = mem_by_id[mid]
            m_tex = m.get("emotion_texture", "") or ""
            if m_tex and any(
                _texture_similarity(m_tex, s.get("emotion_texture", "") or "") > 0.6
                for s in result if s.get("emotion_texture")
            ):
                continue
            hop2_added.append(m)

        result = result + hop2_added

    # 检索后强化（N2-A: allow_strengthen=False 时跳过，读路径不写回）
    _COOLDOWN_S = 6 * 3600
    _STRENGTH_CEIL_NONCORE = 0.9
    if allow_strengthen:
        ids_to_strengthen = {m["id"] for m in result}
        changed = False
        for mem in memories:
            if mem["id"] in ids_to_strengthen:
                # P0-4: 冷却 + 上限，防 strength 单调爬升
                last = mem.get("last_retrieved") or 0
                if now - last < _COOLDOWN_S:
                    continue
                ceil = 1.0 if mem.get("is_core") else _STRENGTH_CEIL_NONCORE
                retrieval_count_before = mem.get("retrieval_count", 0)
                boost = _RETRIEVAL_BOOST_BASE / (1 + retrieval_count_before)
                mem["strength"] = min(ceil, mem.get("strength", 0.5) + boost)
                mem["retrieval_count"] = retrieval_count_before + 1
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

    if return_trace:
        _selected_ids = {m["id"] for m in result}
        trace_items = []
        for score, mem in scored:
            _hay = (" ".join(mem.get("topic_keywords") or mem.get("tags", []))
                    + " " + " ".join(mem.get("raw_facts", [])))
            trace_items.append({
                "id": mem["id"],
                "score": round(score, 4),
                "hop": 1,
                "kw": sorted(g for g in query_grams if g in _hay),
                "rel": round(rel_map.get(mem["id"], 0.0), 3),
                "sem_sim": round(sem_sim_map.get(mem["id"], 0.0), 3),
                "summary": (mem.get("narrative_summary") or mem.get("summary", ""))[:80],
                "strength": round(mem.get("strength", 0.5), 3),
                "emotion_peak": mem.get("emotion_peak", "neutral"),
                "kw_src": "keyword" if kw_matched_map.get(mem["id"]) else ("semantic" if sem_sim_map.get(mem["id"]) else "facts"),
                "selected": mem["id"] in _selected_ids,
            })
        for mem in hop2_added:
            trace_items.append({
                "id": mem["id"],
                "score": round(cand_scores.get(mem["id"], 0.0), 4),
                "hop": 2,
                "kw": [],
                "rel": round(rel_map.get(mem["id"], 0.0), 3),
                "summary": (mem.get("narrative_summary") or mem.get("summary", ""))[:80],
                "strength": round(mem.get("strength", 0.5), 3),
                "emotion_peak": mem.get("emotion_peak", "neutral"),
                "kw_src": "two_hop",
                "selected": True,
            })
        return result, trace_items
    return result


def delete_episode(user_id: str, ep_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    """Delete one episodic entry by id and cascade-delete its vector.

    Returns True if the entry was found and removed, False otherwise.
    Never raises — caller can treat False as 'not found'.
    """
    try:
        memories = _load_memories(user_id, char_id=char_id)
    except EpisodicCorruptError:
        logger.error("[episodic] delete_episode: file corrupt uid=%s", user_id)
        return False

    original_len = len(memories)
    before_gist = ""
    memories_new = []
    for m in memories:
        if m.get("id") == ep_id:
            before_gist = (m.get("narrative_summary") or m.get("summary", ""))[:120]
        else:
            memories_new.append(m)

    if len(memories_new) == original_len:
        return False  # not found

    _save_memories(user_id, memories_new, char_id=char_id)
    _rebuild_index(user_id, memories_new, char_id=char_id)

    # cascade: remove from vector store (fail-open)
    try:
        from core.memory import vector_store as _vs
        from core.sandbox import safe_user_id as _safe_uid
        _vs.delete(_safe_uid(user_id), char_id, "episodic", ep_id)
    except Exception as e:
        logger.warning("[episodic] vector cascade delete failed ep_id=%s: %s", ep_id, e)
        from core import silent_failure
        silent_failure.note("episodic.vector_cascade_delete", e)

    # provenance
    try:
        from core.memory import provenance_log
        provenance_log.append(
            user_id, char_id,
            artifact="episodic",
            field=ep_id,
            before_gist=before_gist,
            after_gist="",
            trigger_signal="explicit_forget",
            origin={"source": "admin"},
        )
    except Exception:
        pass

    logger.info("[episodic] deleted episode ep_id=%s uid=%s", ep_id, user_id)
    return True


def list_episodes(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> list[dict]:
    """列出该用户/角色下的全部情景记忆条目，供管理面板浏览。按 timestamp 降序排列。"""
    try:
        memories = _load_memories(user_id, char_id=char_id)
    except EpisodicCorruptError:
        logger.error("[episodic.list_episodes] 文件损坏 uid=%s", user_id)
        return []
    return sorted(memories, key=lambda m: m.get("timestamp", 0), reverse=True)


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
    user_pronoun: str = "她",
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
        summary = summary.replace("用户", user_pronoun)

        # P0-3: 用 occurred_at（事件真实时刻）渲染时间锚；旧数据回退 timestamp
        anchor_ts = mem.get("occurred_at")
        if not isinstance(anchor_ts, (int, float)):
            anchor_ts = mem.get("timestamp", now)
        elapsed = max(0.0, now - anchor_ts)
        days = elapsed / 86400
        local_now = datetime.fromtimestamp(now)
        local_then = datetime.fromtimestamp(anchor_ts)
        is_past = mem.get("temporal_ref") == "past"

        if is_past:
            # 回顾型：禁用刚刚/几小时前/今天，只给粗粒度
            if days < 1:
                time_str = "之前"
            elif days < 3:
                time_str = "前几天"
            elif days < 7:
                time_str = "上周"
            elif days < 30:
                time_str = f"大约{int(days)}天前"
            else:
                time_str = f"{int(days // 30)}个月前"
        else:
            if elapsed < 3600:
                time_str = "刚刚"
            elif elapsed < 6 * 3600:
                time_str = "几小时前"
            elif local_then.date() == local_now.date():
                time_str = "今天上午" if local_then.hour < 12 else "今天早些时候"
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
        status = mem.get("status", "open")
        expires_at = mem.get("expires_at")
        is_elapsed = status == "elapsed" or (
            isinstance(expires_at, (int, float)) and now > expires_at
        )
        if status == "resolved":
            resolved_str = "（这件事已经结束了）"
        elif is_elapsed:
            summary = _render_elapsed_summary(summary)
            resolved_str = "（那时说要做的事应该已经发生了）"
        else:
            resolved_str = ""

        core_mark = "【重要】" if mem.get("is_core") else ""
        lines.append(f"- {core_mark}{time_str}，{summary}{resolved_str}{feeling_str}{arc_str}")

    return "\n".join(lines)


def _render_elapsed_summary(summary: str) -> str:
    """Replace relative future anchors only in prompt rendering, preserving stored facts."""
    rendered = re.sub(r"(?:明天|后天|\d{1,3}\s*天后)", "那天", summary)
    return re.sub(r"(?:这周|本周|下周)?周末|下周[一二三四五六日天]", "那天", rendered)


def retrieve_fallback(user_id: str, recent_history: list[str], top_k: int = 1, *, char_id: str = DEFAULT_CHAR_ID, return_trace: bool = False) -> list[dict] | tuple:
    """
    tag 未命中时的兜底召回。不依赖 query，按强度+时间挑近期高强度记忆。
    筛选条件：7天内、strength >= 0.6、不在最近 short_term 内容里。

    return_trace: 若 True，返回 (result, trace_items)。
    """
    try:
        memories = _load_memories(user_id, char_id=char_id)
    except EpisodicCorruptError:
        logger.error("[episodic.retrieve_fallback] 文件损坏，本轮跳过 episodic uid=%s", user_id)
        return ([], []) if return_trace else []
    now = time.time()
    candidates = []
    for m in memories:
        if m.get("status", "open") in ("resolved", "elapsed"):
            continue
        # P0-4: 用 occurred_at（事件真实时刻）卡 7 天窗口
        anchor = m.get("occurred_at")
        if not isinstance(anchor, (int, float)):
            anchor = m.get("timestamp", now)
        age_days = (now - anchor) / 86400
        if age_days > 7:
            continue
        # 核心记忆只经主相关性召回浮现，不参与 fallback 兜底
        # (confab-fixation-loop fix: is_core episodes must not be unconditionally
        # surfaced by fallback every turn — they should only reach the prompt via
        # keyword-matched retrieve())
        if m.get("is_core"):
            continue
        if m.get("strength", 0) < 0.6:
            continue
        summary = m.get("narrative_summary") or m.get("summary", "")
        if any(_is_similar(summary, h) for h in recent_history if h):
            continue
        # 衰减加地板，与主 retrieve 保持一致
        decay = max(0.5, 1.0 / (age_days + 1))
        score = m.get("strength", 0.5) * decay
        expires_at = m.get("expires_at")
        if isinstance(expires_at, (int, float)) and now > expires_at:
            score *= 0.3
        candidates.append((score, m))
    if candidates:
        vals = [c[0] for c in candidates]
        logger.info(
            f"episodic_fallback uid={user_id} pool={len(vals)} "
            f"min={min(vals):.3f} max={max(vals):.3f} "
            f"selected={len([v for v in vals if v >= 0.4])}"
        )
    candidates.sort(key=lambda x: x[0], reverse=True)
    selected = [m for _, m in candidates[:top_k]]
    if return_trace:
        trace_items = [
            {
                "id": m["id"],
                "score": round(score, 4),
                "hop": "fallback",
                "summary": (m.get("narrative_summary") or m.get("summary", ""))[:80],
                "strength": round(m.get("strength", 0.5), 3),
            }
            for score, m in candidates[:top_k]
        ]
        return selected, trace_items
    return selected



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
