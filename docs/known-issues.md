# docs/known-issues.md — 已知问题与技术债

> 最近核对：2026-06-07
> 核对范围：perceive_event P0+P1 收口。已落地事项按当前实现同步；未列出的条目保持原审计结论。
>
> 状态标签：
> - `now-safe-to-fix`：可按小修推进。
> - `refactor-phase`：适合重构期统一处理。
> - `boundary-doc-needed`：先补设计或行为边界，再决定是否改代码。
> - `observe`：已有缓解或属于兼容层，先观察。

---

## 当前仍存在

### B11：部分现实对话入口仍可能读取旧上下文

**状态**：`now-safe-to-fix`

**位置**：`main.py` → `handle_message()`；`admin/routers/chat.py` → `/chat`

**已收口路径**：desktop/mobile owner chat（`run_owner_chat_turn()`）、`desktop_wake` Path B
（perceive_event gate + `conversation_lock`，P0）、scheduler `_pipeline_send`
（perceive_event gate + `conversation_lock`，P1）均已用 uid 级锁覆盖
`fetch_context → build_prompt → run_llm → record_assistant_turn`。

**仍为 legacy**：`main.handle_message()`（QQ 主入口）仍在发送后以
`asyncio.create_task` 异步调用 `pipeline.post_process()`；`message_queue` 虽然串行调用
handler，下一条消息仍可能在上一条异步落库完成前进入 `fetch_context()`。
`_reply_with_tool_result`（QQ 工具确认）同样无 perceive_event gate 和
`conversation_lock`，已明确排除在 P0/P1 范围之外。

冻结管理面板 `/chat` 仍走异步 `post_process()`，未接入统一 turn sink /
conversation gate。legacy `/desktop/trigger` 已删除，不再属于本条缺口。

**建议**：把剩余 QQ 入口收口到 `record_assistant_turn()`；若保留先发送语义，也要保证
下一轮 `fetch_context()` 前 critical write 已完成。

---

### F10：trait_tracker 未接入当前 fixation 主链

**状态**：`fixed` (R8-B, 2026-06-10)

**位置**：`core/pipeline.py` → `_handler_trait_tracker_update` + `post_process()` 入队

R8-B 已落地：`post_process()` 在每个 `can_write_memory=True` 的有效 assistant turn 后
入队 `trait_tracker_update` slow_queue 任务，handler 独立于 `character_growth.update()`
运行，直接写入 `data/runtime/characters/{char_id}/inner/trait_state.json`。

R8-C 已落地（2026-06-10）：`author_note_rotator.get_current_note()` 新增 `char_id` 参数，
三处路径调用（`trait_state` / `author_note_state` / `author_notes_pool`）均随 `char_id`
传入对应角色路径；`prompt_builder.build()` 已透传本轮 `char_id`。
读写路径对齐，多角色 `underrepresented` 加权不再默认读 yexuan。

`character_growth.update()` 保留（不在 R8-B/R8-C 退役），legacy handler 不删除。

---

### F11：memory 工具已注册，但正式探针不会暴露

**状态**：`fixed-R5`（工具承诺侧已对齐；工具执行侧不变）

**位置**：`core/tool_dispatcher.py`、`core/prompt_builder.py`

`read_diary`、`read_watch`、`search_diary`、`get_profile`、`get_episodic`、`get_growth`
均已注册为 `category=”memory”`，`execute()` 也能执行。但 QQ 与 owner chat 的探针都只传
`categories=[“info”, “desktop”]`，`get_probe_prompt()` 同样只列出 info / desktop。

**R5 修复（Fable）**：`core/prompt_builder.py` 层11 Author's Note 的”强制工具规则”
已改为条件分支：有 `tool_result` 时提示”工具结果已提供”；无 `tool_result` 时明确
禁止声称调用工具/编造日记内容。不再包含无法履约的”必须调用 read_diary”指令。
`format_tool_capability_note()` helper 已加入 `tool_dispatcher.py`，供 prompt 注入工具
名称时从 registry 派生，防止手写不存在的工具名。

**剩余工作**（memory 工具完整接入超出 R5 范围）：若要让叶瑄在对话中真正自动触发
memory 类工具，需在 `run_llm()` 或对话循环中接入 tools schema + 执行回合。

---

### D2：调度器活跃窗口仍硬编码为 120 秒

**状态**：`now-safe-to-fix`

**位置**：`core/scheduler/loop.py` → `_user_active_recently()`

低优先级主动消息在用户 120 秒内活跃时跳过。`core/scheduler/policy.py` 已有 defer/drop
策略表，但该文件仍是静态 scaffold，没有接入真实执行层。

**建议**：先把窗口提到配置，再决定是否接入 per-trigger defer/drop。

