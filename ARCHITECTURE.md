# ARCHITECTURE.md — 系统架构总览

主动消息采用 signal-first autonomy：scheduler/sensor 只产生带事实、理由、优先级、时效、记忆锚点和行动模式的 `autonomy-signal.v1`；每个 tick 合并为一个 `autonomy-opportunity.v1`，由 `core/autonomy` 评估。只有 autonomy job 内显式调用 `talk_owner` 才能进入 `turn_sink`，旧 scheduler 直发路径按 `docs/autonomy.md` 迁移清单逐项封存。

Agent Runtime 的分层、身份边界、现有 trigger/tool/store 映射和 Reality/Dream 隔离合同见
`docs/agent-runtime-architecture.md`。它是同一角色的 durable / specialized 副链运行时，
不是角色外的第二个 Agent；走 Work Session 只代表执行链不同。

current（Briefs 230–234 / 239）：Reality Task Manager、Work Session、workspace、
local process runner 与 browser confirmation hardening 已落地。生产路径只走
`execute_structured()`；tuple `execute()` 已删除。
roadmap：Briefs 235–237 的其余 gated capability、覆盖证明与旧路径联删；Dream 侧独立
runtime；`letter_writer` 的 Task/副链产物迁移。Brief 229 本身仍是架构合同，不新增
客户端字段。运行细节以本文和各专题文档为准。

---

## 系统全貌

角色通过 QQ、桌宠和调度器三个入口进入同一条 pipeline，输出再交给通道层发送：

```
QQ 消息 → main.py → message_queue
桌宠消息 → admin/routers/chat.py（POST /desktop/chat）
手机前台消息 → admin/routers/mobile.py（POST /mobile/chat，使用 mobile provenance，共用 owner-chat pipeline）
文件上传 → POST /upload/ingest → media_processor → 拼入用户消息
调度器主动消息 → core/scheduler/loop.py
         ├─ state_machine：观测 owner turn / sensor tick，维护 CHATTING / QUIET / RESTLESS
         ├─ gating/policy：统一 state / active-window / DND / defer / cooldown 决策（含 Watch 发言事件）
         ├─ proposer dry-run：记录 would-send / would-mark；live winner 进入统一执行层
         ├─ dream_exit：出梦后 QUIET-only 主动开口，一梦一次，沿用 dream_state.char_id
         ├─ policy.py：active-window / DND 运行时策略权威
         ├─ EXECUTE_MODE：当前为 live（见 core/scheduler/execution.py）
         └─ legacy gather：发言型 trigger 让路，maintenance/state scan 保留
         ↓（入口共用）
      Pipeline（core/pipeline.py）
         ↓
      LLM（Model Registry：按 routing profile 解析 DeepSeek / Claude-compatible / OpenAI-compatible / local preset）
         ↓
      输出层（消息信封可选携带 `char_id` 发言人字段）
         ├─ QQ 主入口：_qq_reality_reply_adapter 可见发送 + turn_sink 记忆写入
         └─ desktop/mobile/scheduler/sensor：core.turn_sink 写入 + channels.registry 广播活跃通道
```

桌面 `message_segments` 的 `say` 段可选携带句级表演 spec（`perform`，`core/perform_mapper.py`，
fail-open），见 `docs/perform-mapping.md`。
桌面端当前正式协议为 v0.1（legacy 冻结版）：用户输入正式走 `POST /desktop/chat`，服务端通过 `/ws/desktop` 下发回复与动作；本仓入口见 `docs/desktop-client-protocol.md`，协议正文唯一权威位于 PresenceKit-desktop 的 `docs/protocol-v0.md`。v1 未排期，双方均未实现。

通道细节见 `docs/channels.md`。手机端当前通过 mobile 轮询通道接收主动消息，不占用桌宠 WebSocket。
表情包仍由 QQ 走 OneBot 图片段；桌宠 WS 与 mobile 队列则收到不含本机路径的自包含 `sticker`
payload，旧客户端可忽略，渲染由客户端实现。花园这类不进入对话 pipeline 的伴生状态，见
`docs/garden.md`。

