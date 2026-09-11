# docs/scheduler.md — 调度器设计

## IME 扫描

既有 scheduler 循环在 autonomy tick 前执行 core.ime_awareness.tick，外层45秒上限；没有新增全局 worker，接收 HTTP 不调用模型。


> 主动性架构边界：调度器和 sensor 只产生候选 signal，不生成台词。signal 在一个 tick 内合并为单一 autonomy opportunity，由 `core/autonomy` 完成评估；`talk_owner` 是主动消息进入 `turn_sink` 的唯一出口。契约、状态观测和旧直发迁移清单见 `docs/autonomy.md`。

---

## 定位

调度器负责**他的主动行为**——不等用户发消息，自己在合适的时机触发。

```
core/scheduler/loop.py           ← 主循环 + 工具函数 + 冷却管理
core/scheduler/gating.py         ← proposal 收集、状态/冷却/defer/DND 过滤、每 tick 选一
core/scheduler/execution.py      ← proposal dry-run/live 执行收口，成功后才 mark
core/scheduler/defer_queue.py    ← defer 队列（内存态）：age 跟踪 + 过期 force_send/drop
core/scheduler/proposer_registry.py ← 原生 proposer 注册表
core/scheduler/rhythm.py         ← presence、逻辑日、时间窗比例等节律 helper
core/scheduler/overflow_bucket.py ← Overflow 五类只读信号与加权分数
core/scheduler/triggers/         ← 各触发器独立文件
    time_based.py                早安 / 晚安 / 随机消息 / 天气 / 日记 / 记忆衰减
    diary.py                     日记相关触发
    period.py                    生理期关心
    interest_seed.py             每周兴趣遴选（practice.enabled 总闸）
    practice.py                  深夜练习入慢队列 + 停滞求助 proposer
    memory.py                    未完结话题追问 / 主动回忆
    birthday.py                  生日多段触发
    timenode.py                  时间节点感知
    festival.py                  节日 + 长假加速
    episodic_sweep.py            mid_term 老化扫描，批量晋升情景记忆
    garden_water.py              花园自动浇水
    garden_daily.py              花园 harvest/vase 每日扫描
    hidden_state_decay.py        用户隐性状态衰减（12h）+ 基线收敛（7d），不发言
    event_log_salvage.py         event_log 归档前抢救持久事实（24h，每次上限3文件），不发言
    memory_janitor.py            闲时整合 pass：episodic 近似重复合并 + 向量库一致性核对（24h，深夜时段），不发言
    event_edge_proposer.py       有界模型候选关联边（per-scope cooldown + daily budget），不发言
    private_exchange.py          角色间私下往来：深夜/闲时低频受限会话，产物只回流关系层（Brief 86），不发言
    spend_monitor.py             API 余额每日检测；仅生成充值提醒和台账，绝不自动付款
    watch.py                     Apple Watch 心率 / 睡眠事件
    reminders.py                 到点备忘录 proposer
    sensor_aware.py              sensor 实时状态 → 主动开口（默认关闭）
    overflow.py                  多种真实理由累计溢出后主动联系
    dream_exit.py                出梦后由做梦角色主动开口一次
    letter_writer.py             情感事件驱动的真实邮件来信
    dnd.py                       请勿打扰状态（已实现，R2-D 已接入 main.py）
```

## 支出余额观测（Brief 57 / 127）

`spend_monitor` 是每日 maintenance check，不具备支付、充值、下单或资源操作能力。Brief 127
增加 `kind: aliyun_bss`：它只签名调用 BssOpenApi `QueryAccountBalance`，从有效运行时配置的
`spend.aliyun_bss.access_key_id` / `access_key_secret` 读取专用 RAM 凭据。将这个块放进
`config.yaml`；`secrets.local.yaml` 是管理面密码本，不参与此读取链。provider 配置中的任何
凭据字段都不会被读取。

专用 RAM 用户只附加以下自定义策略，且不要为该用户附加任何其他 RAM/BSS 策略：

```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["bssapi:QueryAccountBalance"],
      "Resource": "*"
    }
  ]
}
```

这比 `AliyunBSSReadOnlyAccess` 更窄；后者仍包含账单、订单等其他读取能力。阿里云也接受
兼容 action `bss:DescribeAcccount`，但本策略使用 API 名对应的 `bssapi:QueryAccountBalance`。

开关、阈值和提醒金额应放在 `config.yaml`：

```yaml
spend:
  aliyun_bss:
    access_key_id: "YOUR_DEDICATED_RAM_ACCESS_KEY_ID"
    access_key_secret: "YOUR_DEDICATED_RAM_ACCESS_KEY_SECRET"
  enabled: true
  daily_cap: 100.00
  monthly_cap: 100.00
  payee_whitelist: [aliyun_bss]
  balance_providers:
    - name: aliyun_bss
      kind: aliyun_bss
      threshold: 50.00
      balance_field: available_cash_amount
      topup_amount: 100.00
      timeout_s: 10
```

成功检查会在 `data/runtime/spend/ledger.jsonl` 写一条 `action=balance_check`、
`status=observed` 记录，并保留 `AvailableCashAmount`、`AvailableAmount` 和服务端
`Currency` 的非敏感观测值。低于阈值才接续原有 `proposed -> notified` 合同；它只是人工
充值提醒，绝不执行付款。请求失败或响应格式无效写 `status=check_failed`，不产生 proposal。

管理面提供 `POST /spend/check`（admin scope）强制执行一次只读检查，并返回不含余额、URL 或
凭据的 provider outcome。之后用 `GET /spend/ledger` 确认 `observed` 或 `check_failed`
记录，即可确认观察期 Day 1 已开始。通用 API 调用总账只记录固定 provider、耗时与
`ok`/失败类别，不记录请求 URL、AccessKey 或 secret。

---

### Scheduler/autonomy effective state (Brief 153)

调度器总闸、例行来源闸、autonomy 配置/代理覆盖、talk gate、冷却与预算的读取统一使用
`GET /admin/autonomy/effective-state`。`core/autonomy/effective_state.py` 是运行时解析
入口；生产消费者也从该模块读取相同 effective 值，避免管理面把多个配置端点拼成另一套
语义。响应中的 `runtime_consumer` 记录每个显示开关唯一消费点，`restart_required` 用于
明确热加载/持久状态是否需要重启（当前这些开关均不需要）。

trigger 生命周期注册表位于 `core/scheduler/gating.py::TRIGGER_MIGRATION_STATUS`，除
`migrated`、`maintenance-only`、`retired` 外，autonomy 原生 interval/schedule/overflow、
desktop wake 和外部事实来源标为 `active`。未注册名称不会被管理面静默推断为 active。

## 主循环

每 60 秒检查一次。每个 tick 先跑 `core/scheduler/gating.py::run_shadow_tick()` 收集原生
proposal、记录 `data/logs/gating_shadow.jsonl`，再根据 `core/scheduler/execution.py`
的 `EXECUTE_MODE` 决定是否执行 winner。当前常量为 `EXECUTE_MODE = "live"`：所有发言型
winner（含 Watch 事件到达路径）均先经 `gating._decide()`，再由
`execute_prompt(dry_run=False)` 真正发送，成功后才 `_mark()`。

随后 `loop.py` 仍用 `asyncio.gather(..., return_exceptions=True)` 跑 legacy `_check_*`。
已迁移触发器会通过 `legacy_tick_should_send()` 在 live 模式下让路；维护型扫描
（如 `garden_water`、`garden_daily`、`episodic_sweep`、`log_maintenance`、`hidden_state_decay`、`hidden_state_consolidate`、`event_log_salvage`、`memory_janitor`）仍需执行状态变更。

```
owner turn ──notify_owner_turn──→ state_machine
sensor tick ─feed_sensor_tick───→ state_machine
                                  ↓
loop.py tick ──gating log──→ logs/gating_shadow.jsonl
        │      └─ winner ──migrated──→ autonomy signal；compatibility──→ _pipeline_send()
        └────legacy/maintenance asyncio.gather──→ 未迁移检查或状态扫描
```

`MIGRATED_TRIGGERS` 中的 winner 不再调用 LLM、`turn_sink` 或 channel；兼容边界
`_pipeline_send()` 只把它转换为 autonomy signal 并返回 `None`。只有不在迁移注册表中的
窄范围 compatibility trigger 才能走下方的 execution-only pipeline，最终是否向用户发言
仍由 autonomy 的 `talk_owner` 或既有显式兼容调用方决定。

非迁移兼容触发进入 `_pipeline_send()` 后的执行顺序（P1 gate）：

```
receive_perceive_event(source="scheduler", kind="scheduled")
  → Dream Guard (fail-closed) + TTL dedup (60s bucket, key = trigger_name)
  → ACCEPTED → log perceive_event=true + correlation_id + event_id
conversation_lock(uid)
  → fetch_context → build_prompt → run_llm
  → record_assistant_turn(bypass_gate=True)
```

- **conversation_lock** 是 uid 级，`_pipeline_send` 与 `run_owner_chat_turn` 的 reality LLM
  共享同一把锁。`desktop_wake` Path B 不在 HTTP 请求中取得这把锁或调用 Pipeline；若
  autonomy 最终选择发言，统一由 `talk_owner` 在发送边界取得既有会话门闩。
- **desktop_wake Path A delivery ledger**：回放前在 uid 锁内读取 `wake_delivered: {turn_id: ts}`，
  筛选时排除已送达 turn，并在 HTTP 返回前原子写入；因此重复/并发 wake 对同一 turn 至多返回一次。