---

### D7：叶瑄日记尚未反向进入长期认知

**状态**：`boundary-doc-needed`

**位置**：`data/runtime/characters/{char_id}/inner/diary/`

叶瑄每日写的日记目前只作为 prompt 层 6e 注入，尚未参与 user identity、trait tracker
或 mood_state 的长期更新。

**待设计**：决定它应影响角色长期认知，还是只保留为短期内省材料。

---

### G4：花园采后部分分支仍留在 harvest

**状态**：`boundary-doc-needed`

**位置**：`core/garden/manager.py` → `daily_check()`

`vase` 分支会从 `harvest` 移除；`dry` / `gift` / `ask` 只标记状态或
`handle_triggered`，仍留在 `harvest`。之后过期扫描会把同一朵花转成
`harvest_expired` 并移入 history。

**待设计**：明确干花、赠礼、询问后的最终容器，以及这些分支是否还应产生过期事件。

---

### P1：管理面板 context.max_turns 不影响真实 prompt 预算

**状态**：`fixed` (R7-A, 2026-06-10)

**位置**：`admin/routers/settings_misc.py`、`core/memory/short_term.py`

已修复：唯一真值 owner 统一为 `memory.short_term_rounds`。管理面板 PUT `/context-config`
现在写 `memory.short_term_rounds`；`get_history()` 读取顺序也已与 `load_for_prompt()` 对齐。
`context.max_turns` 降级为 deprecated alias，仅兼容读取旧配置。

---

### P2：prompt `_layer` 元数据仍透传给 LLM

**状态**：`now-safe-to-fix`

**位置**：`core/prompt_builder.py`、`core/pipeline.py`、`core/llm_client.py`

`prompt_builder.build()` 为裁剪和观测给每条 message 添加 `_layer`，返回前没有剥离；
`Pipeline.run_llm()` 又直接把 messages 传给 `llm_client.chat()`。这会把非标准字段发给
OpenAI-compatible API；宽松 provider 可能忽略，严格 provider 可能拒绝。

**建议**：在 `llm_client.chat()` 边界构造仅含协议字段的副本，内部 debug 继续保留 `_layer`。

---

### P3：裁剪后 layers_activated 仍包含已删除层

**状态**：`now-safe-to-fix`

**位置**：`core/prompt_builder.py` → token 强制裁剪

裁剪会从 `messages` 删除 droppable 层，但 `debug_info["layers_activated"]` 直接返回追加式
`_layers`。`tests/run_eval.py` 因而可能把已裁掉的层报告为仍激活。

**建议**：裁剪后从最终 messages 重算 effective layers，必要时另保留
`layers_before_trim`。

---

### TEST-1：部分迁移测试仍断言旧 datapath API

**状态**：`now-safe-to-fix`

**位置**：`tests/test_sandbox_paths.py`、`tests/test_post_process_ordering.py`

部分测试仍预期 `channel_queue()` 位于根目录、使用 `get_paths().event_log() / uid`，
或引用已经不存在的 `user_identity._identity_file()`。这些断言已经落后于
`data/runtime/` 与 `user_memory_root()` 布局。

**建议**：按当前路径模型更新测试，避免迁移回归被旧断言淹没。

---

### ADMIN-1：破限预设 TXT 导入缺少 Path 导入

**状态**：`now-safe-to-fix`

**位置**：`admin/routers/jailbreak_entries.py` → `import_entries_txt()`

TXT 导入分支调用 `Path(file.filename).stem`，但文件没有导入 `pathlib.Path`。
命中该接口时会抛出 `NameError`。

---

### SEC-AUTH-1：多个 admin 端点无鉴权（2026-06-10 核对）

**状态**：`now-safe-to-fix`

**位置**：`admin/routers/chat.py`

以下端点挂载在 admin server（默认 `127.0.0.1:8080`）但没有 Bearer token 校验：

| 端点 | 风险等级 | 影响 |
|---|---|---|
| `POST /upload/ingest` | 中 | 写入 `data/inbox/`，触发文件 ingest pipeline（含 LLM vision 调用） |
| `POST /desktop/activate` | 低 | 修改 desktop channel 活跃状态，不触发 LLM |
| `POST /desktop/deactivate` | 低 | 修改 desktop channel 活跃状态，不触发 LLM |
| `POST /desktop/wake` | **高** | 触发完整 LLM 轮（Path B），向 event_log / short_term 写入 assistant turn；任意能到达 admin server 的本地进程均可无鉴权调用 |

`/desktop/wake` 风险高于其他三个：Path B 走 `_pipeline_send()`，完整经历 `fetch_context → run_llm → post_process`，可在无用户参与的情况下写入记忆。

