# Brief 47 · 记忆稳定性小修：召回增强收益递减 + identity 反证时间衰减

> 依赖：无（可与 44/45/48 并行）。与 46/49 同碰 `episodic_memory.py`，合并时注意串行。
> 参考：SSGM（arXiv 2603.11768）记忆漂移稳定性治理。
> 两处都是仓库已自知的风险点：short_term 文档标注的"回忆永动机"、known-issues `identity-1`。

## 1. 召回增强收益递减（core/memory/episodic_memory.py::retrieve 副作用段）

现状三个正反馈叠加：每次召回 `strength += 0.15`（无递减）→ retrieval_count 增加 →
decay 变慢（`recall_factor`）→ 更容易再被召回；外加 emotion_bonus 与 `nudge_from_memory`
的情绪⇄记忆小环。高强度情绪记忆一旦进入循环基本永不衰减、轮轮浮现
（memory-recall-audit.md 实测过"同 3 条高强度情绪记忆每轮被选中"）。

改法：

```
boost = 0.15 / (1 + retrieval_count)      # 首次 +0.15，第 5 次 +0.025，渐近归零
```

- `retrieval_count` 用增强**前**的值；strength clamp ≤ 1.0 不变。
- `recall_factor`（decay 减缓）与 `nudge_from_memory` 不动——只掐"强度只增不减"这一条腿，
  一次只动一个变量，便于用 recall_trace 对比观察。

## 2. identity 反证时间衰减（core/memory/fixation_pipeline.py::_synthesize_identity）

现状（known-issues identity-1）：`counter_evidence_count` 只在 LLM 重写 text 时归零，
否则只增不减；模型长期保守时维度可能被历史反证永久压死。

改法：consolidate 执行时，读入每个维度后先按 `last_conflict_at` 做半衰期衰减再参与判断：

```
decayed = counter_evidence_count * 0.5 ** (days_since_last_conflict / 30)
```

- 衰减结果**写回磁盘**（floor 到整数，<1 归零并清 `last_conflict_at`）。
- `last_conflict_at` 缺失（旧数据）→ 不衰减，保持现值（兼容层，不猜）。
- 写回时 `provenance_log.append(artifact="identity", field=维度key, trigger_signal="counter_decay")`
  ——仅在值实际变化时记（Hard Rule 6）。

## 3. 拍板

- 半衰期 30 天、递减公式 `1/(1+n)`：拍死初值，常量命名放模块顶部，调参不改逻辑。
- 不引入"每日 boost 预算"状态文件——递减公式无状态、幂等，够用。

## 4. 测试

1. 同一条记忆连续召回 10 次：strength 增量总和 < 0.5（对照现行为 1.5），且 clamp 生效。
2. 召回 0 次的记忆行为与现行为逐字节一致（回归保护）。
3. counter=4、last_conflict_at=60 天前 → consolidate 后 counter=1；30 天前 → 2；缺 last_conflict_at → 4 不变。
4. 衰减写回落 provenance；无变化时不落。
5. 跑 `tests/memeval`（Brief 44）确认无召回质量回归。

## 5. 不做什么

- 不动 emotion_bonus、decay_all 的分情绪衰减率、fallback 召回逻辑。
- 不做维度级"复活通知"（衰减后重新过阈值属正常固化流程）。
