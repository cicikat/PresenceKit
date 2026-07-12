# docs/proactive-trigger-audit.md — 主动触发累积/队列诊断（Brief 08 #5）

> 来源：`Emerald-client/cc-tasks/08-collapse-triggers-status-history.md` #5。
> 目的：给茶茶带去和 fable 讨论触发器整体架构前，先把现状盘清楚 + 落地低风险止血。
> 本文只诊断 + 止血参数，**不重构触发架构**（架构改动留给 fable 讨论后另开工单）。
> 完整触发器清单/冷却时长表见 `docs/scheduler.md`「完整触发器列表与冷却时间」一节，本文不重复摘抄，只补充分类视角和结论。

---

## 一、有没有队列：结论

**没有一个「发送队列」把待发的主动消息排队、合并、限速吐出。** 现状是三件事组合起来顶替了队列的作用：

1. **每个 tick 只选一个 winner**（`core/scheduler/gating.py::_decide()`）——所有 proposer 报名后，按 state → active-window → DND → per-trigger cooldown → 全局最小间隔层层过滤，剩余候选取 `urgency` 最高的一个执行，其余候选本 tick 直接放弃（未落盘、未排队，下个 tick 若条件仍满足会重新报名）。
2. **per-uid 互斥锁**（`core/conversation_gate.py::conversation_lock(uid)`）——`_pipeline_send()` 内部 `async with _conv_lock(oid)` 包住 `fetch_context → build_prompt → run_llm → record_assistant_turn`，保证同一用户任意时刻只有一路 LLM 调用在跑，防止并发触发脏写短期历史/情景记忆。这只是**互斥串行**，不是限流。
3. **60 秒 TTL 去重**（`core/perceive_event.py::receive_perceive_event()`）——同一 `trigger_name` 60 秒时间桶内重复触发判定 `DUPLICATE`，直接短路不进 LLM。这防的是「同一触发器短时间内被触发两次」，不是「多个不同触发器同时想发」。

`core/scheduler/defer_queue.py` 名字里带「queue」，但它只做 `active_window_behavior="defer"` 的候选**年龄追踪**（超龄后 `on_defer_expire` 强制发或丢弃），不是发送去重/限流队列，不要和「有没有队列」的问题混淆。

**唯一真正跨触发器限流的杠杆是 `global_proactive_min_gap_seconds`**（见下节），但它只在 gating proposer 体系里生效；不发言的维护型 legacy `_check_*`（`log_maintenance`、`episodic_sweep` 等）不受它约束——这些本来就不产生主动消息，无需约束。

**如果未来同一 tick 内有多个 winner 背靠背触发**（例如两次 tick 间隔恰好卡在 60s 边界各选出一个 winner），现有机制不会合并/丢弃，只会分别经过全局间隔过滤——这正是 `global_proactive_min_gap_seconds` 存在的意义，调大它是当前架构下最直接的降频手段。

---

## 二、触发器分类维度盘点

### 2.1 前端 vs 后端来源

| 来源 | 说明 |
|---|---|
| 前端（Emerald-client） | 触发面很收敛，只有两处：① `desktopWake()`（上线一次性问候，见 Brief #2）；② 接收 WS 推来的 `action`（`presence_nag`/`dream_invite`/`toy_invite` 等）后**执行**展示。前端不产生「主动召回」判断，只是执行后端已经决定要说的内容。本仓库（后端）未找到 `dream_invite`/`toy_invite` 这两个 action 名的定义——目前只有 `presence_nag` 是已实现的后端 proposer（`core/scheduler/triggers/presence_nag.py`），另外两个可能是前端规划中的名字，需要向提出工单方确认来源，不属于当前后端已知触发器。 |
| 后端（本仓库） | 累积/阈值/决策全部在这里：`core/scheduler/loop.py` 主循环 + `core/scheduler/gating.py` 决策 + `core/scheduler/triggers/*.py` 各触发器 proposer。 |

### 2.2 按写入方式（fanout / 落地形态）

| 写入方式 | 代表触发器 | 备注 |
|---|---|---|
| 聊天消息（对话气泡） | 早安/晚安/随机消息/生日系列/花园事件/未完结话题追问/overflow 等绝大多数 | 走 `_pipeline_send()` 统一出口，`record_assistant_turn` 写短期历史 |
| 桌面弹窗（独立 fanout） | `presence_nag`（`fanout=["desktop"]`） | 不进常规聊天历史通道，走独立弹窗 |
| 日记 | `diary_reminder`/`diary_share_reminder`（提醒去写）、`diary_inject`（维护型，不发言，只做上下文注入） | |
| 真实邮件 | `letter_writer` | 7 天最多一封，经质量/相似度门控，属最低频类别 |
| 设备事件驱动 | `watch.py`（Apple Watch 心率/睡眠） | `hr_critical` 为 emergency，豁免全局间隔 |
| 备忘录到点 | `reminders.py` | 到点即发，无常规冷却，靠 `mark_done` 防重发 |

