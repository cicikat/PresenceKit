# Runtime 生命周期

> Emerald-Presence 的 runtime 启动顺序和长期组件 ownership。
>
> 本文描述当前 runtime topology，不是未来 architecture proposal。

---

## 1. 进程入口

Backend entry point：

```text
python main.py
```

`main.py` 负责应用启动。

除 startup path 外，任何 subsystem 都不应独立创建 global runtime instance 或 background service。

---

## 2. 后端启动顺序

当前启动顺序：

```text
main.py
|
+-- 加载配置
|
+-- 校验 admin authentication
|
+-- 加载 active character
|
+-- 初始化 lore engine
|
+-- 创建 Pipeline
|
+-- 注册 Pipeline
|
+-- 注册 slow handler
|
+-- 清理 pending state
|
+-- 启动 background service
|
+-- 启动 HTTP/admin service
```

---

## 3. 核心 Runtime Owner

### Pipeline

Owner：

```text
main.py
```

创建：

```text
Pipeline(character, lore_engine, active_character_id)
```

注册：

```text
pipeline_registry.register()
```

生命周期：

```text
进程生命周期
```

职责：

- conversation processing
- prompt construction
- memory interaction
- LLM execution coordination

其他模块应通过 registry 访问 Pipeline，不要自行创建实例。

---

## 4. Scheduler 生命周期

Owner：

```text
main.py
```

启动：

```text
scheduler.start()
```

生命周期：

```text
background asyncio task
```

职责：

- 周期性检查
- proactive proposal
- trigger evaluation
- gating
- execution coordination

Scheduler 结构：

```text
scheduler loop
|
+-- trigger module
|
+-- proposer registry
|
+-- gating
|
+-- execution
|
+-- turn sink / output
```

重要约束：

Scheduler 依赖 Pipeline 先完成初始化。

Pipeline 注册前执行 trigger 是无效行为。

---

## 5. Registry Ownership

系统当前使用多个 registry。

它们解决不同问题；没有明确理由时不能合并。

---

## Pipeline Registry

用途：

保存 active Pipeline instance。

流程：

```text
main.py
|
v
Pipeline creation
|
v
pipeline_registry.register()
```

---

## Tool Registry

用途：

注册可用工具。

流程：

```text
tool module
|
v
tool dispatcher registry
|
v
LLM/tool execution
```

---

## Scheduler Proposer Registry

用途：

允许 trigger module 提交 proposal。

流程：

```text
trigger module
|
v
register_proposer()
|
v
scheduler gating
|
v
execution
```

---

## 6. 长期组件

| 组件 | Owner | 生命周期 |
|---|---|---|
| Pipeline | main.py | 进程生命周期 |
| Lore Engine | main.py | 进程生命周期 |
| Scheduler Task | scheduler.start() | 进程生命周期 |
| Hardware job manager | main.py → `core.hardware.jobs.startup()` | 进程生命周期；shutdown 时停止/取消 worker |
| FastAPI Admin Server | main.py | 进程生命周期 |
| Sensor worker | 对应 runner | 启用时为进程生命周期 |

FastAPI admin server 和 QQ listener 在 `main.py` 后面运行于独立的 service boundary。某个 service 的 bind failure、`SystemExit` 或普通 exception 会被记录，但不会取消另一个 service。进程 shutdown 时，协作式 task cancellation 仍会传播。

---

## 7. Background Worker

Background service 必须具备：

- 明确的 owner
- 明确的 startup location
- 明确的 shutdown behavior

当前示例：

```text
scheduler task
sensor runner
visual observation runner
hardware worker
```

Hardware job manager 在 Pipeline setup 后由 `main.py` 启动。它会把前一个进程留下的 active job 标记为 `expired`，尝试显式停止，并在 shutdown 时注销 device-disconnect listener。Worker 永远不会从 module import 时启动。

模块不应在 import 时静默创建 background task。

---

## 8. Import 规则

Import module 不应：

- 启动 thread
- 创建 network connection
- 创建 global runtime object
- 启动 asyncio task

初始化属于 startup code。

---

## 9. Event Flow

当前消息流：

```text
Input
|
v
Channel / API
|
v
Pipeline
|
v
LLM + tools + memory
|
v
Turn Sink
|
v
Output
```

Proactive flow：

```text
Sensor / Scheduler / Trigger
|
v
Proposal
|
v
Gating
|
v
Execution
|
v
Pipeline / Output
```

---

## 10. EventBus 状态

当前系统不使用 universal EventBus。

现有机制：

- pipeline registry
- tool registry
- proposer registry
- perceive_event
- turn sink

它们目前代表不同的边界。

只有在明确需要以下能力时，未来才应引入 EventBus：

- 跨模块异步通知
- 多个独立 consumer
- lifecycle ownership

不要只为了 abstraction 就用 generic event bus 替换现有 registry。

### Agent Runtime target contract (Brief 229)

