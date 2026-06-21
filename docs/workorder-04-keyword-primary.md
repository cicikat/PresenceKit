# 施工单 04 — 关键词优先匹配（topic_keywords 主信号，raw_facts 弱辅证）

> 给 CC 的执行单。**前置**：03b（截断抢救 + 写入硬化）已完成；01/02/03 已落地。
> 仍**不做二跳**（二跳=施工单 05，本单后再做）。
> 目标文件：`core/memory/episodic_memory.py`（`retrieve()` 候选段 + 评分段）。

---

## 0. 为什么是这单（03 后 trace 实测）
03 把精度拉上来了，但残留一类噪声：**偶然/语法碎片词**虽稀有却话题无关，仍能拽出固定记忆。
逐词核对它们在记忆里的落点，结论很干净：

| gram | 在 topic_keywords | 仅在 raw_facts | 判定 |
|---|---|---|---|
| 到了 | 0 | 12 | 噪声 |
| 一起 | 0 | 1 | 噪声 |
| 时间 | 4 | 4 | 混 |
| 睡觉 | 4 | 2 | 信号 |
| 依赖 | 2 | 0 | 信号 |
| 情绪 | 14 | 2 | 信号 |

**噪声来自命中 `raw_facts`（逐字句子，混入大量偶然词）；信号来自命中 `topic_keywords`（LLM 提炼的概念）。**
典型坏例（03 trace）：
- 「快去看时间！」→ `时间`(facts) → 布洛芬/亲密/低烧
- 「熬到了五点」→ `到了`(facts) → AI情感/田园/期望
所以这单：**topic_keywords 命中为主信号，raw_facts 命中降权为弱辅证，纯 facts 单词命中不足以单独入选。**

---

## 1. 改法

### 步骤 1：候选段 —— 分开记录 keyword 命中与 facts 命中
把 03 的候选 for 循环（`for mem in memories:` 那段）改成分别匹配：
```python
candidate_ids = set()
matched_map: dict = {}      # mem_id -> set(全部命中 gram)
kw_matched_map: dict = {}   # mem_id -> set(命中 topic_keywords 的 gram)
df: dict = {}
query_grams: set = set()
idf: dict = {}
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

    for mid, matched in matched_map.items():
        kwm = kw_matched_map.get(mid, set())
        # 主证据：命中关键词里的具体词，或命中≥2个关键词
        if (kwm & specific) or (len(kwm) >= 2):
            candidate_ids.add(mid)
        # 弱辅证：纯 facts 命中要更强（≥2个不同具体词）才入选，挡掉“到了/一起”这类单词偶然命中
        elif len(matched & specific) >= 2:
            candidate_ids.add(mid)

if topic and not candidate_ids:
    logger.debug("[episodic.retrieve] 无足够关键词证据，主召回返回空，交给 fallback uid=%s", user_id)
    return ([], []) if return_trace else []
```

### 步骤 2：评分段 —— facts 命中降权
把 03 的 `idf_sum` 那行改成关键词全权、facts 命中 ×0.3：
```python
FACTS_WEIGHT = 0.3   # 纯 facts 命中的折扣（据 trace 可调）
_kwm = kw_matched_map.get(mem["id"], set())
_fact_only = matched_map.get(mem["id"], set()) - _kwm
idf_sum = sum(idf.get(g, 0.0) for g in _kwm) + FACTS_WEIGHT * sum(idf.get(g, 0.0) for g in _fact_only)
relevance_norm = min(1.0, idf_sum / REL_SCALE)
score = strength * decay * (0.4 + 0.6 * relevance_norm) + emotion_bonus + 0.3 * relevance_norm
```
（`REL_SCALE`、闸门系数沿用 03，不动。）

### 步骤 3：trace 体现来源
`return_trace` 段每条 item 加 `"kw_src"`，标出命中来自哪：
```python
"kw_src": "keyword" if kw_matched_map.get(mem["id"]) else "facts",
```
方便你核对"还有没有纯 facts 噪声漏网"。

---

## 2. 预期效果（对照坏例）
- 「快去看时间！」：`时间` 若只在那几条的 facts 里 → 纯 facts 单词 → 不入选；只有 `时间` 真在 topic_keywords 的记忆才可能入选。布洛芬/亲密/低烧三件套消失。
- 「熬到了五点」：`到了` 纯 facts 单词 → 不入选 → 不再吐 AI情感/田园。
- 「依赖/睡觉/情绪/怀念」：在 topic_keywords 里 → 继续稳定命中。

## 3. 风险与护栏
- **可能漏掉只在 facts 出现的真信息**（如某具体事件名只在 raw_facts、没被抽进 keywords）。
  这类仍可经"≥2个具体 facts 词"入选；若发现误杀，把 `FACTS_WEIGHT` 调到 0.5 或放宽辅证门槛到"1个具体 facts 词 + 命中数≥2"。先用默认值，**看 trace 的 `kw_src` 分布**再调。
- 长期：`时间`混在 keywords 里这类（4 条）是 LLM 抽词不够干净，属另一条线（抽词质量），本单不处理。
- **不要动**：emotion_texture 去重、N2-A `allow_strengthen=False`、emotion_bonus、decay、MIN_SCORE。

## 4. 验收（trace 自验）
重启后跑若干轮：
1. `时间`/`到了`/`一起` 不再作为单独命中拽出离题记忆；`kw_src` 里 facts-only 选中项应大幅减少。
2. 真关键词（依赖/睡觉/情绪/怀念/记忆）仍稳定命中、`rel` 高。
3. `pytest` 通过；未动 `tag_rules.py`，跳过 `run_eval.py`。

## 5. 下一步
关键词优先达标后 → **施工单 05：关键词二跳**。
种子（hop-1）此时是干净的 topic_keywords 具体词，经 `_load_index` 倒排索引扩到共享关键词的记忆，
护栏沿用 `docs/memory-recall-audit.md` 四条（二跳分数衰减、只补1–2条、过 MIN_SCORE、不拉回 resolved/低强度）。
