# ARCHITECTURE.md — 系统架构总览

---

## 系统全貌

角色通过 QQ、桌宠和调度器三个入口进入同一条 pipeline，输出再交给通道层发送：

```
QQ 消息 → main.py → message_queue
桌宠消息 → admin/routers/chat.py（POST /desktop/chat）
手机消息 → admin/routers/mobile.py（POST /mobile/chat）
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
      LLM（DeepSeek）
         ↓
      输出层（消息信封可选携带 `char_id` 发言人字段）
         ├─ QQ 主入口：_qq_reality_reply_adapter 可见发送 + turn_sink 记忆写入
         └─ desktop/mobile/scheduler/sensor：core.turn_sink 写入 + channels.registry 广播活跃通道
```

桌面 `message_segments` 的 `say` 段可选携带句级表演 spec（`perform`，`core/perform_mapper.py`，
fail-open），见 `docs/perform-mapping.md`。
桌面端当前正式协议为 v0.1（legacy 冻结版）：用户输入正式走 `POST /desktop/chat`，服务端通过 `/ws/desktop` 下发回复与动作；本仓入口见 `docs/desktop-client-protocol.md`，协议正文唯一权威位于 PresenceKit-desktop 的 `docs/protocol-v0.md`。v1 未排期，双方均未实现。

通道细节见 `docs/channels.md`。手机端当前通过 mobile 轮询通道接收主动消息，不占用桌宠 WebSocket。花园这类不进入对话 pipeline 的伴生状态，见 `docs/garden.md`。

ESP32 具身硬件通过 `/ws/device` 接入，帧格式与桌宠端 `/ws/desktop` 一致（见 `docs/channels.md`）；
固件本身（板型、代码结构、鉴权配置）见 `docs/presence-device-firmware.md`
（`firmware/presence-device/`，不要与已废弃的 `hardware/_achieve_Emerald-hello` 测试项目混淆）。
Dream Session 后续必须走独立 pipeline，不进入当前现实对话 pipeline，也不走现有 `post_process`。

Scheduler proposal 显式携带 `char_id` 时，`execute_prompt → _pipeline_send` 会冻结该角色的
Reality scope；若它不是当前 active character，Pipeline 按该 scope 加载对应角色卡与世界书，
避免“做梦角色”和实际发言角色错位。

多角色群聊使用独立 `core/stage/` Session 内核：Stage 持有 roster、共享 transcript 和纯规则回合仲裁，
一整轮 Phase A + Phase B 共用一次 owner conversation lock。Reality Stage 通过 per-character
只读生成视图显式绑定角色卡与 memory scope，群 transcript 独立注入 prompt；回合后只把摘要按
`group:{group_id}` 来源送入各角色 fixation 链。Dream Stage 当前 fail-closed。详见 `docs/stage.md`。

Intiface / Buttplug 硬件是 reality-side actuator：只有 owner 私聊中的真实 turn 工具调用可触发，
不进入 scheduler、trigger 或 Dream pipeline。`core/hardware/buttplug_client.py` 通过
`aiohttp` 的无代理 WebSocket 连接本机 Intiface Central，并维护进程内设备发现状态。

手机端用户输入走 `POST /mobile/chat`，桌宠输入走 `POST /desktop/chat`。这两个 owner 入口共享
`core/conversation_gate.py` 的 per-user conversation lock，保证同一用户多端输入按顺序完成
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
    ▼ 步骤3  run_llm()
  调 llm_client.chat(messages)，含重试
    │
    ▼ 步骤4  post_process()（owner 入口与调度器主动消息通过 turn_sink 等待关键写入）
  │
  │  【关键路径】uid_lock(uid) 内，按顺序同步完成：
  ├─ detect_emotion()                  asyncio.wait_for(timeout=8s)，超时降级 neutral
  ├─ global_lock("mood_state") 内：
  │   ├─ mood_state.update(emotion)    更新情绪状态
  │   └─ yandere 触发检测              关键词 + 关系阈值
  └─ capture_turn()                    写 history + event_log（user/assistant，含 turn_id）
                                      失败会入 capture_turn_retry 慢队列，重试超限落 DLQ
  │
  │  【慢队列】uid_lock 释放后入 slow_queue，单 worker 异步执行：
  ├─ summarize_to_midterm              LLM 压缩单轮到 mid_term，写血缘字段；emotion 显著时触发 reflect_to_episodic(eager)
  ├─ reflect_to_episodic               mid_term 列表 → episodic，更新 fixation_state；达阈值触发 consolidate_to_identity
  ├─ consolidate_to_identity           unconsolidated episodic + old identity + profile → user_identity.yaml
  ├─ consistency_check                 人设一致性检测，问题存 author_note_extra
  └─ user_profile_update               每 N 轮触发，入队时已判断条件
  │  （旧 handler mid_term_append / episodic_compress 保留供 DLQ 残留任务重试）
  │  （consolidate_to_growth 已在 R8-E1 移除：DEAD 名字残留，从未注册 handler）
  │
  │  【side effects】保持 asyncio.create_task，不入慢队列：
  ├─ TTS / 表情包（随机互斥）
  └─ _parse_and_execute_intent()       桌面操作意图解析
  │
  │  slow_queue worker 特性：
  │  - 单 worker（per-uid 顺序由 handler 内 uid_lock 保护）
  │  - 失败退避重试（0.5s×1, 1.0s×2，共 3 次）
  │  - 超限写入 DLQ：data/dead_letter_queue/{ms_ts}_{task_type}.json
  │  - 不持久化队列（进程退出丢失，有意设计）
