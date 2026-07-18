# Brief 88 · user_hidden_state 现实侧接线：全量信号映射（H1 落地）

> 背景：00e A1。Phase 3-7 基建全在（primitives/decay/consolidate/Dream 快照/afterglow/
> 观测端点 hidden_state_debug），现实对话零写入——系统只被梦境单向喂养。
>
> **拍板：全量映射，不留保守版**（用户明示：保守版会变成永久版）。敢全量的结构性理由：
> 这套状态机自带阻尼——`MAX_NUDGE_PER_EVENT` 单事件封顶、logistic step 越界越钝、
> `current` 持续回归 `baseline`、`baseline` 只由 7d consolidate 慢移。映射激进一点，
> 被吸收的是幅度不是方向；真正的安全边界在 schema 里，不在信号筛选里。

## 0. 事件类型扩展

`RealityEventType` 现有 3 个（SEEK_COMPANIONSHIP / NO_INTERACTION / RECEIVED_COMFORT），
新增 2 个：

- `BODY_TOPIC` — 身体/亲密话题触碰
- `AFFECTION_EXPRESSED` — 用户主动亲昵表达

`integrate_event()` 相应扩 elif 分支（BODY_TOPIC → `nudge_current_sensitivity`；
AFFECTION_EXPRESSED → `discharge_touch_deficit` + 小幅 `nudge_current_sensitivity`），
全部走 `UpdateSource.REALITY_BEHAVIOR`，遵守既有不变量：**integrate_event 只碰中期层**
（sensitivity.current / touch_need.deficit），长期层照旧只归 body_cue / 调度器。

## 1. 🟡 对话侧映射（post_process_slow，零 LLM）

接线点：`pipeline.post_process_slow` 的 detect_emotion 完成后（emotion 可用），
`stamp_user_chat()` 信封，fail-open（任何异常不影响主链）。**trigger 轮
（stamp_trigger）不参与本节任何事件**。判定全部用现成数据（tags / emotion /
presence.json / 常量词表）：

| 事件 | 判定（命中其一即触发） | 效果 |
|---|---|---|
| SEEK_COMPANIONSHIP | (a) 距上次 owner 轮 ≥ 6h 的开场轮（presence.json gap）；(b) 消息命中陪伴意图词表（「在吗/陪我/想你/好想你」级，常量表 `_COMPANIONSHIP_WORDS`） | discharge deficit |
| RECEIVED_COMFORT | 用户消息 tags ∩ {emotion.down, emotion.indirect, topic.health} **且** 本轮 assistant 检测 emotion ∈ {gentle, sad}（安抚交换完成态） | discharge deficit |
| BODY_TOPIC | tags ∩ {body_intimate, physical_closeness, query.body_state}（与 Dream D4.5 门控同一标签集，语义一致） | sensitivity.current +2.0 |
| AFFECTION_EXPRESSED | 消息命中亲昵表达词表（「抱/贴贴/摸摸/亲/牵手」级，常量表 `_AFFECTION_WORDS`） | discharge deficit（小档）+ sensitivity.current +1.0 |

同轮多事件允许并发触发（各自 capped）；数值常量集中一处，注释标明「初值，观察期后可调」。

## 2. 🟡 NO_INTERACTION（调度侧）

挂**现有** `hidden_state_decay` 12h tick（不新建 trigger）：读 presence.json，
gap ≥ 24h → `integrate_event(NO_INTERACTION)` accrue，每逻辑日（rhythm 逻辑日）至多一次，
已触发日期记在 hidden_state.json 旁的小 stamp（或复用 decay tick 自身状态），重启不重复。

## 3. 🟡 body_memory 长期层（integrate_body_cue 接线）

AFFECTION_EXPRESSED / BODY_TOPIC 命中且 `envelope.can_write_memory=True` 时，以命中的
词表词为 cue 调 `integrate_body_cue_and_save`（函数自带权重强化/32 条淘汰，重复出现
自然沉淀为条件化线索）。这是唯一的长期层写入，且完全走既有守卫
（`_assert_not_long_term` 不适用于该路，它本来就是合法长期写者）。

## 4. 🟢 观测与词表

- 观测端点已有（`admin/routers/hidden_state_debug.py`），补充：integrator 触发计数
  （per event_type 累计）进该端点响应，验收「接线后真的在动」。
- 两个词表放 `user_hidden_state_integrator.py` 顶部常量（Hard Rule 9：词表是行为词
  不是角色名，无插值问题）。

## 验收

- 五类事件各自单测（含判定边界：5h59m 不触发 SEEK、trigger 轮零写入、envelope
  can_write_memory=False 时 §3 跳过但 §1 中期层照常）。
- 同轮 BODY_TOPIC+AFFECTION 并发 → 各 delta 独立 capped，总变化 ≤ 2×MAX_NUDGE_PER_EVENT。
- NO_INTERACTION 同一逻辑日重复 tick 只 accrue 一次；重启后不重复。
- decay/consolidate 既有测试零回归；Dream 路径（afterglow/impression）零回归。
- known-issues H1 条目移入已关闭表；`docs/memory.md` hidden_state 段补「现实侧信号映射」
  小节；ARCHITECTURE.md hidden_state 块注释更新（Phase 6 → 接线完成）。
- `pytest -n auto`。

## Commit 划分

1（enum 扩展 + integrate_event 分支）→ 2（对话侧映射）→ 3（NO_INTERACTION）→
4（body_cue + 观测计数）→ 5（文档）。1 前置全部；2/3 可并行。
