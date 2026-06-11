# docs/known-issues.md — 已知问题与技术债

> 最近核对：2026-06-11
> 核对范围：R1-C QQ adapter 收口；R1-B QQ 主入口 full-convergence 审计；perceive_event P0+P1 收口。已落地事项按当前实现同步；未列出的条目保持原审计结论。
>
> 状态标签：
> - `now-safe-to-fix`：可按小修推进。
> - `refactor-phase`：适合重构期统一处理。
> - `boundary-doc-needed`：先补设计或行为边界，再决定是否改代码。
> - `observe`：已有缓解或属于兼容层，先观察。

---

## 当前仍存在

### B11：QQ 主入口 turn_sink 统一（turn-sink-converged）

**状态**：`fixed`（R1-D 完成 2026-06-11；QQ LLM 回复链路已接入 `record_assistant_turn`）

**位置**：`main.py` → `_qq_reality_reply_adapter()` → `core/turn_sink.record_assistant_turn()`

**已完成（含 R1-D）**：

| 修复点 | 包 | 当前状态 |
|---|---|---|
| `handle_message` + `_reply_with_tool_result` 均改为 `await post_process()` | N10 | ✓ 完成 |
| 轮级 scope freeze（`_frozen_scope`）+ 透传到 `post_process` | N1 | ✓ 完成 |
| `conversation_lock` 覆盖 QQ 主路径和工具确认路径 | R1 | ✓ 完成 |
| pre-scrub（turn_sink 内 `scrub_reality_output_text`）+ `strip_render_tags` | R6-A/B | ✓ 完成 |
| legacy `/chat` 禁用（410）、`/desktop/trigger` 已删除 | — | ✓ 完成 |
| `QQChannel.send` 支持 `target_id / is_group`（移除硬编码 False） | R1-C | ✓ 完成 |
| `_qq_reality_reply_adapter` 统一两条 LLM 回复链路，消除重复代码 | R1-C | ✓ 完成 |
| adapter 内存写链路接入 `record_assistant_turn`（turn_sink 统一入口） | R1-D | ✓ 完成 |
| `record_assistant_turn` 支持 `target_id / is_group / pending_paths / frozen_scope` | R1-D | ✓ 完成 |

**当前 QQ LLM reply 路径（R1-D 后）**：

```
_qq_reality_reply_adapter
  ├── strip_render_tags → text_output.send → QQ   (REALITY_VISIBLE)
  └── record_assistant_turn(fanout=[], bypass_gate=True)
        ├── scrub_reality_output_text               (defense-in-depth)
        └── await post_process(frozen_scope, ...)
              └── capture_turn                      (REALITY_MEMORY authority)
```

**text_output.send 分类（R1-D 后全部 main.py 调用点）**：

| 分类 | 数量 | 位置 | 是否写 memory |
|---|---|---|---|
| LLM_ASSISTANT_REPLY | 1 | `_qq_reality_reply_adapter`（→ turn_sink） | 是（via record_assistant_turn → post_process → capture_turn） |
| SYSTEM_SHORT_TEXT | 3 | 梦境 guard ×3（_to_dg 别名） | 否（直接 return） |
| SYSTEM_SHORT_TEXT | 1 | 取消确认 `text_output.send` | 否（直接 return） |
| TOOL_CONFIRMATION_PROMPT | 2 | WAITING_INPUT ask_text、probe ask_text | 否（直接 return） |

**剩余架构缺口**（不影响数据安全）：

- **QQ 仍不经 channel fanout**：`fanout=[]`，desktop/mobile 不收到广播。
  这是预期行为；如需广播改 `fanout="all"` 即可，不影响 scrub 链路。

**R6 final（单出口稳态）已完成（2026-06-11）**：全部 QQ LLM 回复出口均经
`_qq_reality_reply_adapter` → `record_assistant_turn`（turn_sink）→ `post_process` → `capture_turn`。

守卫测试：
- `tests/test_r1b_qq_convergence_audit.py`（A10 已翻转，13 项 pass，2026-06-11）
- `tests/test_r1c_qq_reality_reply_adapter.py`（R1-C/D 更新，29+ 项，2026-06-11）
- `tests/test_r1d_qq_reality_reply_adapter.py`（R1-D 专项，新增，2026-06-11）
- `tests/test_r6c_reality_scrub_final.py`（R6-final，R1-D 更新，2026-06-11）

---

### F10：trait_tracker 未接入当前 fixation 主链

**状态**：`fixed` (R8-B, 2026-06-10)

**位置**：`core/pipeline.py` → `_handler_trait_tracker_update` + `post_process()` 入队