- **Dream Guard** 通过 `receive_perceive_event` fail-closed：BLOCK_ACTIVE / BLOCK_UNCERTAIN → 直接返回 None，不进 LLM。
- **TTL dedup**：同一 trigger_name 在同一 60s 时间桶内重复触发 → DUPLICATE，静默丢弃。
- **dedupe_key 组成（scheduler）**：`scheduler:uid:char:system:scheduled:hash({"trigger_name":name}):bucket` — payload 只含 trigger_name，key 稳定。
- **dedupe_key 组成（desktop_wake）**：`desktop_wake:uid:char:desktop:wake:hash({}):bucket` — payload 固定为 `{}`，last_seen 等 per-request 字段不入 hash，rapid reconnect 内幂等。
- **dedupe 原则（P1.1 稳定化）**：`event_id` / `correlation_id` 仅用于 tracing，不参与 dedupe（event_id is tracing only）。`dedupe_key` 只能包含稳定语义字段；`last_seen`、timestamp、随机 UUID、request time 等观测字段不得加入 payload（last_seen must not be part of dedupe payload）。违反此原则会导致同语义请求因 key 不同而各自通过去重、产生多个候选机会。
- **DUPLICATE / BLOCKED_DREAM no-op 保证**：命中 DUPLICATE 或 BLOCKED_DREAM 的调用必须立即返回，不入队 signal、不进入 LLM、不调用 record / post_process、不向任何 channel fanout。
- `bypass_gate=True` 传给 `record_assistant_turn`，避免锁重入。

`desktop_wake` Path B 现走独立的 signal-first 路径：

```
POST /desktop/wake
  → receive_perceive_event(source="desktop_wake", payload={})
  → 记录 accepted / duplicate / dream-blocked trigger audit
  → ACCEPTED + autonomy enabled
  → enqueue_desktop_wake_signal()（有界离线时长、10 分钟 TTL）
  → scheduler tick drain + 与同窗口其他低权重 signal 合并
  → autonomy runner：silent / tools-only / talk_owner
```

HTTP handler 不取得 Pipeline，不创建 task，不写 turn sink 或 ProactiveLedger。autonomy
关闭时不保留 wake signal；入队后关闭、signal 过期或入队后再次被 Dream Guard 阻断时，
都写入 autonomy 的终态观测且不在未来补发。若 Dream 阻断的是混合 opportunity，parent
job 终结且 `desktop_wake` 记为 `not_replayed`；仍在原始 TTL 内的 non-wake signals 合并为
一个带 parent job/run lineage 的 child retry job，过期项记为 terminal `expired`。纯
non-wake job 仍按原有退避重试，纯 wake job 不创建 child。parent 完成、run 追加与 child
入队在同一次 scoped state load/save 内完成，避免并发 signal/config 写入互相覆盖。Path A
的历史 turn 回放与 delivery ledger 保持原语义，不属于这次迁移。

---

## 全局主动间隔与统一发送预算：ProactiveLedger（CC 任务 19 · A/B）

> 背景：2026-06 主动触发审计的 Brief 08 #5 止血（把全局最小间隔调大）；原始快照已归档。
> **实际从未在运行中的进程里生效**——`core/config_loader.py` 的 `get_config()` 曾是
> 永久缓存单例，手改 `config.yaml` 不会被运行中进程读取；同时旧版
> `_global_proactive_gap_ready()` 每次检查都重新抽 jitter，等价于"反复抽签直到抽到
> 最松的"，有效间隔恒为配置值的 0.8 倍。CC 任务 19 从根上修复了这两个问题，并把
> "全局最小间隔" 升级为完整的 `ProactiveLedger`（间隔 + 当日预算 + 承接感三合一）。

### A1：config 热加载

`core/config_loader.py::get_config()` 现在每次调用都 `stat()` 一次 `config.yaml`；
`mtime` 与上次加载时不同（或从未加载过）就自动 `reload_config()`。stat 开销可忽略，
使手改 `config.yaml` 对运行中进程 ≤60s 内生效，无需重启。stat 失败时 fail-open：
沿用内存缓存，不抛出。

`core/scheduler/loop.py` 在调度器启动时和检测到 `global_proactive_min_gap_seconds`
变化时打一行 INFO 日志：`[scheduler] effective global_proactive_min_gap_seconds=XXXXX`，
让"配置生效没生效"直接在日志里可见（对应 `_log_effective_gap_if_changed()`，每 tick
调用一次，值不变时不重复打印）。

### B：ProactiveLedger — 所有主动发言的最后一道闸 + 唯一记账点

文件：`core/scheduler/proactive_ledger.py`。决策权威仍是 `gating._decide()`；
ledger 是它查询的数据源，两层不冲突：

```
can_send(trigger_name, *, priority) -> (bool, reason)
    # 检查 next_allowed_ts（A2：一次性 jitter 采样，只增不减）+ 当日预算
    # priority="emergency" 恒 True（间隔/预算均豁免），但仍需 record_send 记账
record_send(trigger_name, *, channel, gist) -> None
    # 写 next_allowed_ts、当日计数、最近 3 条 gist（B3 承接感）
continuity_hint() -> str
    # 读最近一条 gist，生成"别重复上次话题"软提示；fail-open
snapshot() -> dict
    # 观测用，GET /scheduler/proactive-ledger 消费
```

**A2 jitter 一次性采样**：`record_send()` 里计算并持久化
`next_allowed_ts = now + gap + uniform(0, 0.2*gap)`（jitter 只加不减，只在真实发送时
采样一次）；`can_send()` 的间隔检查退化为 `now >= next_allowed_ts`，不再每次检查都
重新抽签。

**当日发送预算**：`scheduler.max_daily_proactive`（默认 8），按 `rhythm.logical_day()`
逻辑日重置（凌晨 5 点前算前一天）。这是比"最小间隔"更符合直觉的总闸：间隔管
"别连珠炮"，预算管"一天别太吵"。emergency 优先级触发器豁免预算但仍计入统计。

**持久化**：`data/runtime/proactive_ledger.json`（原子写，`core/safe_write.py`），
含 `next_allowed_ts` / `daily_count` / `daily_logical_day` / `recent`（最近 3 条
`{trigger_name, gist, ts, channel}`）。

**接入点（全量记账，修复 RC5 "残缺的上次主动时间"）**：

| 出口 | 记账方式 |
|---|---|
| `execution.execute_prompt()` | 发送成功后 `record_send()`（替代旧 `_mark_global_proactive`） |
| `sensor_aware.handle_tick()` | judge 前先 `can_send()`；通过后只入 signal store；最终由 autonomy `talk_owner` 成功送达时以 `trigger_name="autonomy"` 统一 `record_send()` |
| autonomy `talk_owner`（包括由 `desktop_wake` signal 合并出的机会） | `talk_gate` 先查间隔/预算，真实发送成功后以 `trigger_name="autonomy"` 调用 `record_send()`；仅入队或静默不记发送账 |
| `manual_trigger`（管理面板测试） | record-only：绕过冷却/条件检查属设计，但也该记账 |
| watch emergency（`hr_critical` 等） | 经 `execute_prompt()` 自动覆盖；`priority="emergency"` 豁免限流但仍记账 |

**gating 集成**：`gating._decide()` 的候选过滤阶段对每个候选按其
`_policy_is_emergency()` 结果调用 `can_send(name, priority=...)`；全部被拒时返回
`global_gap_filtered`（间隔未到）或 `daily_budget_filtered`（当日预算已用完）。

**观测端点**：`GET /scheduler/proactive-ledger` 返回 `effective_gap_seconds`（内存
实际生效值）、`next_allowed_ts` / `next_allowed_in_seconds`、`daily_count` /
`daily_budget` / `daily_logical_day`、最近 3 条 `recent`。管理面板"调度器"页新增
「主动发言账本」卡片消费此接口。

**配置**（`scheduler:` 块）：

```yaml
scheduler:
  global_proactive_min_gap_seconds: 5400   # 90 分钟；想更克制就调大；改后 ≤60s 内热加载生效
  max_daily_proactive: 8                    # 当日主动消息总条数上限；emergency 豁免但仍计数
```

**读写字段名**：`PUT /scheduler/config` 只接受 `global_proactive_min_gap_hours`（小时，
校验范围 `(0, 24]`），换算后落盘为 `global_proactive_min_gap_seconds`；`_hours` 这个 key
本身从不落盘。`GET /scheduler/config` 补两类派生字段做对称/一致性回显：
`global_proactive_min_gap_hours`（由 `_seconds` 换算，避免调用方 PUT hours 后在 GET
里读不到同名字段，Brief 08 #5 止血修复的 key mismatch）；`effective_gap_seconds` +
`effective_gap_reload_needed`（D5：内存实际生效值与文件值并列，A1 落地后应恒一致，
`effective_gap_reload_needed=True` 说明热加载链路出了问题）。

---

## Overflow 主动互动

`core/scheduler/overflow_bucket.py` 每个 tick 只读计算五类 `0~1` 信号：
距上次对话时长、高强度且三天未召回的 episodic、hidden state 高需求、
六小时内 fresh harvest、强度超过 `0.75` 的 mood。单个信号读取失败只贡献 `0`，
不会阻断其余信号。

加权分数为 `time_gap*0.6 + episodic*0.5 + hidden_need*0.4 + garden*0.3 + mood*0.4`。
分数达到 `1.6`（每次判断带 `±15%` jitter）时，`overflow` proposer 才报名；
最高加权信号会成为 prompt 的具体缘由。proposal 仅允许在 `QUIET` 状态发送，
成功发送后进入三小时冷却。`scheduler.overflow_trigger=false` 可完全关闭。

## 存在感弹窗

`presence_nag` 是默认关闭的 QUIET-only proposer。开启 `scheduler.presence_nag` 后，仅在
`scheduler.activity_level=high`、距上次 owner 互动达到 `presence_nag_minutes`（默认 60 分钟）、
且当前角色 mood 为 `sad` / `angry` / `yandere` 时报名。它受统一 active-window 和 DND
过滤，用户正活跃时直接 drop，成功发送后进入两小时冷却。

执行时台词由 LLM 生成，只投递到 desktop，并随
`{"action_type":"presence_nag","params":{"text":...,"avatar":...}}` action 下发。
客户端设置开关通过 `PUT /scheduler/config` 同步此配置；默认关闭时不会弹窗。

---

## 触发状态机（Phase 2 Step 1）

`core/scheduler/state_machine.py` 维护每个 uid 的三态：

| 状态 | 含义 |
|---|---|
| `CHATTING` | 最近收到 owner turn，主动触发器应在后续 gating 接管时静默 |
| `QUIET` | 安静期，主动触发器可作为候选 |
| `RESTLESS` | sensor 事件频繁，后续由 sensor_aware 优先 |

当前状态机只观测不干预。入口：