### 2.3 按平台

当前所有 proposer 共用同一个 `_pipeline_send()` 出口和同一套 `_COOLDOWNS`/全局间隔/uid 锁，**不区分 desktop/QQ 平台单独限流**——如果同一用户同时挂 desktop 和 QQ，两个平台会共享同一个 uid 的冷却状态和全局间隔（这是刻意设计，防止「同一个人不同设备各收一份」）。`presence_nag` 例外，它显式只 fanout 到 desktop。

### 2.4 是否共用节流/冷却

**是，全部共用**：所有经 gating proposer 体系报名的触发器（时间节点、花园、生日、备忘录、overflow、watch 事件等）都受 per-trigger `_COOLDOWNS` + 全局 `global_proactive_min_gap_seconds` + active-window/DND + 60s TTL 去重四层过滤，emergency 优先级（`hr_critical`、生日系列）豁免全局间隔但仍受各自 per-trigger 冷却。不存在「某个触发器绕开所有节流单独发」的口子——已核对过的花园 legacy 直发循环是唯一例外，见下节。

---

## 三、已知隐患复核

**花园事件循环无节流（`garden_daily.py`/`garden_water.py` 里 `for event in events: await _emit(event)`）**：字面代码确实存在，但被 `legacy_tick_should_send()` 挡住——`core/scheduler/execution.py:EXECUTE_MODE = "live"` 时该函数恒返回 `False`，`_emit()` 永远不会被调用（当前生产实际发送路径是同文件里注册的 `propose_garden_*` proposer，走 gating 统一决策，有节流）。全仓 `docs/known-issues.md` 目前已不再记录这条隐患（历史条目应已随 legacy 分支被 `EXECUTE_MODE` 挡死而失效/清理），**Brief 里引用的"已知隐患"目前已是死代码，非当前生产风险**，但建议后续清理时顺手删掉这段 legacy for-loop（纯代码卫生，不在本次止血范围内，未动）。

---

## 四、止血：本次已落地的改动

| 改动 | 文件 | 说明 |
|---|---|---|
| 修复 `PUT`/`GET /scheduler/config` 读写字段名不一致 | `admin/routers/scheduler.py` | `PUT` 只接受 `global_proactive_min_gap_hours`（换算后落盘为 `_seconds`，`_hours` 本身从不落盘）；`GET` 之前只原样返回 `_seconds`，调用方 PUT hours 后回显读不到同名字段。现在 `GET` 补一个由 `_seconds` 派生的 `global_proactive_min_gap_hours` 字段，PUT/GET 对称。 |
| 调大全局最小间隔默认值降频 | `core/scheduler/loop.py`（fallback 默认）、`config.example.yaml`、`docs/scheduler.md` | 默认值从 45 分钟（`2700s`）调到 90 分钟（`5400s`）。**注意**：本机 `config.yaml`（gitignored，运行时配置）已被此前手动调到 `35100s`（9.75 小时），本次未覆盖这个已生效的本地值——只调了新装/模板默认值，避免覆盖已经做过的止血。 |

验证方式：`python -c` 直接调用 `admin.routers.scheduler.get_sched_config()`，确认返回体同时含 `global_proactive_min_gap_seconds` 和派生的 `global_proactive_min_gap_hours` 两个字段，数值一致（`hours = seconds / 3600`）。

---

## 五、留给 fable 讨论的问题（**已在 CC 任务 19 · D 关闭，2026-07**）

> 决策记录见 `DESIGN.md` §七「架构决策记录（CC 任务 19 · D）」。

1. ~~是否需要一个真正的「发送队列」~~ **不建**。`ProactiveLedger`（`core/scheduler/proactive_ledger.py`，CC 任务 19 · B）+ A2 的一次性 jitter `next_allowed_ts` 已硬性解决相邻 tick 背靠背双发的担忧，真队列的合并/排序/过期语义收益不成比例。
2. ~~`desktop`/`QQ` 是否需要平台级独立节流~~ **不做**，共用同一 uid 账本是刻意设计（防同人双份）。
3. ~~`dream_invite`/`toy_invite`~~ **确认为前端规划中功能**，后端无发射点，不在当前实现范围。
4. ~~legacy 花园直发循环清理~~ **已删除**（`garden_daily.py::_emit()` 及 `garden_water.py` 对应分支，CC 任务 19 · D）。