未来 Agent Runtime 按 Clock/Trigger、Task、Agent、Capability、Interaction 五个 plane 分层，
但不通过 EventBus 合并现有 registry。完整目标和现有组件迁移映射见
`agent-runtime-architecture.md`。本 brief 不创建 runtime owner、worker、store 或 startup/shutdown
hook；这些必须由 Brief 230-237 逐项实现并补齐生命周期验证。

---

## 11. Dream continuation recovery（Brief 170）

`main.main()` 注册 channel 后，进程会调度有界的 `core.dream.reality_continuation.recover_pending()` 扫描。它只消费现有 Dream exit lifecycle ledger，并重新排队 `pending` 或 `failed` row。Continuation worker 随后等待 per-owner conversation gate，并使用正常 Reality pipeline；它不是 scheduler tick、proposal、winner 或单独的 persistent ledger。Send marker 在 `record_assistant_turn()` 返回后写入；客户端负责匹配 Dream active-to-closed transition 和一次性 navigation close。

## Brief 171：Remote deployment preflight

`deployment.mode` 是进程配置（默认 `local`，或 `remote_server`），不能由 request、prompt 或 model 修改。只读的 `/system/deployment-preflight` projection 报告 bind mode、声明的 TLS/WSS、persistent-root 可写性、desktop WS state、已禁用 capability、diary-sync state，以及未执行 port scan。它是 readiness projection，不证明外部 tunnel 或 backup 健康。

在 remote mode 下，服务端本地 OS operation 和客户端 file fallback 被禁用。Desktop action 使用现有 heartbeat/ack WebSocket path；owner turn 和 mobile polling 仍使用正常 HTTP/queue path。

## External Companion receipt/session ownership（Brief 193）

`main.py` 创建并注册 Pipeline；companion router 不创建第二个 Pipeline，也不直接调用
LLM。请求通过 `core/companion/service.py` 读取 pipeline registry，在执行前冻结 owner
和 active `MemoryScope`，再调用共享 owner-turn executor。

`core/companion/store.py` 拥有 `data/runtime/companion/` 下的 metadata-only
receipt/session/stats 文件，路径全部来自 `core/sandbox.get_paths()` 的 DataPaths
accessor，写入使用 atomic replace。receipt 以 caller + `session_id + event_id`
幂等；`running` 先持久化，完成后 terminalize。进程重启不会恢复或重跑旧 running
任务，后续请求返回 execution outcome unknown 的 503。新 session 只有在
`created_at` 严格更新时推进 current，旧 session 永久失效。

进程内 companion service 维护 inflight 集合，仅用于判断当前任务是否仍由本进程拥有；
它不是持久化执行证明。每次 reserve 会按 30 天窗口和每 caller 1000 条上限 prune
terminal receipts。`GET /observability/companion-events` 是对应的 `state.read`
只读 projection，返回运行可用性、session 更新时间、计数、bounded metadata rows
和 prune 信息，不返回正文、reply、token、owner 或路径。

---

## 12. 已知缺口

### 缺少 shutdown 契约

当前生命周期文档重点描述 startup。

未来工作：

- service shutdown 顺序
- task cancellation
- resource cleanup

### 缺少 runtime topology 视图

未来改进：

记录：

- 正在运行的 worker
- owned task
- persistent state owner
- communication path

## Brief 217 EventContext startup gate

During `_init_modules()`, existing Reality event ledgers are discovered and
upgraded before channel or scheduler work starts. With observer mode set to
`observe`, any failed ledger or bounded-scan truncation aborts startup. When the
service starts with observation disabled, the PUT setting repeats this check
before enabling observation. Schema work stays outside the realtime send path,
while a failed trace write remains fail-open.

## Brief 176：Dream WAKE 确认与跨进程收口

Dream 的 `DREAM_EXIT_REQUESTED` 是退出确认待决态，不是已关闭态。客户端的“留下”通过带同一 `dream_id` 的 `/dream/resume` 恢复 `DREAM_ACTIVE`；“还是要醒来”、再次 WAKE 或 Esc 通过唯一的 `force_exit_dream()` / `_do_close_dream()` 收口。只有后端确认 close/archive 成功后，桌面窗口才关闭；请求失败时保留 current session 和可重试出口。

后端关闭成功的持久不变量是：active `dream_id` 清除、`last_dream_id` 固定、current transcript 移入对应 archive，后续 archive replay 只读且不能 resume、send、WS/TTS 或重新写 current。`archive_ok=false` 时保持 `DREAM_CLOSING`，不得由 UI 关闭掩盖失败。重复调用返回首次关闭 metadata，并通过 operations 观测重复次数；旧 `dream_id` 的迟到请求不得影响新梦。

这条链路的单元/协议检查不替代真实跨进程验收。完整验收仍需在不污染真实 runtime/userdata 的隔离环境中，真实启动 backend 与 desktop，验证“挽留 → 确认醒来 → 窗口关闭 → 只读回放 → 旧梦不可发送 → 新梦 ID 不同”。