- `main.py` 收到 owner 的 QQ turn 后调用 `notify_owner_turn(uid)`
- `admin/routers/chat.py` 的 owner 对话入口调用 `notify_owner_turn(uid)`
- `loop.py` 在 `sensor_aware` tick 后读取现有审计结果里的候选数，调用 `feed_sensor_tick(uid, count)`

状态持久化在 `data/runtime/scheduler_user_state.json` 的 `trigger_state` 段，不覆盖同文件里的
`last_diary_share` / `followed_topics` 等运行态。每次状态切换追加到
`data/logs/trigger_state.jsonl`。冷却时间单独保存在 `data/scheduler_cooldowns.json`。

`CHATTING → QUIET` 的滞后按会话 owner turn 数和 `mood_state.get_intensity()` 动态计算；
`QUIET ↔ RESTLESS` 按 sensor 事件率和持续时间确认，避免短暂鼠标/键盘动作造成状态抖动。

---

## Gating 并行观测（Phase 2 Step 2）

`core/scheduler/gating.py` 定义 `TriggerProposal` 并实现 `collect_and_decide(uid, proposals)`：

1. 过滤 `requires_state` 不包含当前状态的候选，`bypass_state_machine=True` 跳过此过滤
2. 过滤冷却未到的候选，冷却沿用 `loop.py` 的 `_COOLDOWNS` / `_is_ready`；proposal 携带
   `char_id` 时读取 `{char_id}:{trigger_name}` 角色键，否则读取旧全局键
3. 多候选按 `urgency` 选最高者
4. 一个 tick 最多返回一条候选

候选已不再有 `_adapt_legacy_triggers()` 桥接，只来自
`core/scheduler/proposer_registry.py` 注册的原生 proposer；未迁移触发器不会在
`gating_shadow.jsonl` 里报名。`EXECUTE_MODE="dry_run"` 时只记录 would-send；当前
`EXECUTE_MODE="live"` 时 winner 由统一执行层真实发送，已迁移 legacy tick 自动让路。

已迁移 proposer 覆盖：watch（`hr_critical/hr_high/sleep_end`）、生日四档、`period_reminder`、
time_based 的早晚安/随机/天气/日记/主动回忆、diary 两档、`timenode`、`festival/holiday_boost`、
`reminders`、`topic_followup`、`overflow`、`dream_exit`、`letter_writer`、花园伴生事件（bloom/harvest/handle/vase）。`garden_water`、
`garden_daily` 扫描本体、`episodic_sweep`、`episodic_decay`、`dlq_monitor`、`sensor_aware`
仍只走 legacy 真实检查或事件驱动路径。

`run_shadow_tick()` 如果选中了带 `execute` 的 proposal，会按模式调用 `execute()`：
dry-run 写 would-send / would-mark；live 真实调用 `_pipeline_send()`。Watch 心率和睡醒事件到达时，
`watch.py::on_watch_event()` 通过 `gating.decide_and_execute_event()` 进入同一套 `_decide()`；
普通 tick 也可重试缓存 proposal。`WATCH_EXECUTE_MODE` 仅控制事件到达时立即 live 或 dry-run，
是 rollback/config switch，不能绕过 gating/policy。
urgency 分档统一由 `core/scheduler/urgency.py` 提供。同一个日志也记录 live execute 被 `_pipeline_send()` 拦下的
`blocked=true` 条目，用于观察 active window 真实拦截分布；该日志只做可观测性，不改变发送、
mark 或重试行为。

shadow log 格式：

```json
{"ts": 1748000000.0, "uid": "1234567890", "state": "QUIET", "candidates": [], "would_pick": null, "reason": "no_candidates"}
```

dry-run log 格式：

```json
{"ts": 1748000000.0, "trigger_name": "morning_greeting", "would_send_prompt": "...", "would_mark": ["morning_greeting"], "would_mark_done": [], "reads_cache_ok": true}
```

blocked log 格式：

```json
{"ts": 1748000000.0, "trigger_name": "reminders", "reason": "sent_false", "would_mark": [], "would_mark_done": ["abc"], "sent": false, "blocked": true}
```

---

## Policy 表（R2-C 已完成）

`core/scheduler/policy.py` 是 **active-window / DND 决策的单一权威来源**：
`gating._decide()` 通过延迟 import 引用 `POLICY_TABLE`；`_pipeline_send()` 不再做任何
active-window / DND 过滤——R2-C 后，执行层仅负责 send + mark，仲裁全在 `gating._decide()`。

R2-C 后的状态：

- `POLICY_TABLE`（25 条）驱动 `gating._decide()` 的 active-window filter 和 DND filter。
- `loop._legacy_active_window_blocks()` 和 `_legacy_dnd_blocks()` **已删除**（R2-C）。
- `_pipeline_send()` 中无任何 active-window / DND 检查；传入的 trigger 已是 gating winner。
- `_HIGH_PRIORITY_TRIGGERS` 常量保留用于文档和测试断言（验证 POLICY_TABLE exempt 集合），
  不参与运行时决策。
- birthday_eve / afternoon / night 由 policy 的 `defer` 对齐为 `exempt`（R2-B 修复）。

两条定性保留：

- `sleep_end`：`priority=”normal”` + `active_window_behavior=”drop”`，`mark_on_drop=False`。
  `cross_marks=[“morning_greeting”]` 只表达”实际 sent 后才联动 mark”；drop 不 cross-mark，
  避免睡醒关心被拦后又压掉早安。
- `hr_high`：语义上是 defer，但 Watch proposal 受 `HEART_RATE_PROPOSAL_TTL_SECONDS=10min`
  限制，`max_defer_age_secs` 只能记为 10 分钟；超过 TTL 后 proposal 返回 `None`，等同过期 drop。

**R2-D 完成项（2026-06-11）**：

- ✅ defer 队列最小实现（`core/scheduler/defer_queue.py`，内存态）
- ✅ DND 触发词自动检测接入 `main.py` owner 消息路径

---

## Defer 队列（R2-D）

文件：`core/scheduler/defer_queue.py`

### 语义

当 `gating._decide()` 遇到 `active_window_behavior="defer"` 的触发器且用户活跃时，
不仅跳过本 tick，还将 `(uid, trigger_name)` 记录到内存队列并追踪首次推迟时间戳。
每个 tick 的 `_decide()` 调用流程：

1. `scan_expired(uid)` — 扫描超龄条目，返回 `(force_send_names, dropped_names)`；
   超龄条目从队列中删除。
2. 若 `trigger_name in force_send_names`（即 `on_defer_expire="force_send"` 且已超龄），
   该触发器被加入 `aw_allowed`，**即使用户仍在活跃窗口也可通过**。
3. 若用户活跃且触发器未能通过 `aw_allowed`，对 `defer` 行为的触发器调用 `enqueue_defer`
   （幂等：首次推迟时间戳不会被后续 tick 覆盖）。
4. 触发器被选中（`picked`）后，调用 `release_defer(uid, trigger_name)` 从队列删除。

### 到期行为

| `on_defer_expire` | 超龄后的行为 |
|---|---|
| `"force_send"` | 即使用户活跃也强制发送（bypass active_window filter） |
| `"drop"` | 清除队列条目，下次重新从零累积 age |

### 限制与生命周期

- **内存态**：进程重启后队列清零；所有 `defer` 触发器的 `max_defer_age_secs` 均较短
  （10 分钟 ~ 4 小时），重启后从新的 tick 开始重新累积 age，无实质影响。
- **不扩大发送频率**：defer 队列只做 age 追踪 + 过期决策，不创建新 proposal；
  proposal 仍来自 proposer registry 每 tick 正常返回。
- **可观测**：`get_queue_snapshot(uid)` 返回当前队列快照，含 `enqueue_ts` 和 `age_secs`；
  候选序列化中新增 `force_send` 和 `deferred_age_secs` 字段，写入 `gating_shadow.jsonl`。

### 当前配置表（defer 触发器）

| 触发器 | max_defer_age_secs | on_defer_expire |
|---|---|---|
| `hr_high` | 10 分钟 | drop |
| `weather_alert` | 30 分钟 | drop |
| `topic_followup` | 2 小时 | drop |
| `reminders` | 10 分钟 | **force_send** |
| `diary_share_reminder` | 4 小时 | drop |
| `diary_reminder` | 4 小时 | drop |

---

## DND 主入口（R2-D）

文件：`core/scheduler/triggers/dnd.py`（逻辑），`main.py`（接线）

**R2-D 完成**：`detect_and_set()` 已接入 `main.py` 的 owner 消息处理路径。

### 接线位置

`main.py::handle_message()` — 在确认 `user_id == owner_id` 的 try 块内，
`mark_user_active()` 调用之后，立即调用：

```python
from core.scheduler.triggers.dnd import detect_and_set as _dnd_detect
_dnd_detect(user_id, message.get("content", ""))
```

- 仅对 owner 消息生效（非 owner 消息不触发 DND 检测）。
- `detect_and_set` 是纯内存操作，不阻塞快速路径。
- 不调用任何 LLM，不影响 pipeline 流程。

### 触发词（设置 DND）

`学习` / `开会` / `上班` / `工作` / `在忙` / `忙着` / `复习` / `备考` / `做题` / `写作业` / `写报告`

### 结束词（清除 DND）

`下课` / `散会` / `下班` / `忙完` / `做完了` / `写完了` / `结束了` / `搞定了`

结束词优先于触发词（同一消息中若两者都出现，结束词生效）。

### DND 生效期间

- DND 默认持续 3 小时（`_DND_DURATION = 3 * 3600`），超时自动失效。
- `gating._decide()` 在 DND 活跃时，只有 `priority="emergency"` 的触发器（`hr_critical`）可通过，
  其余全部返回 `dnd_filtered`。
- Maintenance tick（`log_maintenance` / `episodic_sweep` / `hidden_state_decay` 等）不经过
  发言 gating，不受 DND 影响。

---

## proactive=off 闸门（Brief 29 · per-char，3.3）

角色卡 `presence_ext.proactive`：`"full"`（默认，现状）/ `"off"`。判定函数
`core.character_loader.is_proactive_disabled(char_id=None)`，fail-soft（加载失败/未注册 →
`False`，不阻断发言）。两处闸门入口：

