# Brief 55 · 角色↔角色关系存储（char_relations，v2 可延后）

> 依赖：**54**（挂在其投影 hook 之后）。Stage 系列最后一张，观察 50–54 效果后再跑也行。
> 参考：生成式 agent 关系记忆（Park et al.）、EvoSpark 长时程叙事。
> 现状问题：identity / impression 全是 char↔owner 维度。群聊产生角色间关系
> （"上次你们俩吵过"），现在无处落、下次群聊全忘。

## 1. 存储（新文件 core/stage/char_relations.py）

路径：`data/runtime/relations/{char_a}__{char_b}.json`（pair 按字典序排序保证唯一；
经 `core/data_paths.py` 新增访问器，Hard Rule 1）。**双向条目**存在同一文件：

```json
{
  "a_of_b": {"summary": "≤60字第三人称印象", "valence": 0.3, "updated_at": ...},
  "b_of_a": {...},
  "interaction_count": 12,
  "last_interaction_ts": ...
}
```

- valence ∈ [-1, 1]，只是软倾向，不做任何行为门控。
- 与 owner 的关系**不进此库**（那是 identity/impression 的领地，边界写死在模块 docstring）。

## 2. 更新（slow_queue 新任务 update_char_relations）

1. `run_reality_stage_turn()` 投影完成后，找出本轮**直接互相回应过**的角色对
   （transcript 中 `triggered_by` 链相邻的 AI-AI 对）。
2. 每对检查冷却：`last_interaction_ts` 距今 < 6h → 只 `interaction_count += 1`，不调 LLM。
3. 过冷却的对入队：handler 一次 LLM 调用，输入 = 本轮两人的对话摘录 + 旧双向 summary，
   输出新的双向 summary + valence。
4. 写入前 `WriteEnvelope.can_write_memory` 门控；每次 summary 实际变化
   `provenance_log.append(artifact="char_relation", trigger_signal="stage_interaction")`（Hard Rule 6）。
5. LLM 失败 → 保留旧值，只更新计数（fail-open）。

## 3. 注入（core/stage/context.py）

`2.2_stage_presence` 渲染时，对在场角色的每个 pair 追加一行：
`{A}对{B}的印象：{summary}`（双向各一行；无记录不渲染）。名字经 `get_char_name` 插值。
该层已有 `_layer` 字段，无新层（Hard Rule 3 不触发）。

## 4. 拍板

- 冷却 6h、summary 60 字上限拍死初值，命名常量。
- 全局存储（跨群共享）：A 和 B 在群 1 吵过架，群 2 里也该记得——关系挂在角色对上，
  不挂在群上。
- G2 显式遗忘同步：新增 `delete_relation(char_a, char_b)` + admin DELETE 端点
  （仿 `admin/routers/memory.py` 模式，记 explicit_forget provenance）。

## 5. 测试

1. 一轮含 AI-AI 直接互动 → 任务入队、双向 summary 写入、provenance 落条目。
2. 6h 冷却内二次互动 → 只加计数，零 LLM 调用。
3. 注入：在场 pair 有记录 → presence 层含两行印象；无记录 → 不渲染。
4. owner 相关条目永不写入（负向测试）。
5. `is_test` envelope → 拒写。
6. delete_relation → 文件删除 + provenance。

## 6. 不做什么

- 不做关系图谱/多跳推理（两两印象足够 v2）。
- 不让 valence 影响 arbiter 打分（观察一段时间，将来若加也是独立工单）。
- 不回填历史 transcript（从接入日起前向积累，与 provenance 同哲学）。
