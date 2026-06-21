# 施工单 05 — 关键词二跳召回（保守版，flag 可关）

> 给 CC 的执行单。**前置**：01–04 已落地并验证；第一跳精度已达标（04 trace：selected 命中 keyword=32 / facts=5，
> 依赖/撒娇/休息/冷萃酸奶/推荐 等均精确命中）。种子已干净，二跳才有意义。
> 目标文件：`core/memory/episodic_memory.py`（`retrieve()` 选出 `result` 之后）、`config.yaml`。

---

## 0. 二跳是什么、为什么现在能做
- hop-1（现状）：query → 命中 topic_keywords 的记忆（精确但**窄**，只浮出直接相关的）。
- hop-2（本单）：从 hop-1 选中的记忆出发，经倒排索引 `_load_index`（`keyword -> [mem_ids]`，已存在且随写入 `_rebuild_index` 维护）
  找到**共享具体关键词**的其他记忆，补 1–2 条联想记忆。
- 现在能做的前提：hop-1 种子已是干净的具体词（如 `依赖`/`撒娇`），从它们扩散不会像早期那样把"叶瑄→通用三件套"铺开。

## 0.1 先补一个小去噪（顺手）
04 trace 残留的纯功能词碎片 `自己的`/`己的` 仍偶尔入选（多在主动触发、facts 源）。
在 `core/text_match.py` 的 `STOP_GRAMS` 里加：`"自己的", "己的", "这种", "那种", "一下", "这件", "那件"`。
> 注意：**不要**把 `时间`/`昨天` 加进停用集——它们有时就是真话题（如"凌晨问时间被警告睡觉"是真相关记忆）。
> 这类时间/指代词的“一词多义”噪声是词面匹配的天花板，只有语义检索能根治，本单不强求。

---

## 1. 二跳实现（在 `result` 选定后插入）
在 `retrieve()` 里 `result = selected`（约 375 行）之后、强化写回之前插入：
```python
# ── hop-2：关键词二跳（保守、flag 可关）────────────────────────────────
from core.config_loader import get_config
_two_hop = get_config().get("episodic", {}).get("two_hop_enabled", False)
hop2_added: list = []
if _two_hop and topic and result:
    HOP2_MAX = 2          # 最多补 2 条
    HOP2_DECAY = 0.4      # 二跳分数相对种子的衰减
    index = _load_index(user_id, char_id=char_id)   # {keyword: [mem_id,...]}
    mem_by_id = {m["id"]: m for m in memories}
    _seed_ids = {m["id"] for m in result}
    _already = _seed_ids | candidate_ids            # 不重复召回 hop-1 候选

    # 种子的“具体关键词”才用于扩散（避免从泛词漂移）
    cand_scores: dict = {}   # mem_id -> 累计二跳分
    for seed in result:
        seed_rel = rel_map.get(seed["id"], 0.5) if 'rel_map' in dir() else 0.5
        for kw in (seed.get("topic_keywords") or []):
            if kw not in specific:          # 只从具体词扩散（specific 来自 hop-1 段）
                continue
            for mid in index.get(kw, []):
                if mid in _already or mid not in mem_by_id:
                    continue
                m = mem_by_id[mid]
                if m.get("status", "open") in ("resolved", "elapsed"):
                    continue
                if m.get("strength", 0) < 0.5:        # 不拉回低强度
                    continue
                # 二跳分：种子相关性 × 衰减 × 记忆自身强度，按共享具体词累加
                cand_scores[mid] = cand_scores.get(mid, 0.0) + \
                    seed_rel * HOP2_DECAY * m.get("strength", 0.5)

    # 过 MIN_SCORE，取前 HOP2_MAX，并做 emotion_texture 去重（与 hop-1 selected 比）
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
            continue   # 与已选过于相似则跳过
        hop2_added.append(m)

    result = result + hop2_added
```
> 依赖项：`rel_map` 需在 04 评分段把 `relevance_norm` 存进去（`rel_map[mem["id"]] = relevance_norm`）；
> 若未存，用默认 0.5 兜底（代码已含 `if 'rel_map' in dir()` 守卫，但建议正式存一份）。
> `specific`、`candidate_ids`、`MIN_SCORE`、`_texture_similarity` 均为 hop-1 段已有变量/函数。

## 2. trace 标记 hop=2
`return_trace` 段：给 `hop2_added` 里的记忆，trace item 的 `"hop"` 写 `2`、`"kw_src"` 写 `"two_hop"`，
其余字段照常。这样你能在 trace 里直接看二跳补了哪几条、是否相关。

## 3. config 开关
`config.yaml` 加：
```yaml
episodic:
  two_hop_enabled: false   # 默认关；置 true 开二跳做对比测试
```

## 4. 风险与护栏（已内置，复述备查）
- **只从具体词扩散**（`kw in specific`）——泛词不扩散，挡主题漂移。
- **HOP2_DECAY=0.4 + 过 MIN_SCORE**——二跳分天然低于种子，弱关联进不来。
- **最多 2 条 + emotion_texture 去重**——不喧宾夺主、不和 hop-1 重复。
- **排除 resolved/elapsed/strength<0.5**——不拉回已了结/边缘记忆。
- **flag 默认关**——随时可回滚；读路径仍 `allow_strengthen=False`，二跳不写回 strength（保持 N2-A）。

## 5. 验收（trace 自验，A/B）
1. `two_hop_enabled: false` 跑几轮存基线 → 置 `true` 跑同类对话。
2. 看 trace 里 `hop=2` 的条目：**是否补到了"种子的相关联记忆"**（如种子=依赖，二跳补到"依赖并担忧/被遗忘"等同簇），
   而不是不相关漂移。若漂移多 → 调高 HOP2_DECAY 门槛或把 `specific` 收紧。
3. 确认二跳没有把回复带偏（主动触发尤其留意，结合 PB1：别让二跳又把一堆旧情绪记忆翻出来）。
4. `pytest` 通过。

## 6. 这之后
二跳验证有价值后，才考虑 `docs/memory-recall-audit.md` 里的**网状物化**（`memory_edges.json` 显式带权边）——
仅当你需要"共现统计权重/非关键词关联/多跳"时。否则二跳（隐式图深度2）已够，不必上重型方案。
词面匹配的"一词多义"天花板（时间/昨天类）若要根治，是另一条线：**语义/embedding 检索**，独立评估。