- `core/scheduler/gating.py::_decide()`：proposals 非空且活跃角色 `proactive=off` 时，直接
  返回 `(None, "proactive_off", candidates)`，拒绝全部发言类 proposal（不区分 trigger_name）。
- `core/scheduler/execution.py::legacy_tick_should_send(force=False)`：`force=True`（手动/
  强制触发）无条件放行，语义不变；`force=False` 时叠加同一判定。

**维护任务不受影响**：`episodic_decay`、`inner_diary_write`（Brief 26）、`diary_inject`、
`hidden_state_decay`、garden 自动浇水等不经过 `gating._decide()` 或 `legacy_tick_should_send()`
的扫描型任务照常运行——这两个闸门只挡"发言"这一件事。

v1 不做 `"minimal"` 档（reminders 也一并压掉）；真有需要再分级。

---

## R2-A（2026-06-10）+ R2-B（2026-06-11）+ R2-C（2026-06-11）+ R2-D（2026-06-11）

> R2-A 是审计和决策包。R2-B 完成"发言决策前移第一段"。R2-C 完成 legacy 安全网删除、
> 执行层收口到 send + mark only。R2-D 完成 defer 队列最小实现 + DND 主入口接线。

### 最终调度决策路径图（R2-D 后）

```
owner QQ 消息
    ↓ main.py::handle_message()
    ├─ notify_owner_turn(uid)       → state_machine
    ├─ mark_user_active()           → _last_user_message_time
    └─ detect_and_set(uid, content) → dnd._dnd_expire  ← R2-D DND 接线

每 60s tick
    ↓ loop._loop()
    ├─ gating.run_shadow_tick(uid)
    │     ↓ _collect_native_proposals(ctx)   → proposer_registry 每个 proposer
    │     ↓ _decide(uid, proposals)
    │         1. scan_expired(uid)           → defer_queue 过期处理 (R2-D)
    │         2. state filter               → TriggerState 门控
    │         3. active_window filter       → POLICY_TABLE.active_window_behavior
    │            ├─ force_send_names 豁免   → 过期 defer + on_defer_expire=force_send
    │            └─ 被过滤 defer → enqueue_defer(uid, name)  (R2-D)
    │         4. DND filter                 → is_dnd(uid), emergency 豁免
    │         5. cooldown filter            → _is_ready(name)
    │         6. max urgency 选 winner
    │         7. release_defer(uid, winner) → defer_queue 释放 (R2-D)
    │     ↓ winner.execute(dry_run=False)
    │         → migrated: signal-first（不进入 pipeline）
    │         → compatibility: execute_prompt() → _pipeline_send() → perceive_event gate
    │           → conversation_lock → run_llm → record_assistant_turn → _mark
    └─ legacy asyncio.gather(_check_*...)
          speaking 触发器: legacy_tick_should_send()=False → 让路（no-op）
          maintenance 触发器: 正常执行（不发言，不受 gating/DND 影响）
```

**最终合约**：
- 发言 trigger winner 决策 **只在** `gating._decide()`（含 POLICY_TABLE + defer_queue + DND + state）。
- migrated winner 不进入 `_pipeline_send()` 的 LLM/channel 段；compatibility 的
  `_pipeline_send()` / `execution.execute_prompt()` **只负责** send + mark，block/defer 不调 `_mark()`。
- maintenance tick 不受 active-window / DND / defer 误伤（不在 MIGRATED_TRIGGERS，不走发言路径）。
- `WATCH_EXECUTE_MODE` 仅是事件到达时 live/dry-run 的 rollback/config switch；两种模式都经过统一 `gating._decide()`。
- `policy.py` 是运行时决策权威，被 `gating.py` 通过延迟 import 引用，`loop.py` 不直接引用。

### 当前执行面总览（R2-D 后）

| 编号 | 执行面 | 文件 | 状态 |
|---|---|---|---|
| S1 | **Gating/Proposer live 路径** | `gating.py::run_shadow_tick()` → signal 或 compatibility executor | migrated winner 只入 autonomy signal；仅未迁移 compatibility trigger 进入 `_pipeline_send()` |
| S2 | **Legacy `_check_*` gather 路径** | `loop.py::_loop()` → `asyncio.gather(_check_*...)` | 生产活跃；speaking 触发器通过 `legacy_tick_should_send()` 在 live 模式下让路，维护型触发器仍正常运行 |
| S3 | **`legacy_tick_should_send()` 让路垫片** | `execution.py` | 当前 `EXECUTE_MODE="live"` → 返回 False，阻止 legacy speaking 触发器双发 |
| S4 | **Watch 事件到达 adapter** | `triggers/watch.py` → `gating.decide_and_execute_event()` | `WATCH_EXECUTE_MODE` 仅切换事件到达时 live/dry-run；hr_critical/hr_high/sleep_end 均经过 `_decide()`，普通 tick 可重试缓存 proposal |
| S5 | **sensor_aware signal-first 路径** | `triggers/sensor_aware.py` → `core/autonomy/signal_adapters.py` | `handle_tick()` 只入 signal store；autonomy runner 合并 opportunity，显式 `talk_owner` 后才进入 `talk_gate.send()` / `record_assistant_turn()`。旧 `output_mode="return"` 分支在源码 `return` 后封存 |
| S6 | **policy.py 決策表** | `policy.py` | **R2-C 完成**；gating._decide() 以 POLICY_TABLE 为单一权威；_pipeline_send 不再参与决策 |
| S7 | **`_pipeline_send` 执行层（仅 send + mark）** | `loop.py::_pipeline_send()` | **R2-C done**：`_legacy_active_window_blocks()` / `_legacy_dnd_blocks()` 已删除；_pipeline_send 不再做 active-window / DND 过滤 |

### 触发器分类表

**类型一：Live Proposer Speaking Trigger（已迁移，gating 执行）**

| 触发器 | 文件 | 让路逻辑 |
|---|---|---|
| morning_greeting, night_reminder, random_message, weather_alert, daily_journal, spontaneous_recall | time_based.py | `legacy_tick_should_send()` 让路 + proposer 接管；daily_journal 只负责发言，不再写日记（见类型四 `inner_diary_write`）|
| diary_reminder, diary_share_reminder | diary.py | 同上 |
| period_reminder | period.py | 同上 |
| birthday_midnight/eve/afternoon/night | birthday.py | 同上 |
| timenode, festival, holiday_boost | timenode.py / festival.py | 同上 |
| reminders | reminders.py | 同上（proposer 路径，after_send 才 mark_done）|
| topic_followup | memory.py | legacy `_check_topic_followup` 是 no-op stub，proposer 接管 |
| garden_bloom | garden_water.py | legacy 通过 `legacy_send` 变量门控 + proposer 接管 |
| garden_harvest_expired/handle_ask/handle_gift/handle_self/vase_wilted | garden_daily.py | 同上 |

**类型二：Watch 事件驱动 Speaking Trigger（统一 gating 路径）**

| 触发器 | 执行路径 | 备注 |
|---|---|---|
| hr_critical, hr_high | `on_watch_event("heart_rate", ...)` → `decide_and_execute_event()` → `_decide()` → proposal execute | `WATCH_EXECUTE_MODE` 仅控制即时 live/dry-run；普通 tick 可重试缓存 proposal |
| sleep_end | `on_watch_event("sleep_end", ...)` → 同上 | state / active-window / DND / cooldown / policy 均统一决策 |

**类型三：Sensor 实时 Speaking Signal（signal-first）**

| 触发器 | 执行路径 | 备注 |
|---|---|---|
| sensor_aware | `_check_sensor_aware()` → `handle_tick()` → `emit_trigger_signal()` → autonomy runner → `talk_owner` → `record_assistant_turn` | 先过 ProactiveLedger + DND；信号 15 分钟 bucket 去重；不直接跑 LLM/channel |

**类型四：Maintenance Tick（纯状态/清理，不发言）**

| 触发器 | 文件 |
|---|---|
| episodic_decay, dlq_monitor, inner_diary_write | time_based.py |
| log_maintenance | loop.py 内联 |
| episodic_sweep | episodic_sweep.py |
| hidden_state_decay, hidden_state_consolidate | hidden_state_decay.py |
| event_log_salvage | event_log_salvage.py |
| memory_janitor | memory_janitor.py |
| private_exchange | private_exchange.py |
| diary_inject | diary.py（维护型：读日记存 diary_context，无 legacy_tick_should_send 检查）|

### 决策位置表（R2-B 后）

| 决策项 | 位置 | 备注 |
|---|---|---|
| state（CHATTING/QUIET/RESTLESS）过滤 | `gating._decide()` | 软门 |
| cooldown 过滤 | `gating._decide()` → `_is_ready()` | 基于 `_COOLDOWNS` + `_last_trigger` |
| active-window 过滤（proposer 路径）| `gating._decide()` → `_policy_active_window_behavior()` | **R2-B done**；以 POLICY_TABLE 为权威 |
| active-window 过滤（legacy 路径安全网）| ~~`loop._legacy_active_window_blocks()`~~ | **R2-C 已删除**；gating 是唯一权威 |
| DND 过滤（proposer 路径）| `gating._decide()` → `_policy_is_emergency()` | **R2-C done**；emergency 豁免，其余 blocked |
| DND 过滤（legacy 路径安全网）| ~~`loop._legacy_dnd_blocks()`~~ | **R2-C 已删除**；gating 是唯一权威 |
| 高优先级豁免（exempt） | `POLICY_TABLE.active_window_behavior == "exempt"` | hr_critical / period_reminder / birthday 系列全部 exempt |
| priority/urgency 排序 | `gating._decide()` → 按 `urgency` 选最高者 | POLICY_TABLE priority 字段未接入 urgency 排序；R2-C 范畴 |
| defer 队列 | `core/scheduler/defer_queue.py`（内存态） | **R2-D done**；`enqueue_defer` 追踪首次推迟时间；超龄后 force_send / drop；restart 清零（可接受，所有 defer 触发器 TTL 均较短） |

### 执行与 mark 表