ESP32 具身硬件通过 `/ws/device` 接入，帧格式与桌宠端 `/ws/desktop` 一致（见 `docs/channels.md`）；
固件本身（板型、代码结构、鉴权配置）见 `docs/presence-device-firmware.md`
（`firmware/presence-device/`，不要与已废弃的 `hardware/_achieve_Emerald-hello` 测试项目混淆）。
Dream Session 后续必须走独立 pipeline，不进入当前现实对话 pipeline，也不走现有 `post_process`。

Scheduler proposal 显式携带 `char_id` 时，`execute_prompt → _pipeline_send` 会冻结该角色的
Reality scope；若它不是当前 active character，Pipeline 按该 scope 加载对应角色卡与世界书，
避免“做梦角色”和实际发言角色错位。

多角色群聊使用独立 `core/stage/` Session 内核：Stage 持有 roster、共享 transcript 和纯规则回合仲裁，
一整轮 Phase A（直接回应）+ Phase B（自主续聊，轻量 prompt 视图）+ Phase R（短反应，可选）+
Phase T（话题引子，可选）共用一次 owner conversation lock，零后台自发 LLM 调用（Brief 85）。
Reality Stage 通过 per-character 只读生成视图显式绑定角色卡与 memory scope，群 transcript 独立
注入 prompt；角色间关系（`char_relations`）参与 Phase B/R 仲裁打分与定向接话内容。回合后只把摘要
按 `group:{group_id}` 来源送入各角色 fixation 链。Dream Stage（Brief 100 v1）已实现且仅
sandbox：scenario / mirror / D4.5 硬禁用，零回流，hard_exit 绝对；物理树
`data/runtime/dreams/_stage/{group_id}/`，不进入 reality loader。详见 `docs/stage.md` §六。

Intiface / Buttplug 硬件是 reality-side actuator：只有 owner 私聊中的真实 turn 工具调用可触发，
不进入 scheduler、trigger 或 Dream pipeline。`core/hardware/buttplug_client.py` 通过
`aiohttp` 的无代理 WebSocket 连接本机 Intiface Central，并维护进程内设备发现状态。

手机端与桌宠用户输入分别走 `POST /mobile/chat` 与 `POST /desktop/chat`，但复用同一 owner-chat
执行链。两条入口都通过 `core/conversation_gate.py` 的 per-user conversation lock，保证同一用户多端输入按顺序完成
`fetch_context → run_llm → critical post_process`。记忆文件自身仍由 `core/memory/locks.py`
里的 `uid_lock` 保护。

---

## Pipeline 四步骤