```

---

## 探针（probe）机制

探针在 pipeline 之前处理，目的是先判断本轮是否需要调用工具：

- 使用极简 system prompt（`get_probe_prompt()`），不带角色卡
- QQ 入口有关键词快速路径；`/desktop/chat` 和 `/mobile/chat` 走 LLM probe，不走关键词快速路径
- 只判断 info + desktop 两类工具
- memory 类工具不走探针，靠 LLM 在正式对话中自主调用
- QQ 入口（`main.py`）和 owner HTTP 入口（`admin/routers/chat.py`）共用同一个 `get_probe_prompt()` 函数
- **trusted_user_text**：探针只消费 media merge 之前的原始用户输入。QQ 在 media merge（`main.py` line ~276）前捕获 `_trusted_user_text`；desktop/mobile media 端点在 `run_owner_chat_turn` 调用前捕获。media 抽取文本只进 `build_prompt`，不进 probe。
- **execute() origin 闸门**：`tool_dispatcher.execute()` 要求 `origin` 参数在白名单 `{"user_live", "assistant_intent"}` 内，否则 fail-closed 返回 `(None, None)` + warning。

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

---

## 数据目录结构

> 所有路径必须通过 `core/sandbox.get_paths()` 获取，不得硬编码。
>
> **V9 布局稳定**：`_LAYOUT_CHARACTER_INNER = "v1"`（S5）、`_LAYOUT_REALITY = "v1"`（S6）已全面生效。
> 所有累积型路径的 `for_read` 降级读分支已删除；旧型目录已归档至 `data/_legacy_retired/`。
> event_log union 读（30 天窗口内 legacy 天文件）保留至窗口过期后再清理。

```
data/
├── characters/{char_id}/         ← S5 新增：角色维度根目录（当前 char_id = yexuan）
│   ├── inner/
│   │   ├── mood_state.json       角色当前情绪状态
│   │   ├── activity_state.json   当前活动状态（activity_manager 管理）
│   │   ├── activity_snapshot.json  桌宠推来的活动快照（TTL 5 分钟，runtime）
│   │   ├── observations.jsonl    角色行为观察日志（style_hint 来源，prompt 层11）
│   │   ├── author_note_state.json  author_note_rotator 轮转状态
│   │   ├── trait_state.json      性格特质命中统计（trait_tracker 写，author_note_rotator 读）
│   │   ├── presence.json         每个用户上次说话时间（presence.py 管理）
│   │   └── diary/                角色日记（每日 23:00 调度器触发）
│   ├── garden/
│   │   ├── plants.json           五个情绪花槽状态（stage/growth/last_watered）
│   │   └── storage.json          收获/花瓶/历史记录（harvest/vase/history）
│   ├── pet.json                  角色宠物状态（core/pet.py 管理）
│   └── character_growth/          历史遗留数据；Brief 35 已移除读写模块
│       ├── 角色_{uid}.md         不再被读取
│       ├── 角色_{uid}.felt.md    不再被读取
│       └── 角色_{uid}.fingerprint.txt  压缩版指纹
├── memory/{char_id}/{uid}/       ← S6 新增：per-user 记忆根目录（当前 char_id = yexuan）
│   ├── history.json              短期对话历史（磁盘轮数可配；prompt 近场保留 + 远场加权择优）
│   ├── mid_term.json             中期对话摘要（12小时过期，最多20条，三时间桶）
│   ├── episodic.json             情景记忆（最多 200 条，含 strength 衰减）
│   ├── memory_index.json         标签倒排索引（episodic 用）
│   ├── profile.json              用户画像
│   ├── identity.yaml             用户稳定行为模式（prompt 层 6a 主入口）
│   ├── identity.yaml.bak         写前备份（save() 自动维护）
│   ├── diary_context.txt         用户日记上下文
│   ├── reminders.json            用户备忘录列表（调度器检查到期即发）
│   ├── fixation_state.json       固化 pipeline 状态（重启不丢）
│   ├── dream_seed.json           梦境预构短期种子（12h TTL；入梦一次性消费）
│   ├── memory_digest.md          episodic 淘汰归档（Brief 46 §1，已于 Brief 80 起退役：只读存量，不再写入）
│   ├── storyline.json            叙事弧 append-only 存储（Brief 80，弧线+节点，见 docs/memory.md §四点六）
│   ├── storyline_inbox.json      episodic 淘汰批次待聚合暂存（storyline_weekly 消费后清空，滚动上限200）
│   ├── storyline_archive.md      storyline 总弧线数超限时淘汰的 closed arc 归档
│   └── event_log/{date}.md       按天分割的对话流水账（search 最近 30 天）
├── _legacy_retired/{timestamp}/      ← V9 归档：S5/S6 旧型目录（含 event_log 旧路径）
│                                        event_log/{uid}/ 在 30 天窗口内仍被 union 读取
├── dreams/
│   ├── tmp/                      Dream Session 临时文件（dream_only；永不作为记忆源）
│   ├── archive/                  Dream Session 归档（archive 永不进 memory loader）
│   ├── summaries/                Dream Session 摘要（永不进 memory loader）
│   └── state/{uid}/dream_state.json  per-uid Dream Session 状态
├── group_context/{gid}.json      群聊最近动态（prompt 层 4 注入）
├── pending_perception/           桌面动作失败感知（时间戳命名，两阶段提交）
├── runtime/perception/visual_trace.jsonl  本地 VLM shadow 观察（30 天；不含原图，不入记忆/prompt）
├── runtime/spend/ledger.jsonl  花钱动作台账（只提议/提醒；v1 不自动扣款）
├── inbox/                        三端统一的文件落盘目录（QQ/客户端/手机/HTTP 上传都进这里）
├── image_cache/                  图片 sha256 索引（描述文本 + 元数据）
├── agent_actions.json            桌面动作队列（桌宠端轮询）
├── channel_queue.json            调度器广播队列（asyncio.Lock 保护）
├── mobile_queue.json             手机主动消息轮询队列（MobileChannel 写，/mobile/poll 读）
├── scheduler_cooldowns.json       调度器冷却时间戳（triggers map；durability=canonical）
├── scheduler_user_state.json     调度器用户级运行态（trigger_state / last_diary_share / followed_topics；durability=runtime）
├── logs/
│   ├── fixation.jsonl            固化 pipeline 每 job 追加一行（ts/job/uid/status/duration_ms）
│   ├── trigger_state.jsonl       触发状态机每次状态切换追加一行
│   ├── gating_shadow.jsonl       gating 并行观测期每 tick 的候选与 would_pick
│   └── execute_dryrun.jsonl      proposer shadow 执行观测（would_send_prompt / would_mark）和 live blocked 观测
├── dead_letter_queue/            慢任务 DLQ（handler 3次失败后落盘，含 task/error/failed_at）
├── debug/
│   └── llm_output/              LLM异常输出存档（带时间戳，7天自动清理）
└── (锁池)                        core/memory/locks.py 管理，运行时内存对象，不落盘
```

> **User Hidden State（Phase 6 完成）**：
> `core/memory/user_hidden_state.py` — schema（UserHiddenState：sensitivity / touch_need / embodied_ease / body_memory）+ 所有 primitive helpers + 全部更新函数已实现（`apply_time_decay` / `accrue_touch_deficit` / `nudge_embodied_ease` / `reinforce_body_memory` / `consolidate_baselines`）；所有接受 `source: UpdateSource` 的函数含 TypeError 守卫。
> `core/memory/user_hidden_state_integrator.py` — Phase 3 更新：`integrate_event` / `integrate_impression` 新增 TypeError 类型守卫；`integrate_event_and_save` / `integrate_impression_and_save` 新增 uid 类型守卫；新增 `integrate_body_cue` / `integrate_body_cue_and_save`（长期层 body_memory，WriteEnvelope gated）；`_assert_not_long_term` 内部断言防止 integrator 意外写长期层。
> `core/memory/user_hidden_state_store.py` — `load_hidden_state` / `save_hidden_state` / `load_dream_snapshot`（只读 bucket 快照，不暴露 float）。
> `core/scheduler/triggers/hidden_state_decay.py`（Phase 3 新增）— `_check_hidden_state_decay`（12h）+ `_check_hidden_state_consolidate`（7d），使用 `stamp_trigger()`，不发言，不入 pipeline，已接入 `loop.py` 的 `asyncio.gather`。
>
> **Phase 4（Dream 只读接入）**：`core/dream/dream_context.build_snapshot()` 在入梦时调用 `load_dream_snapshot()` 并将结果冻结进 `context_snapshot["user_hidden_state_snapshot"]`。`core/dream/dream_prompt.build_dream_prompt()` 在 D4–D5 之间插入 D4.5 层，tag-gated（`body_intimate` / `physical_closeness`）。注入内容只含 bucket label（sensitivity / touch_appetite / embodied_ease / memory_cues），无 float / uid / timestamp / weight。Fail-closed：任何异常 → 不注入，不阻断 Dream。Dream 无写路径：`DREAM_DIRECT_WRITABLE = frozenset()`。
>
> **Phase 6（Dream Exit Afterglow Wiring）**：`core/dream/dream_pipeline._generate_summary_bg()` 在 `generate_summary()` 后调用 `wire_afterglow_from_summary()`（`core/dream/dream_exit_afterglow.py`）。从 summary record 推导 tone（hard_exit/hurt_reluctance → stress；gentle_residue+high_weight → comfort；gentle_residue → calm；fallback → neutral），构建 `AfterglowResidueInput`，经 `save_afterglow_residue()` 落盘，再经 `integrate_afterglow_and_save(stamp_dream_afterglow())` 写入 hidden_state。Fail-closed：任何步骤失败 → warning，不阻断 Dream exit。Dream 仍无直接写权限（`DREAM_DIRECT_WRITABLE = frozenset()`）。
>
> 长期层写权限（全部 `WriteEnvelope.can_write_memory=True` 必须）：
>   `body_memory` ← `integrate_body_cue*`（Reality-side，stamp_trigger / stamp_user_chat）
>   `embodied_ease` ← `nudge_embodied_ease` / `integrate_afterglow_and_save`（调度器 or Dream exit afterglow）
>   `sensitivity.baseline` / `touch_need.baseline` ← `apply_time_decay` + `consolidate_baselines`（调度器）
>
> 安全不变量：Dream 不得直接写任何字段（DREAM_DIRECT_WRITABLE = frozenset()）；snapshot 只输出 bucket string，不暴露 float 原始值；长期层写入不经过 integrate_event / integrate_impression；afterglow 回流只影响 sensitivity.current / embodied_ease，不写 baseline / touch_need / body_memory。
> 持久化路径：`user_memory_root(uid)/hidden_state.json`，原子写入。设计文档：`docs/user_hidden_state_phase3.md`。

> **authored 静态配置**（不走沙盒）：
> - `content/characters/yexuan/activity_pool.yaml`
> - `content/characters/yexuan/traits.yaml`
> - `characters/yexuan_author_notes.json`

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

裁剪顺序（依次删，按质量从低到高）：
`6b_event_search` → `mid_term` → `6d_diary` → `6e_inner_diary` → `6c_episodic` → `5.5_lore`

`6a_user_identity`、`5_profile`、`9_history`、`11_author_note` 不在裁剪表里。


---

## 目录职责：data/ vs characters/reality/

| 目录 | 用途 | 示例 |
|---|---|---|
| `data/` | **运行时数据**，由程序运行中写入，不应手工编辑 | 聊天历史、情绪状态、计划队列等 |
| `characters/reality/` | **现实 Chat authored prompt assets**，手工维护，可版本审计 | `lorebook.yaml`、`jailbreak_entries.json` |
| `characters/dream_worlds/`、`characters/dream_presets/` | Dream 世界包 authored assets，独立体系 | Dream lorebook、presets |

### 现实 Chat Authored Assets

现实 Chat 的两个 authored prompt assets 存放于 `characters/reality/`，**不再从 `data/` 读取**：

```
characters/reality/
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


## 功能控制面

P0–P2 设置入口、权限边界与降级路径以 docs/feature-control-surface.md 为准；不得把 config 字段存在误写成已有可视化控制。