| 路径 | 谁调用 send | 谁调用 _mark | 未发送是否 mark | 异常是否可观测 |
|---|---|---|---|---|
| Gating live（execute_prompt）| `execute_prompt()` 调用 `_pipeline_send()` | 仅在 `sent=True` 后调用 `loop._mark()` | 否（write_execute_blocked 记录）| 是（execute_dryrun.jsonl blocked 条目）|
| Legacy speaking（步退）| N/A（live 模式下不执行）| N/A | N/A | N/A |
| Watch event-driven | `decide_and_execute_event()` → `_decide()` → `execute_prompt()` → `_pipeline_send()` | 仅 sent 后 mark | 否 | 是（log_error）|
| sensor_aware | autonomy `talk_gate.send()` → `record_assistant_turn()` | `record_send("autonomy")` 记账；旧 `sensor_events.mark_proactive_sent()` 只在不可达兼容分支 | signal enqueue / autonomy run / talk disposition 均可观测 |
| Maintenance tick | 不 send | 立即 mark（不依赖 send 结果）| N/A | 是（log_error 各步独立）|

**A4 失败退避（sent=False）**：`execute_prompt()`（含 `letter_writer` 自有的
`_send_letter_if_worthy()` 执行路径，它不经过 `execute_prompt()` 但同样接了这条）在
`sent=False` 时调用 `loop._record_attempt_failure(trigger_name)`：首次退避 15min，
此后每次失败翻倍，封顶该触发器自身 `_COOLDOWNS`。`loop._is_ready()`（`gating` 的
`_proposal_cooldown_ready()` 底层）同时检查正式冷却和 attempt-cooldown，两者都过才
`ready=True`。成功发送后 `loop._clear_attempt_backoff()` 清除退避状态，下次失败重新
从 15min 起算。修复 RC4：此前失败（DUPLICATE / Dream Guard / LLM 空回复 / 质量门
拒发）不留痕，下个 tick 同一 proposer 立即重新报名重试一遍完整 pipeline
（`letter_writer` 质量门拒发是最明显案例：3269 次 pick 里连续数小时每 tick 被选中）。

### R2-B 完成情况（2026-06-11）

**完成项**：
1. ✅ birthday_eve/afternoon/night policy 对齐为 `exempt`（与 `_HIGH_PRIORITY_TRIGGERS` 一致）
2. ✅ `gating._decide()` 接入 POLICY_TABLE active-window filter（proposer 路径主决策）
3. ✅ `gating._decide()` 接入 DND filter（emergency 豁免）
4. ✅ `loop._pipeline_send()` active-window 检查替换为 `_legacy_active_window_blocks()`（policy 委托）
5. ✅ `loop._pipeline_send()` DND 检查添加 `_legacy_dnd_blocks()`（policy 委托）
6. ✅ `_HIGH_PRIORITY_TRIGGERS` 与 POLICY_TABLE exempt 集对齐（无 mismatch）

### R2-C 完成情况（2026-06-11）

**完成项**：
1. ✅ `loop._legacy_active_window_blocks()` 已删除
2. ✅ `loop._legacy_dnd_blocks()` 已删除
3. ✅ `_pipeline_send()` 中无任何 active-window / DND 过滤（执行层仅 send + mark）
4. ✅ 全量发言 trigger（28 个）均有 proposer 注册，均在 `MIGRATED_TRIGGERS` 中
5. ✅ Legacy speaking `_check_*` 通过 `legacy_tick_should_send()` 在 live 模式让路（维护型保留）
6. ✅ `_HIGH_PRIORITY_TRIGGERS` 保留为文档/测试断言常量

**R2-D 完成情况（2026-06-11）**：
1. ✅ `defer` 队列实现（`core/scheduler/defer_queue.py`，内存态，age 跟踪 + 过期 force_send/drop）
2. ✅ `dnd.detect_and_set` 接入 `main.py` owner 消息路径（R2-D 主入口接线）

**仍为遗留项（R2 外或后续迭代）**：
- 120s active window 长度迁入配置（当前硬编码，影响 `_user_active_recently` 默认值）
- POLICY_TABLE `priority` 字段接入 urgency 排序（目前 priority 仅用于 DND emergency 判断；
  urgency 由 proposer 自身决定，priority 未参与 max() 选择）

---

## Pipeline 注入方式

调度器统一从 `pipeline_registry` 获取 Pipeline 实例（R7-B 后已统一）：

```python
# main.py 初始化时注册一次即可
from core.pipeline_registry import register
register(pipeline)
```

`scheduler.set_pipeline(pipeline)` 兼容壳已删除（Brief 35）；调用点直接用
`pipeline_registry.register()`，调度器不维护自己的 `_pipeline` 副本。

注入后，调度器通过 `_pipeline_send()` 走完整四步流程生成回复：

```python
# _pipeline_send 内部
_pipeline = pipeline_registry.get()   # 从 registry 读取
context  = await _pipeline.fetch_context(owner_id, prompt)
messages, _ = _pipeline.build_prompt(owner_id, prompt, context)
reply    = await _pipeline.run_llm(messages)
await record_assistant_turn(
    assistant_text=reply,
    uid=owner_id,
    source=TurnSource.TRIGGER,
    trigger_name=trigger_name,
    fanout="all",
    pipeline=_pipeline,
)
```

Pipeline 未注入时，只有未迁移 compatibility trigger 才可降级直接发送 prompt 原文（不经过 LLM）。
迁移触发器在 pipeline lookup 之前就已转为 autonomy signal，因此不会因为 pipeline 缺失而恢复旧直发。

Brief 195 进一步收口 tick 型 migrated proposal：`write_shadow_tick()` 完成原有 state、DND、
active-user、cooldown、budget 与 winner 竞争后，只把选中的 winner 转为一个 durable autonomy
signal。适配器不会读取 legacy prompt 或调用 executor、`_pipeline_send()`、`talk_gate.send()`
或 channel。`festival` 与 `period_reminder` 仅携带 factual evidence；七夕通过 scheduler-local
现实日期与 `lunar_python` 农历转换识别，转换不可用时 fail-closed 为无候选。

`_pipeline_send` 支持 `output_mode` 参数（默认 `"speak"`）：

| `output_mode` | 行为 |
|---|---|
| `"speak"`（默认）| 生成 reply 后经 `turn_sink` 写入并广播，返回 reply 文本；被 active window 拦截、owner_id 缺失、LLM 空回复或异常时返回 `None` |
| `"return"` | 生成 reply 后经 `turn_sink` 写入但不广播，直接返回 reply 文本；失败时返回 `None` |

当前 `sensor_aware` 不再使用上述 `output_mode="return"` 旁路：它只调用
`emit_trigger_signal()`，由 autonomy runner 的 `talk_owner` 最终决定是否说话并进入
`record_assistant_turn(source=TRIGGER, trigger_name="autonomy")`。旧的
`record_assistant_turn(source=SENSOR, payload={"behavior": action})` 分支位于不可达兼容代码中，
仅作迁移审计记录。其余未迁移 compatibility trigger 仍按各自 `output_mode` 语义执行。

---

## Active Window 与优先级（R2-B 后）

active window 决策已完全收入 `gating._decide()`（R2-C 后），以 `POLICY_TABLE.active_window_behavior`
为单一权威。`_pipeline_send()` 不再参与决策，只负责 send + mark。窗口长度仍为硬编码 120 秒
（迁入配置为 R2-D 范畴）。

发送边界（R2-B 后）：

- **exempt**（用户活跃时也发送）：`birthday_midnight` / `birthday_eve` /
  `birthday_afternoon` / `birthday_night` / `period_reminder` / `hr_critical`。
  policy.py 中 `active_window_behavior="exempt"`，与 `_HIGH_PRIORITY_TRIGGERS` 对齐。
- **defer**（用户活跃时跳过本 tick，等下次重试）：`hr_high`、`weather_alert`、
  `topic_followup`、`reminders`、`diary_reminder`、`diary_share_reminder` 等。
  真正的 defer 队列为 R2-C 范畴，当前行为等价于"等用户不活跃的下一个 tick"。
- **drop**（用户活跃时跳过）：`random_message`、`spontaneous_recall`、
  `festival`、`holiday_boost`、`timenode`、`daily_journal`、花园伴生事件等 filler；
  `sleep_end`（normal + drop，mark_on_drop=False）。
- 普通主动消息被 gating 拦截时，`execute()` 不被调用，`_mark()` 不被调用。
- execute live 路径里，`execute_prompt()` 收到 `None` 后只写 `execute_dryrun.jsonl` 的
  `blocked=true` 观测，不调用 `after_send`，不执行 `_mark()`，也不 `mark_done()`。
- reminders proposal 由 `core/agent_runtime/scheduler_capability.py` 持有；到点后通过
  `talk_gate` 的新 Reality ingress/turn 投递，成功后才完成 Task receipt。旧 reminder JSON
  不再是写入或投递路径。

当前已收口的“未发送不 mark”语义覆盖 execute live 路径和 Runtime reminder delivery：被
策略拦截、无通道或发送前异常时，不完成 Task receipt。其他 legacy trigger 若仍在
`_pipeline_send()` 后无条件 `_mark()`，需逐个按 sent 结果继续收口。

---

## 完整触发器列表与冷却时间