```
用户消息
    │
    ▼ 步骤0（在 pipeline 之前）
探针判断工具（QQ：关键词快速路径 + LLM probe；desktop/mobile：LLM probe；只看 info+desktop 类）
get_tags()（build_prompt 内计算；部分入口可显式传入复用）
工具执行（结果写入 tool_result）
  └─ Path C tool loop 有效时：跳过 LLM probe，工具决策交给 chat preset 的 function calling
     （QQ 关键词快速路径仍先执行并把结果注入 tool_result）
    │
    ▼ 步骤1  fetch_context()
并发拉取所有记忆数据：
├─ short_term.load_for_prompt() → history            [同步，speaker-aware turn-group；近场保留 + 远场加权择优]
  ├─ user_relation.get_relation()→ relation             [同步]
  ├─ group_context.get_recent() → group_context         [同步]
  ├─ user_identity.format_for_prompt() → user_identity_text [异步]
  ├─ lore_engine.match()        → lore_entries          [同步]
  ├─ episodic_memory.retrieve() → episodic_result       [同步]
  ├─ episodic_memory.retrieve_fallback() → episodic_fallback_result  [同步]
  ├─ get_reminders()            → reminders             [同步]
  ├─ diary_context.load()       → diary_context         [同步]
  ├─ user_profile.load()        → profile               [异步线程]
  ├─ mid_term.format_for_prompt() → mid_term_context    [异步线程]
  └─ event_log.search()         → event_search_result   [异步]
    │
    ▼ 步骤2  build_prompt()
  get_tags() 计算标签（或复用探针阶段的）
  读取 pending_perception（上轮失败的桌面动作感知）
  调 prompt_builder.build() 组装 messages[]
    │
    ▼ 步骤3  主生成
  Path C 激活（有效 tool_loop 开关 + owner 私聊 + function_calling preset）→
    Pipeline.run_agentic_loop()：chat_turn ↔ execute(origin="assistant_loop")，最多 max_steps / total_timeout_s
  否则 → llm_client.chat(messages)，含重试
    │
    ▼ 步骤4  post_process()（owner 入口与调度器主动消息通过 turn_sink 等待关键写入）
  │
  │  【关键路径】send 前在 uid_lock(uid) 内只完成本地写入与条件读取：
  ├─ maybe_mark_sleepy_from_time       本地 sleepy 状态写入（无 LLM/网络往返）
  ├─ capture_turn()                    写 history + event_log（user/assistant，emotion 占位 neutral）
  │                                     失败会入 capture_turn_retry 慢队列，重试超限落 DLQ
  └─ pending perception 确认 / profile 条件快照等本地操作
  │
  │  【发送后慢段】fanout 完成后 asyncio.create_task 调度：
  ├─ detect_emotion()                  asyncio.wait_for(timeout=8s)，超时降级 neutral
  ├─ global_lock("mood_state") 内：
  │   ├─ mood_state.update(emotion)    更新情绪状态
  │   └─ yandere / hidden-state 信号   使用本轮真实 emotion
  └─ avatar / profile / slow_queue      单 worker 异步执行总结、反思、画像更新等
  │
  │  【慢队列】post_process_slow 入队后单 worker 异步执行：
  ├─ summarize_to_midterm              LLM 压缩单轮到 mid_term，写血缘字段；emotion 显著时触发 reflect_to_episodic(eager)
  ├─ reflect_to_episodic               mid_term 列表 → episodic，更新 fixation_state；达阈值触发 consolidate_to_identity
  ├─ consolidate_to_identity           unconsolidated episodic + old identity + profile → user_identity.yaml
  ├─ consistency_check                 人设一致性检测，问题存 author_note_extra
  └─ user_profile_update               每 N 轮触发，入队时已判断条件
  │  （旧 handler mid_term_append / episodic_compress 保留供 DLQ 残留任务重试）
  │  （consolidate_to_growth 已在 R8-E1 移除：DEAD 名字残留，从未注册 handler）
  │
  │  【side effects】保持 asyncio.create_task，不入慢队列：
  ├─ TTS / 表情包（随机互斥；表情包 QQ 图片 + desktop/mobile sticker payload）
  │
  │  slow_queue worker 特性：
  │  - 单 worker（per-uid 顺序由 handler 内 uid_lock 保护）
  │  - 失败退避重试（0.5s×1, 1.0s×2，共 3 次）
  │  - 超限写入 DLQ：data/dead_letter_queue/{ms_ts}_{task_type}.json
  │  - 不持久化队列（进程退出丢失，有意设计）
```

## MCP 在架构中的唯一定位

MCP（Model Context Protocol）是 **Tool subsystem 的外部工具传输协议**，不是
desktop/mobile ↔ backend 的客户端协议，也不是 Interaction/Event kind。当前真实调用链只从
owner 的私聊回合、且在 Path C 有效时进入：

```
owner private turn
  → Path C tool loop
  → tool_dispatcher
  → local tool 或 MCP dynamic tool
  → MCP client/session
  → external MCP server / hardware_gateway
  → bounded ToolResult
  → 当前轮 tool-result 边界
       ├─ 普通单次路径：prompt layer 10_tool_result
       └─ Path C：当前 loop 内对齐的 bounded role=tool 消息，随后才收尾生成
```

