# Brief 49 · 闲时整合 pass：memory_janitor 调度触发器

> 依赖：**排在 46/47 之后**（同碰 episodic_memory.py，且合并逻辑要跑在收益递减的
> strength 语义之上）。建议 44 的 memeval 已可用，改前后各跑一遍对比。
> 参考：Letta sleep-time compute——闲时后台整理记忆；v1 只做无 LLM 的便宜清扫，
> LLM 级"重写整合"观察后再立项。
> 现状问题：episodic 写入时只对最近 10 条做文本查重，**历史存量近似重复没人管**；
> 向量库与真相源没有一致性核对，episodic 被 G2 删除后向量可能已删（delete 接口带删向量）
> 但异常路径/历史数据可能残留孤儿。

## 1. 触发器（core/scheduler/triggers/memory_janitor.py，新增）

模式仿 `hidden_state_decay.py`：调度器注册、深夜时段、冷却 24h、`stamp_trigger()`、
不发言、遍历所有注册角色 × 现存 uid 目录（同 P1-0G 的遍历方式）。全程 `uid_lock` 内执行。

## 2. 清扫任务

### (a) episodic 存量近似重复合并

- 全量两两比对 `narrative_summary`（复用写入时去重的同一相似度函数与阈值，别造第二套）；
  ≤200 条规模 O(n²) 可接受。
- 命中合并对：保留 strength 较高者；`source_mid_ids` 取并集；`retrieval_count` 取和；
  `occurred_at` 取较早者；被并者经现有 `delete_episode()` 删除（自动连删向量）。
- **核心记忆（is_core）不参与合并**——无论作为保留方还是被并方。
- 每次合并 `provenance_log.append(artifact="episodic", trigger_signal="janitor_merge")`，
  before_gist/after_gist 记两条 summary。
- 单次运行合并上限 10 对（防首跑存量大时一次动太多，剩余下轮继续）。

### (b) 向量库一致性核对

- 对照 `vec_meta` 与 episodic.json / 近 30 天 event_log：统计孤儿向量数（source_id 无对应真相源条目）。
- 孤儿数 > 20 或占比 > 10% → 调用现有 `rebuild()`（经单 worker executor，Brief 34 契约）；
  否则只记一行观测日志。
- rebuild 失败 fail-open（vector-store.md 既有契约），不影响下轮。

## 3. 拍板

- v1 **零 LLM 调用**：合并只信相似度函数，不做语义改写；identity 一致性重写、facts 语义
  归并留 backlog，等 46 的 digest 质量观察结果一起评估。
- janitor 运行状态（last_run_at、merged_count 累计）记入 `fixation_state.json`，不新建文件。
- 幂等是硬要求：连跑两遍第二遍必须 no-op。

## 4. 测试

1. 种子含 3 组近似重复（其中 1 组一方 is_core）→ 合并 2 组、核心组不动；血缘并集、
   retrieval_count 求和、provenance 落条目正确。
2. 连跑两遍：第二遍零合并、零 rebuild（幂等）。
3. 合并上限：种子 15 组重复 → 首轮只合 10 组。
4. 孤儿向量 25 条 → 触发 rebuild；5 条 → 只记日志。
5. `tests/memeval` 改前后对比无回归；`pytest -n auto` 指定新增测试 + episodic 相关测试。

## 5. 不做什么

- 不做 LLM 级记忆重写/叙事整合（backlog，见拍板）。
- 不动 mid_term / short_term / event_log 的清扫（各有自己的过期机制）。
- 不做跨 uid / 跨 char 的任何合并。
