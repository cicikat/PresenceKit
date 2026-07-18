# Brief 80 · storyline 叙事弧层：周频聚合 + tagged 召回 + memory_digest 归并退役

> 前置：00d 备忘（含 §五 裁决结果）已定案——**增量式 + 旧节点只读只追加**、
> **storyline 吸收 digest_evicted_episodes、memory_digest.md 退役**。Brief 79（event_log
> 来源标记 + salvage 过滤，`56d4c28`/`b9c7e35`/`e789f1e`）已落地，来源过滤基础设施就绪。
> 本单是 00d 拆单顺序的最后一张：storyline 主单。

## 定位（一句话）

identity.yaml 回答「他是个什么样的人」（稳定属性），storyline 回答「他在经历什么弧线」
（有时间跨度的叙事：职业转变、一个项目的推进、一段持续的情绪过程）。周频后台聚合，
tagged 召回，永不常态注入。

## 1. 🟡 存储层：storyline.json（append-only 节点）

- 路径：`data/runtime/memory/{char_id}/{uid}/storyline.json`，走 `core/sandbox.get_paths()`
  新增 `storyline()` 方法 + `path_resolver`（`MemoryScope.reality_scope`），同现有主记忆惯例。
- Schema（version 字段留升级余地）：

```json
{
  "version": 1,
  "meta": {"last_aggregated_at": 0.0, "event_log_cursor": "YYYY-MM-DD"},
  "arcs": [
    {
      "arc_id": "arc_xxxxxxxx",
      "title": "≤20字弧线标题",
      "status": "active | dormant | closed",
      "tags": ["topic.work"],
      "nodes": [
        {"node_id": "n_xxxxxxxx", "ts": 0.0, "span": [0.0, 0.0],
         "summary": "≤80字该阶段发生了什么", "source_ids": ["ep_...", "el:2026-07-01"]}
      ],
      "created_at": 0.0, "updated_at": 0.0
    }
  ]
}
```

- **append-only 硬约束（00d 裁决 1，必须可测）**：模块公开写 API 只有
  `open_arc()` / `append_node()` / `set_arc_status()`；**不存在**修改既有 node 的公开路径。
  `append_node` 内部校验 node_id 不与已有重复、ts 不早于该 arc 最后一个 node（防伪造历史）。
- `tags` 取值必须来自 `core/tag_rules.py` 现有受控 tag 集（聚合 prompt 里给出可选清单让 LLM
  选择，落盘前用集合校验过滤非法值）——**v1 不新增 tag、不改 tag_rules**，因此不触发
  `run_eval` 义务。
- 上限与淘汰：active ≤ 8、总 arcs ≤ 24、每 arc nodes ≤ 40。超限时最旧的 closed arc 整条
  追加写入 `storyline_archive.md`（纯归档，无读取方，同 memory_digest 退役后的归宿定位）
  再移除。nodes 超限拒绝 append（弧线该 close 了，聚合 prompt 会被告知余量）。
- 每次写入按 Hard Rule 6 调 `provenance_log.append()`（artifact="storyline"，
  field=arc_id，fail-open）。

## 2. 🟡 聚合器：scheduler 周频 trigger（不挂慢队列——00d §三.2）

- 新文件 `core/scheduler/triggers/storyline_weekly.py`，照 `hidden_state_decay` 的
  consolidate tick 模式：**7d 冷却**（per char_id+uid）、深夜/闲时窗口（同 `memory_janitor`
  的时段判断）、`stamp_trigger()`、**不发言、不进 pipeline**。
- 每次运行**一次 LLM 调用**，输入三路：
  1. 上次聚合后新增的 episodic 条目（读 `meta.last_aggregated_at` 之后的，含已
     consolidated 的——identity 固化与 storyline 聚合互不排斥，同一碎片可以既沉淀属性
     又构成弧线节点）；
  2. `storyline_inbox.json` 的淘汰批次碎片（见 §3）；
  3. `event_log` 自 `meta.event_log_cursor` 以来的日文件——**跳过 meta 含 `source:` 非空的
     块**（复用 Brief 79 标记，实现可直接搬 `event_log_salvage` 的过滤写法）。
- Prompt 要求：**按事件边界聚类**（不按时间桶，00d §三.7 / ES-Mem 结论）；输出 ops 列表
  `[{op: "open_arc"|"append_node"|"set_status", ...}]`，代码逐条经 §1 的写 API 落盘——
  LLM 不直接产出全量文件（防重写旧节点，这正是 append-only 的执行面）。