这里的 `hardware_gateway` 只是外部 MCP Server 的一种实现，不是 PresenceKit 核心内部
模块。MCP server 的工具描述和返回结果均视为不可信外部输入；它们只能经现有工具暴露面、
`tool_dispatcher.execute_structured()` 的 origin/角色/白名单/安全闸门，以及当前轮 ToolResult 边界，
不能改变系统权限。

- scheduler、stimulus/trigger、Dream 和 Stage 不得隐式升级为 MCP tool call；它们继续沿各自
  的入口与生命周期运行。MCP 调用不是新的 stimulus，也不经过 `perceive_event`。
- MCP 不拥有直接 memory writer 权限。MCP 结果不会独立写 `short_term`、`event_log` 或长期
  记忆；普通回合的最终 assistant turn 仍由既有 turn sink 处理，工具动作的最小审计痕迹仍由
  既有 dispatcher 收口。
- MCP 不是 EventBus、EventEnvelope 或 kind dispatcher 的替代物；这些统一事件设计仍属于
  deferred scope。

---

## 探针（probe）机制

探针在 pipeline 之前处理，目的是先判断本轮是否需要调用工具：

- 使用极简 system prompt（`get_probe_prompt()`），不带角色卡
- QQ 入口有关键词快速路径；`/desktop/chat` 与 `/mobile/chat` owner-chat 入口走 LLM probe，不走关键词快速路径
- 只判断 info + desktop 两类工具
- memory 类工具不走探针，靠 LLM 在正式对话中自主调用
- QQ 入口（`main.py`）和 owner HTTP 入口（`admin/routers/chat.py`）共用同一个 `get_probe_prompt()` 函数
- **trusted_user_text**：探针只消费 media merge 之前的原始用户输入。QQ 在 media merge（`main.py` line ~276）前捕获 `_trusted_user_text`；desktop/mobile media 端点在 `run_owner_chat_turn` 调用前捕获。media 抽取文本只进 `build_prompt`，不进 probe。
- **execute_structured() origin 闸门**：生产路径只走 `tool_dispatcher.execute_structured()`。`origin` 不在白名单则 fail-closed：`status=tool_failed`（result / confirmation_request 均为 None）+ warning。

---

## 数据流向总图

```
QQ/客户端/手机/HTTP上传
    ↓ media_processor（文件落盘 + 解析）→ 拼 user message
    ↓
用户消息
    ↓ notify_owner_turn(uid) → scheduler_user_state.trigger_state + logs/trigger_state.jsonl
    ↓ get_tags()
    ↓ 探针 + 工具执行
    ↓
fetch_context ──读──→ data/ 目录各文件
    ↓
build_prompt ──组装──→ messages[0..12层]
    │           realtime_state 只以短 TTL 粗摘要进入 `3.9_screen_awareness`
    │           （应用/活动类别 + 模糊编辑状态，不注入窗口标题或屏幕原文）
    │           tool_result 裸输出经 ToolResult.safe_summary 框定后进 layer 10
    ↓
run_llm ──→ reply
    ↓
post_process ──写──→ data/ 目录各文件
```

Memory Event model edge proposals are deliberately outside this flow. The
scheduler reads only a bounded ledger window and writes unreviewed candidates
back to the scoped ledger; proposals never enter prompt construction, recall,
event_log, short-term, episodic, or identity writers.

---

Memory Event shadow recall is likewise outside the model path. During
`fetch_context()` a greylisted scope may run a bounded reality-ledger query in
parallel with legacy recall. It appends only event IDs and aggregate metrics to
`recall_trace`; timeout, error, or rollback leaves the legacy prompt path
unchanged. Explicit owner Path C Memory Event tools remain the only route that
can return event evidence to a model turn. Legacy Memory Event migration is
also offline-only: it scans old Markdown and
memory stores without an LLM, requires a verified private-state backup before
any bounded import batch, and never blocks chat. Event forget requests become
ledger tombstones; payloads are removed from recall while stable evidence IDs
and relation edges remain available for lineage inspection.