| 触发器名 | 冷却 | 优先级 | 所在文件 | 说明 |
|---|---|---|---|---|
| `morning_greeting` | 8h | 低 | time_based | 早安问候 |
| `night_reminder` | 5h | 低 | time_based | 晚安 |
| `random_message` | 4h | 低 | time_based | 随机日间碎碎念 |
| `weather_alert` | 6h | 低 | time_based | 特殊天气联动 |
| `daily_journal` | 1h | 低 | time_based | 他写今日手账并发出来（深夜触发，只负责发言）|
| `episodic_decay` | 20h | 低 | time_based | 情景记忆每日衰减 |
| `inner_diary_write` | 2h | 维护 | time_based | 静默写角色内心日记，与 daily_journal 发言解耦；23:00–次日05:00 窗口，幂等靠当日日记文件是否存在 |
| `spontaneous_recall` | 4h | 低 | time_based | 主动回忆触发 |
| `dlq_monitor` | 24h | 低 | time_based | 扫 DLQ 目录，文件数 > 0 时 log warning；R8-A：legacy task 超 30 天自动归档到 `expired/` |
| `log_maintenance` | 24h | 维护 | loop.py 内联 | 清理 event_log、done reminders、dream archive、inbox/image cache，并压缩 observations |
| `diary_reminder` | 20h | 低 | diary | 提醒用户写日记；冷启动门控见下 |
| `diary_inject` | 6h | 低 | diary | 日记上下文注入 |
| `diary_share_reminder` | 8h | 低 | diary | 很久没看到日记时提一句；冷启动门控见下 |
| `period_reminder` | 24h | **高** | period | 生理期关心；仅在 uid-global `health_state.last_period_date` 已记录时产生候选 |
| `topic_followup` | 24h | 低 | memory | 未完结话题追问 |
| `birthday_midnight` | 365天 | **高** | birthday | 生日零点告白 |
| `birthday_eve` | 20h | **高** | birthday | 生日前夜预热 |
| `birthday_afternoon` | 20h | **高** | birthday | 生日下午关心 |
| `birthday_night` | 20h | **高** | birthday | 生日夜间收尾 |
| `timenode` | 20h | 低 | timenode | 时间节点感知 |
| `festival` | 20h | 低 | festival | 节日感知 |
| `holiday_boost` | 2h | 低 | festival | 长假加速发送 |
| `episodic_sweep` | 30min | 低 | episodic_sweep | mid_term 老化扫描，aged > 11h 且未晋升的条目批量入队 reflect_to_episodic |
| `garden_water` | 300min | 低 | garden_water | 30% 概率按当前 mood_state 给对应花槽自动浇水 |
| `garden_daily` | 24h | 低 | garden_daily | 扫描 harvest 过期、采后处理、花瓶枯萎 |
| `garden_bloom` | 8h | 低 | garden_water | 开花事件发言名；事件发言前单独 check/mark |
| `garden_harvest_expired` | 4h | 低 | garden_daily | 收获过期事件发言名；事件发言前单独 check/mark |
| `garden_handle_gift` | 4h | 低 | garden_daily | 采后送给用户事件发言名；事件发言前单独 check/mark |
| `garden_handle_self` | 4h | 低 | garden_daily | 采后自己处理事件发言名（仅 vase 分支，G4 之后 dry/ask 不再发消息）；事件发言前单独 check/mark |
| `garden_vase_wilted` | 4h | 低 | garden_daily | 花瓶枯萎事件发言名；事件发言前单独 check/mark |
| `hidden_state_decay` | 12h | 维护 | hidden_state_decay | apply_time_decay：所有标量向目标半衰期衰减；不发言，stamp_trigger |
| `hidden_state_consolidate` | 7天 | 维护 | hidden_state_decay | consolidate_baselines：sensitivity/touch baseline 轻推向 SCALAR_CENTER；不发言，stamp_trigger |
| `event_log_salvage` | 24h | 维护 | event_log_salvage | 抢救 age 27-29 天、尚未归档的 event_log 日文件里的持久事实，产出走 Brief 45 冲突裁决入口；每次上限3文件；不发言，stamp_trigger |
| `memory_janitor` | 24h | 维护 | memory_janitor | 闲时整合 pass（深夜时段）：episodic 存量近似重复合并（复用写入时去重同一相似度函数，核心记忆不参与，单轮上限10对）+ vec_meta 对照 episodic/近30天event_log 孤儿向量核对（超阈值触发 rebuild）；不发言，stamp_trigger |
| `event_edge_proposer` | 60s scheduler poll; per-scope configurable cooldown | 维护 | event_edge_proposer | 默认关闭。仅发送有限 reality event 窗口给独立模型类别，写未审核候选边和预算台账；不发言、不进 pipeline、不改 recall 或事实。 |
| `private_exchange` | 2h（+ 独立每日 `daily_limit` 会话预算，默认1对） | 维护 | private_exchange | 角色间私下往来（深夜时段，Brief 86）：pair 选择纯规则零 LLM，单次会话 ≤`max_turns`（默认6）次轻量调用；产物只回流 char_relations（既有6h冷却路径）+ 12h presence 提示，transcript 全文不入五大记忆库/event_log/向量库；不发言，stamp_trigger |
| `sensor_aware`（tick） | 30s（可配置） | 低 | sensor_aware | sensor 实时状态主动开口，默认关闭 |
| `hr_high` | 30min | 低 | watch | 心率>100 提醒 |
| `hr_critical` | 1h | **高** | watch | 心率>120 告警 |
| `sleep_end` | 2h | 低 | watch | 睡眠结束感知；`admin/routers/watch.py` 合并睡眠片段后回到 `watch.on_watch_event("sleep_end", ...)` |
| `overflow` | 3h | 低 | overflow | 对话间隔、旧记忆牵引、隐性需求、花园事件、强情绪累计超过阈值后主动联系 |
| `presence_nag` | 2h | 低 | presence_nag | 高活跃配置 + 60min 无互动 + 负面情绪时，下发可强制全关的桌面存在感弹窗 |
| `dream_exit` | 1h | 普通 | dream_exit | 出梦后由 dream_state.char_id 对应角色主动开口；QUIET-only、一梦一次；无 afterglow 时按有限时段降级为中性问候 |
| `letter_writer` | 周频契约 | 低 | letter_writer | 每个 uid + char_id 每 ISO 周最多成功发送一封；失败不封周，梦境、久未对话、强记忆、纪念日前夕或 hidden state 溢出可作为写信缘由 |
| reminders（备忘录） | 无冷却 | 低 | loop.py内联 | 到点即发，发完标记完成 |

---

## 冷却状态持久化

## 邮件周频契约与观测

`letter_writer` 不再把“7 天冷却”解释为每周计划。它按 `uid + char_id + ISO year/week`
持久化 `unattempted` / `failed` / `sent` 三种状态：只有 SMTP 接受邮件后才写入
`sent`，本周其余调度轮次不会再生成或投递；失败仍可在周窗口内再次候选。SMTP 失败使用
有限指数退避，质量拒绝延后到下一次合法调度机会并受本周最大生成次数限制。进程内租约
避免并发 tick 对同一周重复投递。

本周仍欠一封时，候选仅豁免全局 proactive gap 与旧 `letter_writer` 冷却，仍受 QUIET 状态、
用户活跃窗口和 DND 限制；不改变其他 proposer 的通用行为。默认周窗口为周一至周日，可用
`mail.weekly_window_start_day` / `mail.weekly_window_end_day`（0=周一，6=周日）收窄。

每一次执行写入 `runtime/observability/mail_executions.jsonl`，并可通过
`GET /observability/mail-executions`（`state.read`）读取。台账只含 execution_id、uid、char_id、
触发来源、stage/result、有限 failure_code、异常类型、SMTP 状态码、重试数、耗时与时间戳；
绝不写邮件正文、prompt、地址、密码或异常原文。阶段依次为 `selected`、`generated`、
`quality_passed`、`send_attempted`、`sent`，或终止于 `generation_failed`、`quality_rejected`、
`smtp_failed`。

人工验证：先把 `mail.to_addr` 临时设为受控测试收件箱，在管理 token 下调用
`POST /scheduler/trigger/letter_writer`；响应返回 execution_id，随后按该 id 查询该端点。
`smtp_failed` 会给出脱敏 failure_code/exception_type/status_code，
`sent` 会有 Message-ID 等价投递标识。不要把真实密码、收件地址或正文放入日志、截图或工单。

旧 `data/scheduler_state.json` 已由 `_migrate_scheduler_state_once()` 在启动时一次性拆分：

- `data/scheduler_cooldowns.json`：canonical，保存 `_last_trigger`
- `data/runtime/scheduler_user_state.json`：runtime，保存 `last_diary_share`、`trigger_state`、
  followed topics 等用户级运行态

迁移成功后旧文件会删除。冷却文件示例：

```json
{
  "triggers": {
    "morning_greeting": 1748000000.0,
    "random_message": 1748003600.0
  }
}
```

用户级运行态示例：

```json
{
  "last_diary_share": 1748001234.0,
  "trigger_state": {
    "1234567890": {
      "state": "QUIET",
      "since_ts": 1748003600.0,
      "last_owner_turn_ts": 1748003000.0,
      "session_turn_count": 0
    }
  }
}
```

启动时自动恢复（`_load_scheduler_state()` 在模块导入时执行），重启不丢失冷却状态。
状态机从 `scheduler_user_state.json` 的 `trigger_state` 段恢复。

**全局主动间隔/预算独立持久化**（CC 任务 19 · B）：`data/runtime/proactive_ledger.json`
由 `core/scheduler/proactive_ledger.py` 独立维护，不复用 `scheduler_cooldowns.json`；
含 `next_allowed_ts`（A2 一次性 jitter 采样结果）、`daily_count`/`daily_logical_day`
（当日发送预算计数）、`recent`（最近 3 条发送 gist，B3 承接感）。惰性加载，进程内首次
调用 `can_send()`/`record_send()` 时读一次磁盘。

`proactive_ledger.json` 是唯一的主动发言运行时账本。旧的
`proactive_recent.json` 不属于当前运行时协议，scheduler 不会读取或创建它，也不会在
启动时自动删除既有用户文件。

### 已废弃状态文件的人工清理

升级到不含 `proactive_recent` 读写者的版本后，若要移除旧数据，只能在后端服务已停止时
执行。先再次运行全仓静态搜索确认当前版本没有生产读写者，并备份当前遗留位置的两个明确
文件：`data/runtime/proactive_recent.json` 与
`data/runtime/proactive_recent.json.bak`。确认备份可用后，才可以用 `Remove-Item -LiteralPath`
逐一删除这两个文件；不要使用通配符，也不要删除 `proactive_ledger.json`、
`scheduler_user_state.json`、`scheduler_cooldowns.json` 或任何其他 `.bak` 文件。

**A4 失败退避**（内存态，不持久化）：`core/scheduler/loop.py` 的 `_attempt_backoff_secs`
/`_attempt_cooldown_until` 记录 `sent=False` 后的指数退避窗口（首次 15min，翻倍，封顶
该触发器自身 `_COOLDOWNS`），重启清零可接受——退避窗口相对重启周期很短。

---

## 管理面板集成