R8-B 已落地：`post_process()` 在每个 `can_write_memory=True` 的有效 assistant turn 后
入队 `trait_tracker_update` slow_queue 任务，handler 独立于 `character_growth.update()`
运行，直接写入 `data/runtime/characters/{char_id}/inner/trait_state.json`。

R8-C 已落地（2026-06-10）：`author_note_rotator.get_current_note()` 新增 `char_id` 参数，
主要路径三处调用（`trait_state` / `author_note_state` / `author_notes_pool`）均随 `char_id`
传入对应角色路径；`prompt_builder.build()` 已透传本轮 `char_id`。
读写路径对齐，多角色 `underrepresented` 加权不再默认读 yexuan。

R8-C P1-补丁（2026-06-11）：上述修复遗漏了 `_TRANSITION_CHARACTER_INNER` 分支的 write-back
路径。原代码在状态切换时额外写一份 `yexuan_inner/author_note_state.json`，未检查 `char_id`，
导致非 yexuan 角色也会写入 legacy yexuan_inner 路径。现已加
`char_id is None or char_id == "yexuan"` 守卫：只有 yexuan/legacy 调用才触发该 write-back。
守卫测试：`tests/test_r8c_author_note_rotator_scope.py`（tests 8 & 9）。

R8-E2 已落地（2026-06-10）：`character_growth.update()` 已删除，其内部 `trait_state()` 死代码写路径（缺 char_id）随之消除。`character_growth` 现为只读 legacy 接口，写入链完全由 `trait_tracker_update` slow_queue task 承接。

R8-E3 已落地（2026-06-10）：`character_growth.py` 模块 docstring 及 `load()` docstring 已明确标注 read-only legacy compatibility surface，并注明禁止重引入 `update()` / `should_update()`。`get_growth` 工具描述已更新为"只读历史成长摘要，不触发写入"。新增 `tests/test_r8e3_character_growth_readonly_contract.py`（7 个只读契约守卫）。`docs/memory.md` 补充 character_growth 只读声明 callout。

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

### D2：调度器发言决策面统一（R2-B/C/D + Watch P1 已完成）

**状态**：`fixed`（2026-06-11）

**位置**：`core/scheduler/loop.py` → `_pipeline_send()` / `core/scheduler/gating.py`

R2-A 已完成审计（2026-06-10）。

R2-B/C/D 已完成：active-window / DND / defer / state / cooldown 决策统一在
`gating._decide()`，`policy.py` 是运行时策略权威，执行层只负责 send + mark。
Watch 的 hr_critical / hr_high / sleep_end 事件到达路径也通过
`gating.decide_and_execute_event()` 进入同一决策面；block/defer 不发送、不 mark。
`WATCH_EXECUTE_MODE` 仅保留为事件到达时 live/dry-run 的 rollback/config switch。

后续 P2 卫生清理仍保留，不属于本轮阻断项：例如 defer release 时机、policy priority 与
urgency 的进一步对齐、旧兼容代码清理。

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

### SEC-AUTH-1：HTTP 管理面 Bearer-only

**状态**：`fixed`（2026-06-11）

HTTP 管理面已统一依赖 `admin.auth.verify_token`。本轮最终 P1 补齐：
- `POST /sensor/activity` 已 fail-closed；无 token / 错 token 不进入函数体、不写 snapshot。
- `POST /watch/event` 已移除 `?secret=` 鉴权，只接受 Authorization Bearer。
- OpenAPI 不再暴露 `secret` / `token` query 鉴权参数。
- 全路由鉴权盘点守卫会枚举 FastAPI routes；唯一 public allowlist 是根状态页 `/`，并写明原因。

---

### SEC-WS-1：WebSocket query token 迁移

**状态**：`fixed`（R9 final，2026-06-11）

**位置**：`admin/admin_server.py`、`admin/auth.py`、`admin/log_filter.py`

**R9 final 已完成**：
- `admin/auth.authenticate_ws()` + `extract_ws_token()` 统一 WS 鉴权，只接受 `Authorization: Bearer` header。
- `?token=` query fallback 已删除；即使 token 正确也拒绝连接。
- `ws_desktop_endpoint` 不再声明 `?token=` query param，不再把 token 暴露给 FastAPI OpenAPI。
- token 值不出现在任何日志路径或错误响应；deprecated fallback warning 已移除。
- Emerald-client 已通过 Tauri Rust native bridge 完成 header 迁移。
- `QuerySanitizeFilter` 保留，覆盖被拒绝请求及其他敏感 query 参数的 access log 泄漏风险。
- 守卫测试：`tests/test_sec_ws1_auth.py`。

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

**状态**：`fixed`（R8-E2，2026-06-10）

**R8-E2 完成结论**：