## 数据目录结构

> 所有 `data/` 运行时路径必须通过 `core/sandbox.get_paths()` 获取，不得硬编码；
> `userdata/` 私有 authored 资产则通过 `DataPaths` / `AssetRegistry` 访问。
>
> **V9 布局稳定**：`_LAYOUT_CHARACTER_INNER = "v1"`（S5）、`_LAYOUT_REALITY = "v1"`（S6）已全面生效。
> 所有累积型路径的 `for_read` 降级读分支已删除；旧型目录已归档至 `data/_legacy_retired/`。
> event_log union 读（30 天窗口内 legacy 天文件）保留至窗口过期后再清理。

`bundled/`、`userdata/` 与 `data/` 分离：`bundled/` 是发行内置、只读且可随更新替换的公共
卡片、seed、模板与 examples；`userdata/` 是 Git 忽略的用户私有 authored 资产根（贴纸、角色卡、
角色 reality/dream 素材）；`data/` 才是受 `get_paths()` 管理、会在测试模式偏移的运行时状态
沙盒。读取按 userdata → bundled → legacy compatibility，所有业务 writer 仍只写 userdata/data。
完整分类见 `docs/data-taxonomy.md`。

```
data/
├── runtime/
│   ├── memory/{char_id}/{uid}/   # history、event_log、mid_term、episodic、identity 等现实记忆
│   ├── characters/{char_id}/     # inner、garden、pet、works、notes 等角色状态
│   ├── dreams/{char_id}/         # tmp/archive/summaries/impressions/state/settings 等个人 Dream 状态
│   ├── dreams/_stage/{group_id}/ # 群聊 Dream Stage；不进入 reality loader
│   ├── groups/{group_id}/        # reality Stage 的 meta/transcript/arbiter trace
│   ├── agent_runtime/reality/    # Task Manager / work sessions / workspace versions
│   ├── spend/ledger.jsonl        # Brief 57 支出账本；意向单读面已删，历史 jsonl 文件不由代码清掉
│   ├── companion/                # owner-turn receipts
│   ├── perception/visual_trace.jsonl
│   ├── observability/api_calls-YYYY-MM-DD.jsonl
│   ├── {channel,mobile}_queue.json、agent_actions.json
│   └── scheduler_user_state.json
├── scheduler_cooldowns.json
├── group_context/、inbox/、cache/image_cache/、diary_fallback/
├── logs/、debug/                 # forensic / 排障材料，不是 prompt 输入
└── test_sandbox/{session_id}/    # 仅测试模式；整棵运行时路径在这里隔离
```