- **类型隔离（00d §三.3，写入路由层）**：
  - 聚合 prompt 显式排除「稳定属性断言」（"他是个早睡的人"→ 不产出，那是 identity /
    important_facts 的职责），只产出「有时间跨度的过程」。
  - **同单顺手**：`consolidate_to_identity` 的 prompt 加一句对称排除——「有明确时间弧线的
    过程性叙事不写入 identity，另有 storyline 层负责」。一行 diff，防双写打架。
- LLM 失败 / 输出不合法：本轮放弃、不动 cursor、下周重来（fail-open，聚合是幂等增量）。

## 3. 🟡 memory_digest 归并退役（00d 裁决 2，删除纪律）

- `episodic_memory.py:225` 附近的 `slow_queue.enqueue("digest_evicted_episodes", ...)` 改为
  enqueue `"storyline_evicted_input"`：新 handler 只做一件事——把淘汰批次的
  `{id, summary, ts, strength}` 追加进 `storyline_inbox.json`（上限 200 条滚动，fail-open），
  等周频聚合消费后清空。**不再有淘汰即时 LLM 调用**（原 digest 的 LLM 压缩由周频聚合统一做，
  省一路 LLM）。
- 删除：`fixation_pipeline.py` 的 `digest_evicted_episodes` / `handler_digest_evicted_episodes`
  （约 §1026-1113）、`pipeline.py:1731/1738` 的 handler 注册、**连同其测试与文档条目一起删**
  （AGENTS.md 删除纪律：不留僵尸测试）。
- 存量 `memory_digest.md` 文件原地保留只读，不删数据、不新增读取逻辑（00d 裁决 2 原文）。

## 4. 🟡 注入层：`6h_storyline`（tagged，非常态）

- `fetch_context()` 并发段加载 storyline（同步读 JSON，量小）；`build_prompt()` 新层：
  - 门控：当前轮 `_tags ∩ arc.tags` 非空的 **active/dormant** arc 里取交集数最多的**一条**；
    backchannel 低信息轮跳过（复用 `recall_gate.is_low_information()`，同 diary 闸）。
  - 内容：arc title + 最近 ≤3 个 node 的 summary，总长 ≤300 字，框定语「这是你记得的
    一段持续经历，不是此刻发生的事」。
  - 元数据：`_layer: "6h_storyline"`、`_drop_priority: 65`（mid_term 40 与 episodic 70 之间，
    叙事质量高于中期摘要、低于精筛情景记忆）、`_provenance: {mode: "tagged", matched_tags: ...}`
    （照 3.8_growth_self 的写法）。
  - R4-C checklist 全项过一遍（可裁层声明 int priority；`sanitize_messages` 自动剥离，无需改）。
- 层激活可观测：`debug_info` 的 layers 记录自动涵盖（`_layer` 字段已带），无额外工作。

## 5. 🟡 观测端点（Hard Rule 7，没有观测端点的落盘物不可验收）

- `GET /storyline/state?uid=&char_id=`（admin 只读，scope 取与 memory 观测同级）：返回 arcs
  概要（id/title/status/node 数/updated_at）+ `meta`。inbox 也在响应里给条数。

## 验收

- append-only：对已有 node 的任何改写路径不存在；`append_node` 拒绝 ts 回退与重复 node_id
  （单测）。
- 聚合：构造 episodic + inbox + 带 `source:web` 块的 event_log 输入 → ops 正确落盘、
  source 块未进 LLM 输入、cursor 前进；LLM 输出坏 JSON → 状态零变化（fail-open 断言）。
- 类型隔离：聚合 prompt 含属性排除语；identity prompt 含弧线排除语（存在性断言即可）。
- 注入：tag 命中 → 单条 arc 注入、priority 65 参与裁剪链；无命中/backchannel → 不注入；
  20k 超限时按 65 顺位被裁（裁剪回归）。
- digest 退役：原 handler/测试/文档条目零残留（grep 断言 `digest_evicted_episodes` 只在
  归档文档/CHANGELOG 出现）；淘汰批次进 inbox 不再产生即时 LLM 调用。
- `pytest -n auto`；不改 tag_rules，无 run_eval 义务。
- 文档同步：`docs/memory.md`（新节 + 记忆层一览表加行 + 固化流向图加 storyline 分支）、
  `docs/prompt-layers.md`（层表 + `_drop_priority` 表加 65 行）、AGENTS.md 关键文件速查、
  ARCHITECTURE.md slow_queue handler 列表（digest 移除）。

## Commit 划分（依赖顺序）

1. 存储层 + 写 API + 观测端点（§1 + §5）
2. 聚合 trigger + identity prompt 排除（§2，依赖 1）
3. digest 归并退役 + inbox（§3，依赖 2）
4. 注入层（§4，依赖 1，可与 2/3 并行）
5. 文档同步（最后）