**已缓解**：admin server 默认绑定 `127.0.0.1`，外部网络无法直接访问；但同机进程（含注入攻击）可无障碍调用。

**建议**：给上述端点加统一 Bearer token 依赖（复用现有 `verify_admin_token`）。

---

### SEC-WS-1：WebSocket query token 仍是过渡方案

**状态**：`refactor-phase`

**位置**：`admin/admin_server.py`、`admin/log_filter.py`

`/ws/desktop?token=...` 已校验 token；`QuerySanitizeFilter` 也已安装到
`uvicorn.access`，会遮蔽 `token=` / `secret=` 值。原先“uvicorn access log 直接打印完整
token”的问题已缓解。

但 query token 仍可能出现在截图、代理日志、浏览器调试信息或其他日志链路中。

**建议**：后续迁移到 header、subprotocol、首包鉴权或配对机制。

---

### DESIGN-1：感知数据使用原则仍需补边界

**状态**：`boundary-doc-needed`

**位置**：`DESIGN.md` → “六、感知数据使用原则”

仍需定义：什么时候主动提起现实数据，什么时候只影响态度，哪些数据可以直接说出口，
哪些应隐藏在关心里。

---

### DESIGN-2：主动行为设计原则仍需补边界

**状态**：`boundary-doc-needed`

**位置**：`DESIGN.md` → “七、主动行为设计原则”

仍需定义：主动联系与不打扰的平衡、哪些事值得打断用户、哪些事等待用户来找。

---

### F8：管理面板对话 UI 右键历史未实现

**状态**：`observe`

**位置**：`admin/static/index.html`

对话记录当前没有右键菜单或快捷历史操作。属于前端体验债，与主链路无关。

---

## 观察与重构债

### identity-1：counter 累积没有时间衰减

**状态**：`observe`

**位置**：`core/memory/fixation_pipeline.py` → `_synthesize_identity()`

`counter_evidence_count` 只在 LLM 重写 text 时归零，否则只增不减。若模型长期保守，
某个维度可能持续低于注入阈值。观察实际数据后再决定是否按 `last_conflict_at` 做时间衰减。

### identity-2：identity 注入有冷启动期

**状态**：`observe`

新用户要经过 mid-term → episodic → consolidate 后，`identity.yaml` 才会开始注入。
这是“宁可不注入，也不瞎猜”的预期代价。观察首个有效维度需要多少轮，再决定是否调整阈值。

### TD-1：sandbox.py 兼容胶水暂时不能退休

**状态**：`refactor-phase`

`core/data_paths.py` 已承接实现，`core/sandbox.py` 只保留单例和测试入口。但项目仍有大量
`from core.sandbox import get_paths`，测试 fixture 也依赖它。当前应把它视为稳定兼容层，
不要为了命名整洁做大范围替换。

### TD-2：CharacterGrowth retirement 尚未结束

**状态**：`refactor-phase`（R8-D 审计已完成，进入 R8-E 候选）

**R8-D 审计结论（2026-06-10）**：

| 对象 | 状态 | 依据 |
|---|---|---|
| `character_growth.update()` | DEAD_CANDIDATE | 零生产调用方；`fixation_pipeline` 已切换到 `consolidate_to_identity` |
| `character_growth.load()` | ACTIVE | `tool_dispatcher._get_growth_wrapper()` 唯一调用方，`get_growth` 工具读路径 |
| `consolidate_to_growth` | REMOVED R8-E1 | 已从 `LEGACY_TASK_TYPES` 移除；从未注册 handler，无 enqueue，无 DLQ 文件 |
| `mid_term_append` | LEGACY_COMPAT | handler 注册（DLQ 保护），无新 enqueue，无 DLQ 文件存量 |
| `episodic_compress` | LEGACY_COMPAT | handler 注册（DLQ 保护），无新 enqueue，无 DLQ 文件存量 |
| `LEGACY_TASK_TYPES` | ACTIVE | `time_based` DLQ monitor sweep 使用 |
| `character_growth.update()` 内 `trait_state()` 无 char_id | 死代码缺陷 | 因 update() 无调用方，不影响生产；R8-E 删除时一并清理 |

**R8-E1 已完成**：
- `consolidate_to_growth` 已从 `LEGACY_TASK_TYPES` 移除（2026-06-10，R8-E1）

**R8-E 候选（可在下包删除）**：
- `character_growth.update()` 函数体及调用（整个 async 函数，不含 `load()`）
- `character_growth.should_update()` 函数（已注明 "Legacy：当前无外部调用者"）

**保留至 30-day TTL 后再评估**：
- `mid_term_append` / `episodic_compress` handler 注册