- `get_status()` → 返回所有触发器的上次触发时间、冷却剩余秒数、是否 ready
- `manual_trigger(name)` → test-only 兼容入口，只排队一个 autonomy signal；不绕过冷却、
  talk gate 或生产 admission，也不直接发送。HTTP 响应带 `direct_delivery=false`。
- `manual_trigger("period_reminder")` 在 owner 没有 `last_period_date` 时稳定返回
  `missing_period_date`，不创建 signal；有日期时仍只排队 migrated autonomy opportunity，
  不恢复旧的 direct-send 分支。

当前手动测试触发覆盖：`morning_greeting`、`night_reminder`、`random_message`、`daily_journal`、
`period_reminder`、`diary_reminder`、`diary_share_reminder`、`topic_followup`、
生日四段、`timenode`、`festival`、`holiday_boost`。
未覆盖：天气、记忆衰减、episodic sweep、garden_water、garden_daily、watch 事件、DLQ 监控、activity switch 等。

---

## 主动触发 Prompt 可观测性（admin-panel-round6）

`_pipeline_send()`（scheduler 兼容触发）在调用 `build_prompt()` 前，通过
`core/observe/prompt_capture.py` 的 ContextVar `_capture_origin` 写入主动触发的元数据，
`capture()` 随即把这些信息写入快照。`desktop_wake` Path B 已不调用这条 pipeline；它的
signal、合并机会、run 和 disposition 由 autonomy 观测面记录：

| 字段 | 内容 |
|---|---|
| `origin.origin` | `"proactive"` |
| `origin.trigger_name` | 触发器名，如 `"random_message"` |
| `origin.seed_prompt` | 喂给 `build_prompt` 的用户位消息（第 12 层实际来源） |
| `origin.search_query` | 驱动 RAG/event 召回的锚点词（`fetch_context` 第二参数）；空字符串表示与 seed_prompt 相同 |
| `origin.recall_policy` | CC 任务 19 · C：`"none"` / `"anchored"` / `"seed"`，见下方「召回锚点治理」 |

`update_llm_output()` 同样在 `run_llm()` 返回后被调用，配对写入 LLM 回复文本。

### 召回锚点治理：recall_policy（CC 任务 19 · C，RC6 修复）

主动触发最容易"乱召回"的根源（RC6）：召回锚点不是真实用户输入，而是触发器的种子词。
`festival`/`timenode`/`daily_journal` 等此前用 `search_query="今天"`，`diary_reminder`/
`diary_share_reminder` 用 `"日记"`，而 `sensor_aware`/`presence_nag`/`dream_exit`/
`overflow` 等根本不传 `search_query`，直接用整段括号叙事 prompt 当检索词——宽泛词命中
一堆无关记忆，注入 prompt 后角色"胡乱召回然后说一大堆废话"。

`_pipeline_send()` / `execution.execute_prompt()` 均新增 `recall_policy` 参数
（默认 `"seed"`），一路传到 `pipeline.fetch_context(recall_policy=...)`：

| 档 | 行为 | 适用触发器 |
|---|---|---|
| `"none"`（主动触发默认取向） | `fetch_context` 完全跳过 `episodic_memory.retrieve`/`retrieve_fallback`、`event_log.search`、web_recall 三个检索层（含驱动它们的 embedding 计算），只保留 identity/mood/short_term/花园等状态层 | `random_message`、`weather_alert`、`sensor_aware`、`presence_nag`、`garden_bloom`/`garden_harvest_expired`/`garden_handle_*`/`garden_vase_wilted`、`festival`、`holiday_boost`、`timenode`、`daily_journal`、`morning_greeting`、`night_reminder`、`diary_reminder`、`diary_share_reminder` |
| `"anchored"` | 检索层照常开启，锚点是触发器自带的具体话题（话题 key、被选中记忆的原文），不是宽泛种子词 | `topic_followup`、`spontaneous_recall`、`birthday` 系列、`period_reminder`（`search_query="生理期"`） |
| `"seed"`（默认，兼容过渡） | 现状：锚点是 `search_query` 或 prompt 全文，检索层照常开启 | 未显式指定 recall_policy 的触发器（如 `reminders`、`overflow`、`dream_exit`、`letter_writer`、watch 系列） |

`daily_journal` 的特例：不再传 `search_query="今天"` 驱动语义检索，改为只依赖种子
prompt 里已经拼好的当日 `event_log` 原文（`log_hint`），`recall_policy="none"`。

在 **Prompt 层检视** 页查看主动轮次时，快照总览会明确标出 `search_query` 和
`recall_policy`；`recall_policy="none"` 的轮次里 `6b_event_search`/`6c_episodic`
层应为空。每个 `scored` 层的 `rag_query` 字段仍反映实际驱动检索的锚点（`"none"`
轮次里这些层不出现）。

### 管理面板入口

| 功能 | 页面 |
|---|---|
| 查看任意轮 prompt 层（含主动轮）| **Prompt 层检视** → 轮次选择器；主动轮有绿色「主动 · trigger_name」徽章 |
| 查看主动轮种子 prompt + search_query | 同上，总览卡展开「主动触发详情」面板 |
| 查看所有触发器最近一次真实快照 | **触发器目录** → `GET /observe/trigger-catalog` |

---

## 请勿打扰（DND）模块

文件：`core/scheduler/triggers/dnd.py`

**R2-D 完成：已接入 `main.py` owner 消息路径。**

