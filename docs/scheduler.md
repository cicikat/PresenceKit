# docs/scheduler.md — 调度器设计

---

## 定位

调度器负责**叶瑄的主动行为**——不等用户发消息，自己在合适的时机触发。

```
core/scheduler/loop.py       ← 主循环 + 工具函数 + 冷却管理
core/scheduler/triggers/     ← 各触发器独立文件
    time_based.py            早安 / 晚安 / 随机消息 / 天气 / 日记 / 记忆衰减
    diary.py                 日记相关触发
    period.py                生理期关心
    memory.py                未完结话题追问 / 主动回忆
    birthday.py              生日多段触发
    timenode.py              时间节点感知
    festival.py              节日 + 长假加速
    episodic_sweep.py        mid_term 老化扫描，批量晋升情景记忆
    garden_water.py          花园自动浇水
    watch.py                 Apple Watch 心率 / 睡眠事件
    dnd.py                   请勿打扰状态（已实现，未接入）
```

---

## 主循环

每 60 秒检查一次，所有触发器通过 `asyncio.gather` 并发执行，`return_exceptions=True` 保证单个触发器报错不影响其他。

---

## Pipeline 注入方式

调度器有自己的 `_pipeline` 变量，由 `main.py` 初始化时调用注入：

```python
from core.scheduler import loop as scheduler
scheduler.set_pipeline(pipeline)
```

> 注意：这和 `pipeline_registry.py` 是两套机制，调度器用的是 loop.py 内部的 `_pipeline`，不是 registry。

注入后，调度器通过 `_pipeline_send()` 走完整四步流程生成回复：

```python
context  = await _pipeline.fetch_context(owner_id, prompt)
messages, _ = _pipeline.build_prompt(owner_id, prompt, context)
reply    = await _pipeline.run_llm(messages)
await _send(reply)
asyncio.create_task(_pipeline.post_process(owner_id, prompt, reply))
```

Pipeline 未注入时降级：直接发送 prompt 原文（不经过 LLM）。

---

## 优先级机制

### 高优先级触发器（用户活跃时也强制发送）

birthday_midnight / birthday_eve / birthday_afternoon / birthday_night / period_reminder / hr_critical

### 低优先级（用户 120 秒内活跃则跳过）

其余所有触发器。`mark_user_active()` 由 main.py 每次收到用户消息时调用。

---

## 完整触发器列表与冷却时间

| 触发器名 | 冷却 | 优先级 | 所在文件 | 说明 |
|---|---|---|---|---|
| `morning_greeting` | 8h | 低 | time_based | 早安问候 |
| `night_reminder` | 5h | 低 | time_based | 晚安 |
| `random_message` | 4h | 低 | time_based | 随机日间碎碎念 |
| `weather_alert` | 6h | 低 | time_based | 特殊天气联动 |
| `daily_journal` | 1h | 低 | time_based | 叶瑄写今日手账（深夜触发） |
| `episodic_decay` | 20h | 低 | time_based | 情景记忆每日衰减 |
| `spontaneous_recall` | 4h | 低 | time_based | 主动回忆触发 |
| `dlq_monitor` | 24h | 低 | time_based | 扫 DLQ 目录，文件数 > 0 时 log warning |
| `activity_remind` | 20h | 低 | — | 仅预留冷却位，尚无对应实现 |
| `diary_reminder` | 20h | 低 | diary | 提醒用户写日记 |
| `diary_inject` | 6h | 低 | diary | 日记上下文注入 |
| `diary_share_reminder` | 8h | 低 | diary | 很久没看到日记时提一句 |
| `period_reminder` | 24h | **高** | period | 生理期关心 |
| `topic_followup` | 24h | 低 | memory | 未完结话题追问 |
| `birthday_midnight` | 365天 | **高** | birthday | 生日零点告白 |
| `birthday_eve` | 20h | **高** | birthday | 生日前夜预热 |
| `birthday_afternoon` | 20h | **高** | birthday | 生日下午关心 |
| `birthday_night` | 20h | **高** | birthday | 生日夜间收尾 |
| `timenode` | 20h | 低 | timenode | 时间节点感知 |
| `festival` | 20h | 低 | festival | 节日感知 |
| `holiday_boost` | 2h | 低 | festival | 长假加速发送 |
| `episodic_sweep` | 30min | 低 | episodic_sweep | mid_term 老化扫描，aged > 11h 且未晋升的条目批量入队 reflect_to_episodic |
| `garden_water` | 30min | 低 | garden_water | 30% 概率按当前 mood_state 给对应花槽自动浇水 |
| `hr_high` | 30min | 低 | watch | 心率>100 提醒 |
| `hr_critical` | 1h | **高** | watch | 心率>120 告警 |
| `sleep_end` | 2h | 低 | watch | 睡眠结束感知 |
| `sleep_report` | 20h | 低 | watch | 睡眠报告 |
| reminders（备忘录） | 无冷却 | 低 | loop.py内联 | 到点即发，发完标记完成 |

---

## 冷却状态持久化

冷却记录存在 `data/scheduler_state.json`：

```json
{
  "triggers": {
    "morning_greeting": 1748000000.0,
    "random_message": 1748003600.0
  },
  "last_diary_share": 1748001234.0
}
```

启动时自动恢复（`_load_scheduler_state()` 在模块导入时执行），重启不丢失冷却状态。

---

## 管理面板集成

- `get_status()` → 返回所有触发器的上次触发时间、冷却剩余秒数、是否 ready
- `manual_trigger(name)` → 绕过冷却和条件检查，强制触发部分触发器（管理面板用）

当前手动触发覆盖：`morning_greeting`、`night_reminder`、`random_message`、`daily_journal`、
`period_reminder`、`diary_reminder`、`diary_share_reminder`、`topic_followup`、
生日四段、`timenode`、`festival`、`holiday_boost`。
未覆盖：天气、记忆衰减、episodic sweep、garden_water、watch 事件、DLQ 监控、activity switch 等。

---

## 请勿打扰（DND）模块

文件：`core/scheduler/triggers/dnd.py`

**当前状态：已实现，尚未接入主流程。**

模块逻辑已完整（关键词检测 → 设置 3 小时 DND → 结束词清除），但 `main.py` 和 `loop.py` 均未调用 `detect_and_set()` / `is_dnd()`。如需启用：

1. 在 `main.py` 的消息处理入口调用 `dnd.detect_and_set(uid, content)`
2. 在 `_pipeline_send()` 里检查 `dnd.is_dnd(oid)` 决定是否跳过低优先级触发

---

## 新增触发器规范

1. 在 `core/scheduler/triggers/` 下选择合适文件（或新建）
2. 写 `async _check_xxx()` 函数，内部调 `_is_ready("xxx")` + `_mark("xxx")`
3. 在 `loop.py` 的 `_COOLDOWNS` 字典里加冷却时间
4. 在 `_loop()` 的 `asyncio.gather()` 里注册
5. 如果是高优先级，加入 `_HIGH_PRIORITY_TRIGGERS`
6. 如果需要管理面板手动触发，在 `manual_trigger()` 的 if-elif 里补充
7. 在此文档的触发器列表里补充