原有结论：`tool_dispatcher._get_growth_wrapper()`、legacy `character_growth` 文件与旧测试说明仍保留。
先解决 F10 / F11，再决定是否删除兼容出口。

### TD-3：DLQ legacy handler 兼容层已设 30 天过期（R8-A）

**状态**：`observe`

**位置**：`core/post_process/slow_queue.py`、`core/scheduler/triggers/time_based.py`

`mid_term_append`、`episodic_compress` 两个 handler 仅为 DLQ 残留任务保留。
R8-A 起，`dlq_monitor` 每日扫描时自动将这些类型超过 30 天的
DLQ 文件移到 `data/logs/dead_letter_queue/expired/`（不静默删除，保留审计记录）。

R8-E1（2026-06-10）：`consolidate_to_growth` 已从 `LEGACY_TASK_TYPES` 移除 —
它从未注册 handler，从未 enqueue，DLQ 目录无任何存量文件，属 DEAD 名字残留。

R8-D 实测：当前 DLQ 目录无任何 legacy task 文件（`mid_term_append` / `episodic_compress`），
30-day TTL 归零已提前达成（无 legacy 积压）。

30 天观察已可视为完成，届时可进入 R8-E 评估 handler 是否可安全退役。
在此之前 **不要删除** `mid_term_append` / `episodic_compress` 的 handler 注册。

### 其他观察

- `short_term._sanitize_assistant_message()` 在读取 history 时清洗，不回写磁盘。
- `llm_output_validator` 失败计数在内存中；debug 输出写到
  `data/debug/llm_output/`，保留 7 天。
- event_log 当前实际路径是
  `data/runtime/memory/{char_id}/{uid}/event_log/{date}.md`，不是 `.jsonl`。
- scheduler policy 表仍是 scaffold；真实活跃窗口判断仍在 `_pipeline_send()`。
- Dream 输出协议设计：本轮跳过，等待并行改动稳定后再核。
- `mes_example` 精简、时间联动注入属于 prompt 体感策略，不作为当前小修。

---

## 本轮已核对关闭

| 编号 | 结论 |
|---|---|
| P0 Write Envelope v0 | 已完成。写入准入 fail-closed；未 stamp 默认不写 memory / mood；`is_test` / `is_debug` 强制不可写；sensor / watch 原始感知默认不写 profile。该结论不等于完整权限系统或完整字段契约。 |
| P0 QQ Dream Guard | 已完成。`DREAM_ACTIVE` / `DREAM_CLOSING` 时 QQ owner 消息被拒，不进入现实 pipeline，不写 runtime / memory。P2.4：guard 升级为 fail-closed — 状态文件损坏 / 读失败时同样拒绝 reality turn；`/desktop/chat`、`/mobile/chat`、`/desktop/wake` Path B 同步覆盖。 |
| P0 Render Tag 收口 | 已完成。QQ / mobile 输出移除 `<say>` 等展示标签；reality memory / event_log 保存纯文本；desktop segments 保持原行为。 |
| P0 `/desktop/trigger` 无鉴权旧入口 | 已完成。零调用方确认后已删除 legacy route。 |
| S2 `data/chars` 幽灵分支 | 已完成。生产代码字面量引用归零；`data/chars` 仅作为已退休路径留档，S6 layout 测试已通过。 |
| B12 核心情景记忆未被上限裁剪保护 | 已修复。`write_episode()` 只从非核心记忆删除低 strength 项。 |
| S1 部分 data 路径绕过 sandbox | 已修复。列出的运行模块已走 `get_paths()`；剩余硬编码是 DataPaths 内明确的 authored-content fallback。 |
| D10 diary_share 新旧路径去重源分叉 | 已修复。状态拆到 `scheduler_user_state.json`；proposal 仅在 sent 后执行 `after_send` 和 `_mark()`。 |
| G2 花园写入未使用 safe_write / 锁 | 已修复。`manager.py` 已有 `threading.RLock()` 与 `safe_write_json()`。 |
| HANDOFF Step 3 shadow 卡点 | 已过时。原生 proposer 与 `EXECUTE_MODE="live"` winner 已落地。 |
| D9 Watch 即时路径与 execute live 边界 | 已核对。Watch 由独立 `WATCH_EXECUTE_MODE="live"` 执行 proposal；rollback 分支保留。 |
| short_term 加权裁剪未开 | 已修复。`load_for_prompt()` 使用近场保留和远场加权择优。 |
| SEC-LOG-001 uvicorn access log 直接泄露 token | 已缓解。access log sanitizer 已安装；残余 query 风险转为 SEC-WS-1。 |

历史已修复项继续以 git 历史和相关测试为准，不再在本文重复堆叠。
