# 施工单 01 — 修复情景召回第一跳（中文 n-gram）

> 给 CC 的执行单。对应审计文档 `docs/memory-recall-audit.md` 的**发现 A**。
> 证据：`recall_trace` 里所有 `episodic_hits[].kw == []`，分数清一色 = strength×decay，
> 召回与 query 内容无关 → 第一跳因 `.split()` 对中文整句不分词而全空。
> 目标文件：`core/memory/episodic_memory.py`（主改）、`core/memory/event_log.py`（抽公共函数）。

---

## 1. 根因（一句话）
`retrieve()` 候选匹配用 `topic.split()`，中文无空格 → 整句变一个 token →
拿整句去 `topic_keywords + raw_facts` 做 substring → 几乎永不命中 →
`candidate_ids` 空 → 走"全量按 strength 评分"。

## 2. 改动总览（4 步）
1. 抽一个共享 n-gram 工具函数，episodic 和 event_log 共用（顺手还掉审计里的"双匹配口径不一"债）。
2. `retrieve()` 候选匹配：`topic.split()` → n-gram 命中。
3. 重新标定 `relevance_bonus` 的归一化（n-gram 命中数会比原来大很多）。
4. trace 里加 `kw_legacy` 字段做 A/B（新旧口径同轮对比），跑几天后再删。

---

## 3. 具体改法

### 步骤 1：抽公共 n-gram 函数
新建 `core/text_match.py`（或放进现有 util；走项目惯例）：
```python
def ngram_tokens(text: str, lengths=(2, 3, 4)) -> set[str]:
    """中文友好的 n-gram 切词。复用自 event_log.search 的切法。"""
    out: set[str] = set()
    t = (text or "").strip()
    for n in lengths:
        for i in range(len(t) - n + 1):
            chunk = t[i:i+n]
            if chunk.strip():
                out.add(chunk)
    return out
```
然后把 `core/memory/event_log.py` 里 search() 现有的那段 2/3/4-gram 循环替换成
`keywords = ngram_tokens(q)`，保证两处口径完全一致。**不改 event_log 的评分逻辑**，只换切词来源。

### 步骤 2：`retrieve()` 候选匹配换 n-gram
`core/memory/episodic_memory.py` 约 273–286 行，把：
```python
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
```
改成：
```python
if topic:
    from core.text_match import ngram_tokens
    query_grams = ngram_tokens(topic)
    for mem in memories:
        keywords_text = " ".join(mem.get("topic_keywords") or mem.get("tags", []))
        facts_text = " ".join(mem.get("raw_facts", []))
        haystack = keywords_text + " " + facts_text
        matched = {g for g in query_grams if g in haystack}
        if matched:
            candidate_ids.add(mem["id"])
            hit_counts[mem["id"]] = len(matched)   # 命中的“去重 n-gram 数”
```
要点：用 `set` 去重命中的 n-gram，避免重复 chunk 把计数灌爆。

### 步骤 3：重标定 `relevance_bonus`
当前（约 305 行）：`relevance_bonus = 0.2 * min(hit_counts.get(id,0) / 3, 1.0)`。
n-gram 下命中数普遍比 3 大很多，`/3` 会瞬间打满、失去区分度。改成**按 query 规模归一化**：
```python
_gram_total = max(1, len(query_grams)) if topic else 1   # query 的总 n-gram 数
relevance_bonus = 0.2 * min(hit_counts.get(mem["id"], 0) / _gram_total, 1.0)
```
即"命中比例"而非"命中绝对数"。`query_grams` 需在评分循环外算一次并传进来。

### 步骤 4：trace 加 A/B 字段
`core/memory/episodic_memory.py` 约 379–397 的 `return_trace` 段，
把 `kw` 改成 n-gram 命中，并**额外加一个** `kw_legacy`（旧 `.split()` 口径）：
```python
if return_trace:
    from core.text_match import ngram_tokens
    _grams = ngram_tokens(topic) if topic else set()
    _legacy = topic.split() if topic else []
    _selected_ids = {m["id"] for m in result}
    trace_items = []
    for score, mem in scored:
        _hay = (" ".join(mem.get("topic_keywords") or mem.get("tags", []))
                + " " + " ".join(mem.get("raw_facts", [])))
        trace_items.append({
            "id": mem["id"],
            "score": round(score, 4),
            "hop": 1,
            "kw": sorted(g for g in _grams if g in _hay),        # 新：n-gram 命中
            "kw_legacy": [w for w in _legacy if w in _hay],       # 旧：split 命中（A/B 用，验完删）
            "summary": (mem.get("narrative_summary") or mem.get("summary", ""))[:80],
            "strength": round(mem.get("strength", 0.5), 3),
            "emotion_peak": mem.get("emotion_peak", "neutral"),
            "selected": mem["id"] in _selected_ids,
        })
    return result, trace_items
```

---

## 4. 风险与护栏
- **2-gram 噪声**：像"我们""什么""不过"这类高频 2-gram 可能误命中。
  先按上面最简版上线，**用 trace 观察**；若发现明显噪声召回，再加：① 只用 3/4-gram，
  或 ② 一个小停用词集过滤 2-gram。**不要一上来就过度设计**，让 trace 说话。
- **候选集变小是预期效果**：修好后大量 query 会真正命中，`candidate_ids` 不再落空，
  "全量按 strength" 这条兜底分支应明显少触发——这正是成功信号。
- **不要动**：`MIN_SCORE=0.15` 阈值、emotion_texture 去重、N2-A 的 `allow_strengthen=False`
  读路径不写回约定。本单只换"候选怎么选"和"relevance 怎么算"。

## 5. 验收（用 trace 自验，不靠感觉）
1. 跑 `pytest tests/test_*episodic* 2>/dev/null || pytest`（确认没破现有用例）。
2. 真实对话几轮后看 `data/runtime/memory/yexuan/1043484516/recall_trace/{今天}.jsonl`：
   - `kw` 应开始有命中（不再全空）；
   - 对比同一条记录的 `kw` vs `kw_legacy`，应能看到"新口径命中、旧口径为空"；
   - `selected:true` 的记忆应与 query 内容更相关（强度高但离题的记忆排名应下降）。
3. 确认改了 `tag_rules.py` 才需跑 `tests/run_eval.py`——本单**没动** tag_rules，可跳过。
4. 观察 2–3 天 trace，确认无明显 2-gram 噪声后，删除 `kw_legacy` 字段。

## 6. 下一步（不在本单范围）
第一跳稳定、trace 里 `kw` 有质量后，再开**施工单 02：关键词二跳**
（复用 `_load_index` 倒排索引 + 审计文档里的四条护栏）。二跳的阈值要等本单的 trace 数据出来才好定，
故本单完成前不预写。
