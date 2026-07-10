# Brief 51 · Stage 回声坍缩防护：transcript 注入脱敏 + Phase B 回声掐链

> 依赖：无（可与 50 并行；有 50 的 trace 更好观察效果）。
> 参考：Representational Collapse（arXiv 2604.03809）、Diversity Collapse（arXiv 2604.18005）——
> 同底模多角色 + 互读输出（结构耦合）是坍缩最坏配置，Stage 正是这个配置。
> 现状问题：1v1 的防线（`_sanitize_assistant_message` 风格脱敏、反坍缩重试）没覆盖 Stage：
> `4.2_stage_transcript` 层注入其他角色**原始输出**，话剧腔/句式跨角色传染；
> Phase B AI 连环互聊只有 `max_ai_chain_depth` 数量上限，没有内容级止血——两个角色互相
> 附和复述时会把链吃满。

## 1. transcript 注入前脱敏（core/stage/context.py）

渲染 `4.2_stage_transcript`（和 `2.2_stage_presence` 里若含对话摘录）时，对
**非 owner** 条目逐条过 `short_term._sanitize_assistant_message()`（复用，不抄一份）：

- 只影响 prompt 注入视图；`transcript.json` 磁盘原文与 delivery 路径不动
  （与 1v1 "读时清洗、磁盘保真"同一契约）。
- owner 条目原样保留。

## 2. Phase B 回声检测掐链（core/stage/runner.py）

Phase B 每条 AI 回复生成后、追加 transcript 前：

1. 与**上一条 AI 发言**算文本相似度——复用 episodic 写入去重用的同一相似度函数
   （`core/text_match` 现有实现，别造第二套）。
2. 相似度 > `ECHO_SIM_THRESHOLD`（常量，初值 0.55）→ 该回复**丢弃**：不进 transcript、
   不 delivery、整条链就此结束；trace（Brief 50）记 `"echo_cut": true`。
3. Phase A 直接回应 owner 的条目不做回声检测（回应用户内容相近是正常的）。

## 3. Phase B 生成侧软提示

Phase B 链上（`triggered_by` 为角色而非 owner 时），per-character view 的生成 prompt
追加一行 system 指引："回应但不要复述或简单附和上一位的话，说出你自己的看法或岔开"。
放在 stage presence 层内，不新增 prompt 层（避免 Hard Rule 3 的层管理开销）。

## 4. 拍板

- 阈值 0.55 拍死初值，命名常量；调参依据 Brief 50 trace 的 echo_cut 命中率。
- 丢弃而非重试：Phase B 是锦上添花的自主续聊，掐掉比多付一次 LLM 重试便宜且更安全。
- Phase A 不掐、owner 不脱敏，两条边界写进测试钉死。

## 5. 测试

1. mock 生成返回与上条 AI 发言高度相似文本 → 链终止、transcript 无该条、trace 记 echo_cut。
2. 正常低相似链 → 行为与现状一致（回归保护）。
3. transcript 注入视图：话剧腔长动作被清洗，磁盘 transcript.json 逐字节不变，delivery 内容不变。
4. owner 条目在注入视图中原样。
5. `pytest -n auto tests/test_stage*` + 新增测试。

## 6. 不做什么

- 不做 per-character 差异化模型路由（model_registry 已支持 preset 路由，用户想配随时能配，不在本单）。
- 不做 LLM 级"观点多样性"评审（贵；先看掐链+软提示效果）。
- 不动 1v1 反坍缩路径。