参见上方 [DND 主入口（R2-D）](#dnd-主入口r2-d) 章节。

`is_dnd(uid)` 已在 `gating._decide()` 的 DND filter 中使用（R2-B 起）；
`detect_and_set(uid, content)` 已在 `main.py::handle_message()` 的 owner block 调用（R2-D）。
`loop.py` 不直接调用 `is_dnd`（DND 决策属于 gating 层，不属于执行层）。

---

## sensor_aware 信号适配器

sensor 实时状态感知触发器，是"他主动开口"链路的最终出口。

| 项 | 值 |
|---|---|
| 配置位置 | `scheduler.sensor_aware.enabled` |
| tick 间隔 | `scheduler.sensor_aware.tick_interval_seconds`（默认 30） |
| 默认状态 | **disabled**（`enabled: false`） |
| 启用方式 | `config.yaml` 设置 `enabled: true`，重启服务 |
| 全局发言冷却 | 先由 `proactive_ledger.can_send("sensor_aware")` 做只读闸门；信号入队另有 15 分钟 dedupe bucket。真正送达后由 autonomy `talk_gate.send()` 记账 `record_send("autonomy")` |
| 所在文件 | `core/scheduler/triggers/sensor_aware.py` |

### 行为级别

| 级别 | score 阈值 | 历史 action 意图 | 当前 signal-first 行为 |
|---|---|---|---|
| `passive_speak` | ≥ 35 | 无 action | `behavior_id` 仅作为 signal evidence，最终由 autonomy 决定是否 talk |
| `soft_hint` | ≥ 50 | `pet_emote` | 当前不自动执行 action；只保留候选 evidence |
| `attention_grab` | ≥ 65 | `notify` | 当前不自动执行 action；只保留候选 evidence |
| `direct_act` | ≥ 80 | `execute` | 当前不自动执行 action；恢复需另立 autonomy payload/协议设计 |

### A3/B 纳管：ProactiveLedger + DND + signal-first

当前 `handle_tick()` 在 judge/LLM 之前执行 `proactive_ledger.can_send("sensor_aware")`
和 `is_dnd(uid)` 检查；通过后只调用 `emit_trigger_signal()` 入队，不直接调用 LLM、turn sink
或 channel。信号以 `sensor_aware:<15-minute-bucket>` 做去重，后续由 autonomy runner 合并
opportunity，并仅在显式 `talk_owner` 成功时进入 `talk_gate.send()` → `record_assistant_turn()`。

真正送达后由 `talk_gate.send()` 调用 `proactive_ledger.record_send("autonomy", ...)`，因此
其他主动触发器看到的是统一 autonomy 记账。旧的 8 分钟私有 `sensor_events` 冷却和
`sensor_events.mark_proactive_sent()` 仍留在 `handle_tick()` 返回后的不可达兼容分支中，不是
当前 signal-first 发送路径。

旧分支仍保留在源码中供迁移审计，但已由 `return` 明确封存；其中的
`_pipeline_send(output_mode="return")`、`build_action_packet()` 和 `record_assistant_turn(SENSOR)`
不能写成当前生产链路。

### 触发链路

```
scheduler._check_sensor_aware()         ← loop.py 每 60s 检查一次（受 tick_interval_seconds 门控）
  → sensor_events.tick()               ← 返回本 tick 候选事件列表
  → sensor_judge.judge(event)          ← 客观评分，附 intent_tier
  → BehaviorPlanner.plan(event, score) ← 硬代码行为决策，score < 35 → 丢弃
  → proactive_ledger.can_send() + is_dnd()  ← 被拦则不入队
  → emit_trigger_signal()              ← 15 分钟 bucket 去重后写入 autonomy signal store
  → autonomy runner 合并 opportunity / bounded context
  → 显式 talk_owner → talk_gate.send()
  → record_assistant_turn(source=TRIGGER, trigger_name="autonomy")
                                      ← 写记忆 + 按 autonomy fanout 推送
  → proactive_ledger.record_send("autonomy")
```

当前 signal-first 路径只携带 `behavior_id` 等候选证据，不自动生成或执行旧的
`build_action_packet()`；行为 action 若要恢复，需要单独的 autonomy payload/协议设计。

### 与 chat router 的联动

`POST /desktop/chat` 或 `POST /mobile/chat` 成功处理后调用 `sensor_events.notify_chat_happened()`，重置 `SILENT_TOGETHER` 和 `LONG_FOCUS` 的冷却窗口，避免"人刚聊完立刻被问候"。

### 审计接口

每次 `handle_tick()` 执行完毕（无论走哪条路径），都会向模块级 ring buffer 写入一条决策快照。Buffer 上限 50 条，纯内存，重启清零。

**接口**：`GET /scheduler/sensor_aware/audit`

| 参数 | 说明 |
|---|---|
| `n` | 返回条数，默认 50，最大 50 |
| Authorization | Bearer token（同其他管理接口） |

**响应结构**：

```json
{
  "count": 3,
  "entries": [
    {
      "tick_at": 1747900000.0,
      "candidates": [{"type": "LONG_FOCUS", "narrative": "...", ...}],
      "picked_event": {"type": "LONG_FOCUS", ...},
      "judge_input_prompt": "[SYSTEM]\n...\n\n[USER]\n...",
      "judge_output_raw": "{\"score\": 72, \"reason\": \"专注时间较长\"}",
      "judge_score": 72,
      "judge_reason": "专注时间较长",
      "tier": "medium",
      "candidate_behavior": {"level": "soft_hint", "behavior_id": "focus_acknowledged", ...},
      "signal_queued": true,
      "final_stage": "signal_queued",
      "cooldown_remaining_seconds": null
    }
  ]
}
```

字段拿不到时为 `null`，结构始终完整（不省略 key）。

```
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/scheduler/sensor_aware/audit?n=5"
```

**实现位置**：`core/scheduler/triggers/sensor_aware_audit.py`（ring buffer） + `admin/routers/scheduler.py`（路由）。

---

## 新增触发器规范

1. 在 `core/scheduler/triggers/` 下选择合适文件（或新建），优先实现只读 `propose(ctx)`。
2. 在 `core.scheduler.gating.TRIGGER_MIGRATION_STATUS` 登记唯一生命周期：`migrated`、
   `maintenance-only`、`retired` 或 `active`；不得依赖未登记的兼容直发路径。
3. 主动发言候选只携带 bounded facts，经 signal-first autonomy 决定是否发言；不得新增
   `execute_prompt()` 或 `_pipeline_send()` 直发。静默维护写入由自己的 worker/capability 负责。
4. 在 `loop.py` 的 `_COOLDOWNS` 字典里加冷却时间；只有最终可见投递成功才能 `_mark()`。
5. 需要状态扫描的触发器才保留 `_check_xxx()` 并加入 `_loop()` gather；不要为纯发言新增 legacy 路径。
6. 如果是高优先级，明确 `bypass_state_machine`，必要时加入 `_HIGH_PRIORITY_TRIGGERS`。
7. 如果需要管理面板手动触发，只允许排入同一 signal/task 生命周期，不得恢复 force-send。
8. 补 proposer / live / blocked、唯一生命周期和 EventContext 非污染单测，并更新此文档列表。

### dream_postcards

### Brief 237 旧路径退役

`_check_reminders()` 通过 `talk_gate` 的 Reality interaction adapter 投递，不再调用
`_pipeline_send()`；`manual_trigger()` 对 retired/unregistered 名称 fail-closed。旧 reminder
JSON 仅保留读取兼容函数，不再作为 `add_reminder()` 的写入 fallback。

`dream_postcards` 是独立 `active` 的定时产物投递，不是 `dream_exit` 别名。proposer 每日扫描梦境
archive 出站明信片的 schedule；到期未发送的条目复用 Gmail 链路投递，不进入 assistant speech
outlet。SMTP 失败只递增 attempts 并保留 last_error，后续 tick 持续重试，成功后才标记 sent。

### Dream continuation（Brief 170）

用户明确退出且角色通过 `dream_control.exit=accept` 后的 Reality continuation
不是 scheduler proposal：它直接等待 uid conversation gate，走正常 Reality
`fetch_context → build_prompt → run_llm → turn_sink → fanout`，不检查 QUIET、
DND、全局 gap、主动预算或 winner。发送成功后与 `last_greeted_dream_id` 兼容
去重；发送失败保留同一 lifecycle ledger 的可恢复记录，新 Reality 用户回合以
`new_user_turn` 取消，避免插入用户对话。

`exit_lifecycle.json` 通过 `delivery_kind` 区分 `continuation` 和
`scheduled_greeting`。普通 `dream_exit` proposer 会跳过 continuation 的
pending/failed/sent 记录，因此非 continuation Dream 仍保持原有 aging、DND、
QUIET、预算和 winner 约束。

### dream_exit（Brief 164）

`dream_exit` 只从 `REALITY_AFTERGLOW` 的单人 Dream 状态产生候选，必须经过 QUIET、DND、conversation gate、全局间隔与主动消息预算。候选使用有限 aging：等待时间增长后从 reactive 提升到 window-event，再到 must-not-miss；这只影响普通候选 winner，不绕过任何发送闸门。活跃窗口不强插，超过有效窗口写入 `expired`。

每个 `dream_id` 的发送结果写入 `data/runtime/dreams/{char_id}/exit_lifecycle.json` 的脱敏行，管理面通过 `GET /dream/operations` 读取。发送成功后才写 `last_greeted_dream_id`；失败保留可重试记录。Shadow/gating/autonomy 之间只传递 `dream_id` 和固定事实，不传递梦境正文。

---

## 冷启动门控（Brief 97）

> 背景：全新 `data/` 首启实测复现——`diary_reminder` 首轮就说"你好久没写日记了"，
> 根因是 `yesterday_missing()` 只判断"昨天有没有日记文件"，零数据时恒为 True；
> `diary_share_reminder` 同理，`_last_diary_share` 初始为 0，`now - 0` 恒大于三天
> 阈值。两者都把"从未有过记录"误读成"有记录但很久没更新"。

`core/scheduler/rhythm.py` 新增两个 helper：

```python
real_turn_count(uid, *, char_id=None) -> int
    # 统计 short_term 里 role="user" 的真实条目数。trigger 侧种子旁白从不写入
    # short_term 的 user 位（fixation_pipeline.capture_turn 的 P0 边界规则），
    # 所以这个计数只反映真实用户交互，不会被调度器自己的主动轮污染。

has_real_interaction_history(uid, *, char_id=None, min_turns=COLD_START_MIN_REAL_TURNS) -> bool
    # real_turn_count(uid) >= min_turns（默认 5，常量 COLD_START_MIN_REAL_TURNS）
```

依赖"久未见/久未写"语义的触发器，在自己的时间窗判断之前先查 `has_real_interaction_history`，
不满足直接 skip（记 debug 日志，不算异常）：

| 触发器 | 接入点 |
|---|---|
| `diary_reminder` | `_check_diary_reminder()` / `propose_diary_reminder()`（`diary.py`），额外接入 `diary_reader.has_any_diary_entry()`：即使聊天轮数够了，只要日记目录（`obsidian_path` 或本地 `diary_fallback/`）从没出现过一篇日记，也不触发——不能把"从没配置/从没写过"读成"漏了一天"（用户复核追加，2026-07-18） |
| `diary_share_reminder` | `_check_diary_share_reminder()` / `propose_diary_share_reminder()`（`diary.py`），同时把 `_last_diary_share <= 0`（从未分享过）与"分享过但已过 3 天"拆开，前者一律不触发 |
| `interest_seed` | `_check_interest_seed()`（`interest_seed.py`） |

`festival`/`timenode`/`holiday_boost`/`overflow`/`letter_writer`/`presence_nag` 未接入：
前三者只依赖真实日历日期 + `owner_id` 是否存在，不引用"上次交互"时间差，对全新用户
不构成虚假记忆；后三者的时间差信号已经用 `if timestamps:` / `last_owner_turn <= 0 →
None` 等方式正确区分"没有数据"与"很久以前"，本就不受冷启动误判影响，不需要重复加门控。

同一原则也用于关系层注入判断（`core/user_relation.has_configured_relation()`，见
`docs/prompt-layers.md` `3_relation`）。`desktop_wake` Path B 不再构造首轮或重开 seed
prompt；它只提供 `desktop_session_reopened` 这一当前事实，是否读取有界历史、使用工具或
发言由 autonomy runner 决定。
# Memory Event maintenance final contracts (Brief 214)

`storyline_weekly` uses an all-or-nothing plan. Invalid op types, fields, arc
references, material IDs, limits, status, spans, future timestamps, or
non-monotonic timestamps reject the complete batch without advancing the
cursor or cleaning the inbox. A successful atomic storyline commit advances
the version-2 day/byte cursor and receipt before idempotent inbox cleanup.

`event_edge_proposer` discovery only considers the canonical existing
`.sqlite3` path. Its read-only health check requires the exact ledger version,
all five tables, required columns, and matching scope. Discovery classifies
`missing`, `version_mismatch`, `table_missing`, `column_missing`,
`scope_mismatch`, `database_error`, and `timeout`; it never creates or upgrades
a ledger. Proposal writes revalidate that contract and remain disabled by
default.

## 周信发送断链修复（2026-09-11）

`current`：letter_writer 曾被标为 migrated，winner 仅转入通用 TALK 信号，
而 autonomy 没有邮件发送适配器，导致原邮件回调永远不执行。现将邮件能力准确标为
active，仍经原 QUIET/活跃窗口/DND/角色主动开关与周契约准入，执行仅走邮件子系统。
无论 autonomy 是否开启，scheduler 都经 run_shadow_tick：已迁移的聊天触发器只入队，
不执行旧回调；原生邮件/明信片可继续交付。没有恢复 retired assistant speech outlet。
周契约未到期（已发送、退避、租约或窗口外）时不再落入事件理由旁路。

`current`：GET `/observability/mail-weekly`（state.read）显示配置齐备、scheduler 开关、
窗口、scope_ready、当前周状态及下一次重试，沿用 `/observability/mail-executions` 查阶段。
周信是“每 ISO 周最多成功一封，到期可候选”，不是固定星期保证送达；阻断可导致本周未发。
原手动 trigger 仍只排队测试信号，不会直接发信，不能再按旧文档把它当 SMTP 验证入口。

`observe`：本地配置中 mail/scheduler 已开启且 SMTP 字段齐备，历史台账最后记录为
empty_content/quality_rejected，没有 SMTP 成功证据。本次仅 mock 回归，不发送真实邮件，
不修改收件地址、凭据或运行配置；部署后下一次合法调度需要观察生成质量和 SMTP。
`roadmap`：Task/Agent authored artifact 邮件迁移尚未完成，不把通用 TALK 信号当邮件实现。
管理面继续使用邮件配置和只读观测；桌面/手机不用新增发送控件，未跨仓修改。

验证：41 项邮件/周契约/gating/信号回归通过；相邻测试文件另有一个既有 MCP 静态版本
断言失败（要求早期 brief-195 版本字串），与邮件执行无关，未修改该旧断言。