> **User Hidden State（Brief 88：现实侧接线完成）**：
> `core/memory/user_hidden_state.py` — schema（UserHiddenState：sensitivity / touch_need / embodied_ease / body_memory）+ 所有 primitive helpers + 全部更新函数已实现（`apply_time_decay` / `accrue_touch_deficit` / `nudge_embodied_ease` / `reinforce_body_memory` / `consolidate_baselines`）；所有接受 `source: UpdateSource` 的函数含 TypeError 守卫。
> `core/memory/user_hidden_state_integrator.py` — Phase 3 更新：`integrate_event` / `integrate_impression` 新增 TypeError 类型守卫；`integrate_event_and_save` / `integrate_impression_and_save` 新增 uid 类型守卫；新增 `integrate_body_cue` / `integrate_body_cue_and_save`（长期层 body_memory，WriteEnvelope gated）；`_assert_not_long_term` 内部断言防止 integrator 意外写长期层。
> `core/memory/user_hidden_state_store.py` — `load_hidden_state` / `save_hidden_state` / `load_dream_snapshot`（只读 bucket 快照，不暴露 float）。
> `core/scheduler/triggers/hidden_state_decay.py`（Phase 3 新增）— `_check_hidden_state_decay`（12h）+ `_check_hidden_state_consolidate`（7d），使用 `stamp_trigger()`，不发言，不入 pipeline，已接入 `loop.py` 的 `asyncio.gather`。
>
> **Phase 4（Dream 只读接入）**：`core/dream/dream_context.build_snapshot()` 在入梦时调用 `load_dream_snapshot()` 并将结果冻结进 `context_snapshot["user_hidden_state_snapshot"]`。`core/dream/dream_prompt.build_dream_prompt()` 在 D4–D5 之间插入 D4.5 层，tag-gated（`body_intimate` / `physical_closeness`）。注入内容只含 bucket label（sensitivity / touch_appetite / embodied_ease / memory_cues），无 float / uid / timestamp / weight。Fail-closed：任何异常 → 不注入，不阻断 Dream。Dream 无写路径：`DREAM_DIRECT_WRITABLE = frozenset()`。
>
> **Phase 6（Dream Exit Afterglow Wiring）**：`core/dream/dream_pipeline._generate_summary_bg()` 在 `generate_summary()` 后调用 `wire_afterglow_from_summary()`（`core/dream/dream_exit_afterglow.py`）。从 summary record 推导 tone（hard_exit/hurt_reluctance → stress；gentle_residue+high_weight → comfort；gentle_residue → calm；fallback → neutral），构建 `AfterglowResidueInput`，经 `save_afterglow_residue()` 落盘，再经 `integrate_afterglow_and_save(stamp_dream_afterglow())` 写入 hidden_state。Fail-closed：任何步骤失败 → warning，不阻断 Dream exit。Dream 仍无直接写权限（`DREAM_DIRECT_WRITABLE = frozenset()`）。
>
> **Brief 88（现实侧全量信号映射，H1 接线完成）**：`RealityEventType` 扩至 5 类
> （新增 `BODY_TOPIC` / `AFFECTION_EXPRESSED`）。对话侧判定落在新模块
> `core/memory/user_hidden_state_reality_signals.py::process_reality_turn()`，挂
> `pipeline.post_process_slow` detect_emotion 完成之后，判定全用现成数据（tags /
> emotion / 现实轮 gap 快照 / 常量词表），零 LLM，fail-open；trigger 轮零参与。
> 中期层（sensitivity.current / touch_need.deficit）固定用本模块自建
> `stamp_user_chat()`，不看调用方 envelope；长期层（body_memory，经
> `integrate_body_cue_and_save`）则遵守调用方 envelope.can_write_memory。
> `NO_INTERACTION` 挂现有 `hidden_state_decay` 12h tick（未新建 trigger），
> presence gap ≥24h 且逻辑日未记账时 accrue，去重 stamp 落盘于
> `hidden_state_no_interaction_stamp.json`，重启不重复。`hidden_state_debug` 观测
> 端点新增 `trigger_counts`。详见 `docs/memory.md` 八·现实侧信号映射。
>
> 长期层写权限（全部 `WriteEnvelope.can_write_memory=True` 必须）：
>   `body_memory` ← `integrate_body_cue*`（Reality-side，stamp_trigger / stamp_user_chat）
>   `embodied_ease` ← `nudge_embodied_ease` / `integrate_afterglow_and_save`（调度器 or Dream exit afterglow）
>   `sensitivity.baseline` / `touch_need.baseline` ← `apply_time_decay` + `consolidate_baselines`（调度器）
>
> 安全不变量：Dream 不得直接写任何字段（DREAM_DIRECT_WRITABLE = frozenset()）；snapshot 只输出 bucket string，不暴露 float 原始值；长期层写入不经过 integrate_event / integrate_impression；afterglow 回流只影响 sensitivity.current / embodied_ease，不写 baseline / touch_need / body_memory。
> 持久化路径：`user_memory_root(uid)/hidden_state.json`，原子写入。设计文档：`docs/user_hidden_state_phase3.md`。