| 对象 | 状态 | 依据 |
|---|---|---|
| `character_growth.update()` | REMOVED R8-E2 | 函数已删除；写入链迁移到 `consolidate_to_identity` + `trait_tracker_update` |
| `character_growth.should_update()` | REMOVED R8-E2 | 函数已删除；无外部调用方 |
| `character_growth.load()` | ACTIVE | `tool_dispatcher._get_growth_wrapper()` 唯一调用方，`get_growth` 工具只读兼容面 |
| `consolidate_to_growth` | REMOVED R8-E1 | 已从 `LEGACY_TASK_TYPES` 移除；从未注册 handler，无 enqueue，无 DLQ 文件 |
| `mid_term_append` | LEGACY_COMPAT | handler 注册（DLQ 保护），无新 enqueue，无 DLQ 文件存量 |
| `episodic_compress` | LEGACY_COMPAT | handler 注册（DLQ 保护），无新 enqueue，无 DLQ 文件存量 |
| `LEGACY_TASK_TYPES` | ACTIVE | `time_based` DLQ monitor sweep 使用 |
| `character_growth.update()` 内 `trait_state()` 无 char_id | REMOVED R8-E2 | 死代码缺陷随 update() 删除一并消除 |

**R8-E1 已完成**（2026-06-10）：
- `consolidate_to_growth` 已从 `LEGACY_TASK_TYPES` 移除

**R8-E2 已完成**（2026-06-10）：
- `character_growth.update()` 函数体已删除
- `character_growth.should_update()` 函数已删除
- 死代码携带的 `trait_state()` 无 char_id 缺陷随函数删除一并消除
- `character_growth.py` 现为只读 legacy 接口，不再含 `char_id="yexuan"` 默认参数

**保留至 30-day TTL 后再评估**：
- `mid_term_append` / `episodic_compress` handler 注册（见 TD-3）

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
- scheduler policy 表已接线（R2-B/C/D done）；Watch 发言事件到达路径也进入统一 gating/policy。
- Dream 输出协议设计：本轮跳过，等待并行改动稳定后再核。
- R6-A（reality 输出 scrub 审计）：已完成，4 个调用点全部核对，无高风险遗漏（2026-06-10）。
- R6-B（scrub 契约固化）：已完成（2026-06-10）。capture_turn 权威注释、3 处预清洗注释、tests/test_r6b_reality_scrub_contract.py（C1–C10，17 门禁）、docs/assistant-turn-sink.md §十、docs/memory.md R6 callout 均已写入。无新现实记忆出口遗漏。
- R6-final（单出口稳态确认）：已完成（2026-06-11）。R1-D 后 QQ 路径完整接入 turn_sink，全部 LLM_ASSISTANT_REPLY 均经 scrub 链。守卫：`tests/test_r6c_reality_scrub_final.py`。
- R1-D（QQ turn_sink 全量化）：已完成（2026-06-11）。`_qq_reality_reply_adapter` 调用 `record_assistant_turn`，QQ 不再是独立手写落库链路。剩余：QQ channel fanout（`fanout=[]` → `fanout="all"`）为架构统一可选项，不影响 scrub 安全。
- `mes_example` 精简、时间联动注入属于 prompt 体感策略，不作为当前小修。
- R3 CI 门禁（2026-06-11）：`tests/test_r3_scope_lint.py`（13 条）守卫 core/ 不新增 `char_id="yexuan"` 函数默认参数或裸 `data/` 路径；`tests/test_r3_memory_scope_cleanup_contract.py` 追踪迁移目标 allowlist 清理进度，守卫 admin/ 无新违规。`docs/memory.md` 残余工作 1–8、10 项已全部落地；残留 followup：旧 uid-only 数据迁移（9）、ShortTermMemory 默认值（11）、scope freeze 统一（12）。

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
| D9 Watch 即时路径与 execute live 边界 | 已关闭。Watch 事件到达路径先经 `gating.decide_and_execute_event()` / `_decide()`；`WATCH_EXECUTE_MODE` 仅保留为 rollback/config switch。 |
| Final P1 `/sensor/activity` 无鉴权 | 已关闭。Bearer-only，鉴权失败无副作用。 |
| Final P1 `/watch/event` query secret | 已关闭。Bearer-only，正确 query secret 也拒绝，OpenAPI 无 secret query 参数。 |
| short_term 加权裁剪未开 | 已修复。`load_for_prompt()` 使用近场保留和远场加权择优。 |
| SEC-LOG-001 uvicorn access log 直接泄露 token | 已关闭。access log sanitizer 已安装；SEC-WS-1 final 已拒绝 WS query token。 |

历史已修复项继续以 git 历史和相关测试为准，不再在本文重复堆叠。
