# Brief 45 · 事实更新语义：ADD/UPDATE/NOOP + 状态变更型 closure

> 依赖：无（可与 44 并行）。46 依赖本工单。
> 参考：Mem0（arXiv 2504.19413）抽取→冲突裁决两阶段；LongMemEval 知识更新类。
> 现状问题：`important_facts` 只追加，episodic closure 只覆盖 72h 内非核心事件。
> "搬家了/换工作了/分手了"这类持久状态变更，新旧事实并存注入 prompt，模型各取一半。

## 1. important_facts 写入侧改冲突裁决（core/memory/user_profile.py）

`extract_and_update` 的提取 prompt 改为：把**现有 facts 列表（带 index）**一并给 LLM，
要求对每条候选新事实输出：

```json
{"op": "add" | "update" | "noop", "target_index": null | int, "text": "...", "tag": "...", "ts": ...}
```

- `add`：现行为不变。
- `update`：调用现有 `overwrite_important_fact(uid, index, text, ...)`（已带 provenance），
  provenance `trigger_signal` 用 `"fact_update"` 与显式遗忘区分。
- `noop`：语义重复，丢弃（顺带解决现在同义事实越攒越多的问题）。
- **守卫**：`target_index` 越界/非 int/op 非法 → 降级为 `add`，WARN 日志，不抛（fail-open）。
- `_compress_facts()` 同步确认与新 schema 兼容。

## 2. episodic 状态变更 closure（core/memory/fixation_pipeline.py + episodic_memory.py）

reflect prompt 已有 `is_closure` / `closure_keywords`。新增字段：

- `is_state_change: bool` — "换了工作/搬了家/分手/戒了"这类**持久状态翻转**，
  区别于"吃完了/考完了"的一次性事件完结。
- `is_state_change=true` 时关闭匹配的旧 open 记忆**不受 72h 窗口限制**（全时段扫描）。
- 其余不变：仍只关非核心（`is_core=True` 保持不可自动关闭）、仍压 strength ≤ 0.2、
  仍写 `resolved_at` / `resolved_by`。

## 3. 拍板

- 核心记忆的矛盾**不自动处理**：只在 provenance 里记一条 `"conflict_with_core"` 观测日志，
  人工经 G2 删除 API 处理。自动改写核心记忆的风险大于收益。
- user_facts（全局域）已是 keyed 覆盖语义，不动。
- user_identity 的矛盾走既有 counter_evidence 机制（47 修其衰减），本 brief 不碰。

## 4. 测试

1. update：种子 "住在北京" + 新输入 "搬到上海了" → facts 里只剩上海条目，tag 保留，provenance 落一条 fact_update。
2. noop：语义重复事实不新增条目。
3. 越界 index → 降级 add，无异常。
4. state_change：5 天前的 open episodic "准备换工作" 被今天的 "入职新公司" 关闭（越过 72h）；`is_core=True` 同场景不被关闭。
5. Brief 44 的 knowledge_update xfail 用例翻转为通过。
6. `pytest --testmon` 或指定 `tests/test_fixation_pipeline.py` + 新增测试文件，不跑全量。

## 5. 不做什么

- 不引入知识图谱/时间区间字段（Zep 式 t_valid/t_invalid），现有 resolved 机制够用。
- 不做 facts 的自动合并重写（留给 49 观察后决定）。
- 不改 episodic 去重双防线。