> **authored 静态配置**（不走 `data/` 沙盒）：主路径为
> `userdata/characters/authored/{char_id}/`（activity pool、traits、author notes 等）；贴纸、角色卡、
> reality/dream 素材同在 `userdata/`。旧 `content/characters/`、`characters/`、`assets/stickers/`
> 只保留给旧安装的只读 fallback，详细边界见 `docs/data-taxonomy.md`。

---

## 全局 Pipeline 实例管理

`core/pipeline_registry.py` 持有唯一的 Pipeline 实例，供管理面板、后处理纠偏、调度器等跨模块获取。

```python
# 注册（main.py 初始化时）
pipeline_registry.register(pipeline)

# 跨模块获取（admin/routers/chat.py、consistency_check handler、scheduler._pipeline_send）
pipeline = pipeline_registry.get()
```

> `scheduler.set_pipeline()` 兼容壳已删除（Brief 35）；`main.py` 直接调用
> `pipeline_registry.register()`。调度器不维护自己的 `_pipeline` 副本；
> `_pipeline_send()` 执行时从 registry 读取当前实例。

---

## token 估算与裁剪

估算用字符数（`len(content)`，不是真实 token 数）：

| 阈值 | 行为 |
|---|---|
| > 15000 字符 | 打 warning 日志 |
| > 20000 字符 | 触发强制裁剪 |

裁剪机制（R4-B 动态裁剪，权威见 `docs/prompt-layers.md` §token 裁剪）：
收集所有带 `_drop_priority` 的消息，按 priority **升序**依次丢弃（数字越小越先丢），直到降回阈值内。
不带 `_drop_priority` 的层（核心约束、`11_author_note` 等）永不被自动裁剪，
豁免名单由 `tests/test_r4c_prompt_layer_contract.py` 门禁。


---

## 目录职责：bundled/ vs userdata/ vs data/

| 目录 | 用途 | 示例 |
|---|---|---|
| `bundled/` | **发行内置公共资产**，只读、可替换 | 默认角色、Reality/Dream seed、模板、示例 |
| `userdata/` | **用户私有 authored 资产**，按需创建、更新保护 | 角色卡、Reality/Dream override、贴纸 |
| `data/` | **运行时数据**，由程序运行中写入 | 聊天历史、情绪状态、计划队列等 |

### 现实 Chat Authored Assets

现实 Chat 的两个 authored prompt assets 写入 `userdata/characters/reality/`，**不再从 `data/` 读取**；
缺失时由 `bundled/seeds/reality/` 播种，旧 `characters/reality/` 仅兼容读取：

```
userdata/characters/reality/
├── lorebook.yaml            ← 现实世界书（admin 面板读写）
└── jailbreak_entries.json   ← 破限预设条目（admin 面板读写）
```

路径由 `core/data_paths.py` 的 `DataPaths.lorebook()` / `DataPaths.jailbreak_entries()` 方法决定。  
文件不存在时：记录 warning，返回空列表，**不 fallback 到 `data/`**，也不从 `data/` 读取旧文件。

旧路径 `data/lorebook.yaml` 和 `data/jailbreak_entries.json` 已废弃，仅作迁移来源保留，运行时不读取。

---

## Hook：文档同步提醒

`.claude/hooks/` 下两个 hook 在 Claude Code 编辑代码后自动检查文档是否需要同步更新。

### 工作机制
PostToolUse（每次 Edit/Write/MultiEdit）
└─ track_edits.py 把改动路径追加到 .claude/.cache/edits_{session_id}.json
Stop（Claude 准备结束响应时）
└─ remind_docs.py 读 cache，比对规则
├─ 改了代码但相关文档未动 → decision: "block" + reason 拦下
└─ 文档已同步或只改了文档 → 清空 cache，放行

`stop_hook_active=true` 时直接放行，保证最多卡一轮，Claude 要么补文档、要么明说"无需更新：理由"再停。

### 规则映射（remind_docs.py 顶部维护）

**全局兜底**：改 `core/`、`main.py`、`admin/` 下任何代码 → 提示 `ARCHITECTURE.md` + `AGENTS.md`

**专项追加**：

| 改动路径关键词 | 追加提示文档 |
|---|---|
| `core/memory/`、`core/safe_write.py`、`core/integrity_check.py`、`core/llm_output_validator.py`、`tools/extract_observations.py` | `docs/memory.md` |
| `core/prompt_builder.py`、`core/tag_rules.py`、`core/mood_text.py`、`core/author_note_rotator.py`、`core/lore_engine.py`、`characters/` | `docs/prompt-layers.md` |
| `core/tool_dispatcher.py`、`core/tools/` | `docs/tools.md` |
| `core/scheduler/` | `docs/scheduler.md` |

`docs/known-issues.md` 不进自动规则，bug 修复后手动记账。

### 不会触发的情况

- 改 `tests/`、`README.md`、配置文件等非映射路径
- 同一轮里代码和对应文档都改了（pending 为空）
- 上一轮已被 hook block 过（防死循环）

### 配置位置

| 文件 | 作用 |
|---|---|
| `.claude/settings.json` | hooks 节点声明 PostToolUse + Stop 两个钩子 |
| `.claude/hooks/track_edits.py` | 记录本轮编辑过的文件 |
| `.claude/hooks/remind_docs.py` | Stop 前检查 + 阻塞，规则映射也在此 |
| `.claude/.cache/edits_*.json` | 每个 session 的编辑记录（已 gitignore） |

### 新增规则

`remind_docs.py` 顶部 `SPECIFIC_RULES` 追加一条：
```python
(["路径关键词1", "路径关键词2"], "docs/新文档.md", "说明"),
```
其他都不动。


## Brief 171: Remote owner turns, diary mirror, and capability gates

The versioned `POST /v1/owner/turns` endpoint is an owner-input adapter over
the existing `pipeline_registry` instance, per-owner conversation gate, frozen
Reality scope, and turn sink. Its durable receipt stores only caller label,
request hash, status, canonical turn ID, timestamps, and a safe error code.
The receipt is an idempotency boundary, not a second conversation or memory
store; a completed receipt is projected from retained canonical history.

`deployment.mode` is process configuration (`local` by default or
`remote_server`) and cannot be selected by a request, prompt, or model. In
remote mode server-local shutdown/sleep, desktop signal-file fallback, server
filesystem browsing, and legacy exit signaling fail closed. Desktop actions
remain available only through `/ws/desktop` with an acknowledgement.

Owner diary sync is a separate integration boundary. It accepts only bounded
`YYYY-MM-DD.md` entries, generation, hashes/revisions, and tombstones into the
private runtime mirror under `DataPaths`; remote diary readers use that mirror
and never fall back to a client Obsidian path. The inner-character diary API is
separate and is not reused for this mirror.

## 功能控制面

P0–P2 设置入口、权限边界与降级路径以 docs/feature-control-surface.md 为准；不得把 config 字段存在误写成已有可视化控制。


## 按需截图三端接入（2026-09-12，partial）

详见仓库 `docs/screen-observation-2026-09-12.md`。后端 `observe_user_screen` 通过独立 HTTP poll/result 请求活跃电脑或手机的新截图，UUID/凭据绑定、20 秒 TTL、30 秒设备新鲜度和本地授权均参与门控；图像只在内存中处理。

管理面提供全局开关、effective state 与 `/perception/screen/status` 无正文观测；电脑视觉观察页、手机系统配置页各有独立本地授权，默认关闭。全局开启时自主工具继承启用，显式工具禁用优先；角色消息继续走原通知/免打扰链路。桌面 IPC 新增可选 onDemandEnabled；手机使用专用 screen_observation 通道与无障碍 worker，不改 mobile poll/ack/relay。

实现及构建/定向测试通过，真实双设备、锁屏、OEM 后台及 VLM/消息联合验收保持 open。管理面既有国际化测试 3 项失败保持 open，详见施工记录，不能将静态检查作为真实设备验收。
