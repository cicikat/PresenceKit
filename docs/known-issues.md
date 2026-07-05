# docs/known-issues.md — 已知问题与技术债

> 最近核对：2026-06-26
> 核对范围：F2 窗口标题注入 + peek_screen_content 工具落地。
>
> 状态标签：
> - `now-safe-to-fix`：可按小修推进。
> - `refactor-phase`：适合重构期统一处理。
> - `boundary-doc-needed`：先补设计或行为边界，再决定是否改代码。
> - `observe`：已有缓解或属于兼容层，先观察。

---

## 当前仍存在

### H1：hidden_state 现实侧写入链未接线

**状态**：`boundary-doc-needed`

**位置**：`core/memory/user_hidden_state_integrator.py` → `integrate_event_and_save` / `integrate_impression_and_save`；`core/pipeline.py` → `post_process`

**根因（审计结论 2026-06-14）**：

`user_hidden_state_integrator.py` 的 `integrate_event_and_save` / `integrate_impression_and_save` / `integrate_body_cue_and_save` 三个 disk-wired 入口在全仓**零调用**（已 grep 确认）。运行时唯一修改 `hidden_state.json` 的路径只有三处：

| 路径 | 文件 | 触发条件 |
|---|---|---|
| `integrate_afterglow_and_save` | `core/dream/dream_exit_afterglow.py` | 出梦后 afterglow 回流 |
| `apply_time_decay` | `core/scheduler/triggers/hidden_state_decay.py` | 12h 调度 tick |
| `consolidate_baselines` | `core/scheduler/triggers/hidden_state_decay.py` | 7d 调度 tick |

**后果**：不做梦的情况下，`sensitivity / touch_need / embodied_ease / body_memory` 只随时间朝基线衰减，现实对话中观察到的任何行为信号从不写入。审计脚本 `scripts/audit_hidden_state.py` 可验证——`last_update_source` 的分布里永远不出现 `reality_behavior`，只有 `time_decay` / `init`。

**这不是 bug**：写入链已设计并实现，但「哪些现实信号映射到哪种 `RealityEventType`」尚未拍板，接线因此搁置。

**接线前需先确定**（参见 `cc-tasks/08b-hidden-state-接现实写入.md`）：

1. `post_process` 的哪个位置调 `integrate_event_and_save`（`RealityEventType.SEEK_COMPANIONSHIP` / `RECEIVED_COMFORT` / `NO_INTERACTION`）
2. `fixation_pipeline` 的哪个位置调 `integrate_impression_and_save`（情绪强度/亲密倾向 → `ImpressionInput`）
3. 并发守卫：需在 `uid_lock` 内调用（`post_process` 已持锁），且 `WriteEnvelope` 需为 `stamp_user_chat()` 不是 `stamp_debug()`
4. 已有 `_assert_not_long_term` source 守卫——中期层 integrator 不得触及 baseline / embodied_ease / body_memory（仅 `integrate_body_cue_and_save` 可写 body_memory）

**可视化**：`SubHiddenStatePanel.tsx` 已在"SOURCE OVERVIEW"区域当所有 source 均为 `time_decay` 时显示提示，方便实时确认接线前后的变化。

---

### ACT-1：阅读动向被记录进 yexuan 数据——审计结论：后端已隔离，怀疑点在前端

**状态**：`observe`

**位置**：`core/activity/reading_companion.py` / `core/activity/activity_summary.py` / `core/activity/transcript.py` / `core/activity/activity_store.py`

**背景**：用户报告"阅读动向被记录进 yexuan 数据"（CC 任务 24 · 3.3 顺手核查）。`admin/routers/reading.py` 路由侧已用 `_active_char_id()`（现抽到 `admin/routers/_common.py`）在每个 endpoint 入口解析 char_id 并显式传递。

**审计结论（2026-07-05）**：grep 确认 `reading_companion.py` / `activity_summary.py` / `transcript.py` / `activity_store.py` 里的 `char_id` 参数**全部无默认值**（无 `char_id: str = "yexuan"` 这类签名），调用方必须显式传入，不存在"忘传 char_id 时静默落到 yexuan 路径"的缺省缺口。后端阅读/共同活动记忆路径本身是按 char_id + uid 双重隔离的。

**怀疑点**：现象更可能来自 (a) 前端动向时间轴目前是全局 localStorage，不按角色分桶（属于 `Emerald-client` 前端侧 Brief 14 处理范围）；或 (b) 用户操作时 `active_character` 本身就切到了 yexuan（不是代码 bug，是当时激活的角色确实是 yexuan）。本轮同时修复的 `core/activity_manager.py` 全局单状态 bug（`/activity/current` 随机池此前永远读 yexuan 路径，与 `active_character` 无关）已独立解决，可能是用户实际观察到的现象的另一半根因——`GET /activity/current` 修复前返回的动向文案本就不随角色变化。

**下一步**：待前端接入按角色隔离的时间轴（Brief 14）后，若现象仍复现，再回来查 (b)。

---

### ACT-2：反坍缩输出端重试未覆盖流式路径

**状态**：`now-safe-to-fix`（需要独立设计，非小修）

**位置**：`core/pipeline.py::Pipeline.run_llm_stream()`；对照 `run_llm()` 里的 `_anti_collapse_prefix_retry()`

**背景**：CC 任务 24 · 2.2 (c) 在 `run_llm()`（非流式）加了输出端校验重试——LLM 回复仍以检测到的重复句首 P 开头时追加强指令重试一次。桌宠聊天等走 `run_llm_stream()` 的路径没有接入这个机制。

**原因**：流式场景下 token 已经边生成边推给前端渲染，检测到"又是 P 开头"时前几个字符往往已经吐给用户看到了，不能像非流式那样直接丢弃整段重来；需要专门设计（比如：只在检测到 P 命中时暂缓推送前 N 个 token，等确认非 P 开头再开始转发，或者接受"流式场景下只做软提示、不做硬止血"的降级策略）。

**影响范围**：桌宠 `admin/routers/chat.py` 的 `run_llm_stream` 分支、其他潜在流式入口。

---

### B11：QQ 主入口 turn_sink 统一（turn-sink-converged）

**状态**：`fixed`（R1-D 完成 2026-06-11；QQ LLM 回复链路已接入 `record_assistant_turn`）

**位置**：`main.py` → `_qq_reality_reply_adapter()` → `core/turn_sink.record_assistant_turn()`

**R1-B 基线审计（2026-06-11，`tests/test_r1b_qq_convergence_audit.py`）**：R1-B 是本条目的起点——
对 QQ 主入口 vs `turn_sink` 统一目标做的一次全量收敛审计，当时钉住的差距（partial-convergence
baseline）是：
- `QQChannel.send` 硬编码 `is_group=False`（阻塞群聊，R1-C 前置项）
- `_qq_reality_reply_adapter` 直接调用 `post_process`，未走 `record_assistant_turn`
- LLM_ASSISTANT_REPLY 走 `text_output.send` 直发，不经 channel fanout

以下 R1-C / R1-D 均是在 R1-B 钉住的这份基线之上做的收敛修复；`test_r1b_qq_convergence_audit.py`
里的 A9/A10 用例现在断言这些差距已经被"翻转"（即已修复）。

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

**剩余工作**（memory 工具完整接入超出 R5 范围）：若要让他在对话中真正自动触发
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

### D7：他日记尚未反向进入长期认知

**状态**：`boundary-doc-needed`

**位置**：`data/runtime/characters/{char_id}/inner/diary/`

他每日写的日记目前只作为 prompt 层 6e 注入，尚未参与 user identity、trait tracker
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

### SEC-AUTH-2：鉴权分层 — Scoped Tokens

**状态**：`in-progress`（P1+P2+P3 已合入，2026-07-04）

**位置**：`admin/auth.py`、`admin/scopes.py`、`admin/token_registry.py`、`admin/audit.py`、
`admin/routers/auth_tokens.py`、`core/data_paths.py`、`admin/routers/*.py`（全部 ~34 个 router）

现状（SEC-AUTH-1/SEC-WS-1 之后）：单一 secret 过关后所有接口平权，边缘设备（手机
sensor-service、Watch、ESP32、手机轮询端）与桌面主客户端持有同一 god token。详见
`cc-tasks/21-鉴权分层-scoped-tokens.md`。

**P1 已完成**：
- `admin/scopes.py`：10 个 scope + 6 个 profile 预置组合，`expand_scopes()` 展开 `profile:*`。
- `admin/token_registry.py`：`data/runtime/auth/tokens.yaml` 加载/mtime 热重载，只存
  sha256(token) 不存明文，`hmac.compare_digest` 比对。
- `admin/auth.py`：`TokenInfo` + `resolve_token()`；`require_scopes(*scopes)` 依赖工厂
  （无效/缺失 token → 401；scope 不足 → 403）；`verify_token = require_scopes("admin")`
  别名（迁移期 fail-closed：忘记声明 scope 的端点自动收敛为 admin-only）；
  `authenticate_ws(websocket, required_scope)` 已参数化。
- legacy secret（env `YEXUAN_ADMIN_SECRET` / `config.admin.secret_key`）永远等价虚拟
  `admin` token（label `legacy-admin`），现存客户端零改动继续可用。

**P2 已完成**：
- 按 brief §5 映射表，全部 ~34 个 router 的 `Depends(verify_token)` 已替换为
  `Depends(require_scopes(...))`，按端点/HTTP method 精确到 scope（`memory.py`、
  `relations.py`、`relationship_facts.py`、`sensor.py`、`watch.py`、`system.py`、
  `scheduler.py`、`settings_misc.py` 内部按 method 拆分，其余整文件统一 scope）。
  `mobile` profile 按 Android 端配对文档增补为
  `chat, state.read, memory.read, activity, persona, sensor.write`。
- 守卫测试：`tests/test_sec_auth2_scopes.py`（全量 APIRoute default-deny 扫描 + scope 语义
  403/401 区分 + legacy 兼容 + WS scope 交叉拒绝，对真实 `admin_server.app` 路由验证）。
- 旧有假设"所有端点共享同一个 `verify_token` 可调用对象"的测试
  （`test_final_p1_blockers.py`、`test_buttplug_integration.py`、`test_activity_contract.py`、
  `test_character_avatar_binding.py`、`test_meta_mode.py`、`test_dream_session_char_scope.py`）
  已同步更新为检测 `_required_scopes` 标记 / 按标记覆盖依赖，不再要求身份相同。
- 全量 `pytest` 验证：除既有与本改动无关的 pre-existing 失败外无新增回归。

**P3 已完成**：
- `admin/token_registry.py` 新增 `create_token()` / `rotate_token()` / `delete_token()`：
  label 校验 `^[a-z0-9-]{1,32}$`，`legacy-admin` 保留字不可创建/轮换/吊销；`rotate` 换新
  明文旧值立即失效；`delete` 物理删除条目（未选择 disabled 标记方案）。
- `admin/routers/auth_tokens.py`（新 router，全部 `admin` scope）：
  `GET /auth/tokens`（label/scopes/expires_at/disabled/hash 前 8 位，无明文）、
  `POST /auth/tokens`（body `{label, profile 或 scopes, expires_at?}`，明文仅此一次返回）、
  `POST /auth/tokens/{label}/rotate`、`DELETE /auth/tokens/{label}`。
- `admin/audit.py`：`log_event()` 追加写 `data/runtime/auth/audit.jsonl`（走
  `core.safe_write.safe_append_jsonl`，天然 fail-open，不阻塞请求，不记 token 值）。
  `require_scopes` 在 401（`auth_failed`）/403（`scope_denied`）时调用；
  token 创建/轮换/吊销各记一条；`PATCH /system/meta-mode` 切到 `danger` 时记
  `meta_mode_danger`（含操作者 label）。
- `admin/auth.py` 新增进程内存限速：按来源 IP 统计 401 失败，60s 窗口内 ≥10 次 →
  该 IP 后续认证请求直接 429，持续 300s（`reset_rate_limit_state_for_test()` 供测试清零）。
  **注意**：这个模块级状态是全 pytest 会话共享的——`tests/conftest.py` 新增
  autouse fixture `reset_auth_rate_limit` 在每个测试前后清零，否则跨文件累计的
  401（如 `tests/test_sec_auth1.py` 的大量 no-token/wrong-token 用例）会意外触发
  429 拖垮无关测试。
- 守卫测试：`tests/test_sec_auth2_scopes.py` 新增 §8 items 6-7（token 管理 API 全生命周期
  create→use→rotate→旧值失效→delete→401；同 IP 11 次坏 token → 429；403 不计入限速窗口）。

**P4（前半：签发）已完成，2026-07-04**：
- 生产 `data/runtime/auth/tokens.yaml` 已用 `create_token()` 建好六条记录，对应六类持有者：
  `desktop-main`（profile:desktop）、`mobile-main`（profile:mobile）、
  `sensor-service`（profile:sensor）、`watch-main`（profile:watch）、
  `esp32-device`（profile:device）、`admin-panel`（profile:panel）。
  明文仅在创建时的终端输出里出现过一次，未落盘、未写入本仓库任何文件——需要各自去接
  收发放的一方从当时的终端输出里取值妥善保存；若已丢失只能 `POST /auth/tokens/{label}/rotate`
  换新值（也是仅此一次展示）。
- **legacy secret（`YEXUAN_ADMIN_SECRET` / `config.admin.secret_key`）未改动**，仍是全权
  bootstrap token，现存客户端零影响。
- 附带发现：`tests/test_sec_auth1.py`、`test_final_p1_blockers.py` 里若干直接构造 FastAPI
  测试 app 打真实请求的用例没有用 `sandbox` fixture 隔离，P3 新增的审计写入曾往生产
  `data/runtime/auth/audit.jsonl` 里追加了约 200 条 `ip=testclient` 的测试噪音（无害，
  append-only 且不含敏感信息，但不是真实流量）；未在本轮自动清理，留给你决定是否手动清空。

**P4（客户端接入）核实，2026-07-04**：
- **`Emerald-client` 桌面端 + `sensor-service`**：cc-tasks/13 已按规格完整实现——
  `client_config.rs`/`config/client.example.json` 字段名不变，注释更新；`lib.rs` 全部
  21 处调用点迁移到区分 401/403/429 的 `safe_http_error`；`ws_bridge.rs` 拒绝文案改中性；
  `sensor-service/config.yaml`、`bot_client/post.py` 同步。`cargo test --lib`：**41/41 通过**
  （含新增 `auth_tests` 模块）。
- **`yexuan_memery` 安卓端**：round-鉴权分层-scoped-tokens-移动端.md 已实现——
  `_extractError` 按 401/403/429 分支；`MobileNotificationService.kt` 对 403（scope 不足）
  停止重试并标记 `"token scope insufficient"`，不再无意义刷后端审计/限速。新增
  `test/backend_client_error_test.dart`：**6/6 通过**。**附带发现**：该仓 `flutter test`
  整体跑会在 `test/foreground_mobile_delivery_contract_test.dart` 编译失败——一个测试用
  `_ForegroundBackendClient.pollMobile` mock 覆写没跟上 `BackendClient.pollMobile` 新增的
  `waitSeconds` 参数。与鉴权改动无关（是另一个长轮询 wait 参数功能引入的），未在本轮修复。
- **`firmware/presence-device`（ESP32，真正的具身硬件固件；`hardware/Emerald-hello`
  是已废弃的早期 OLED 测试项目，与此无关）**：发现 `include/secrets.h` 仍把
  legacy secret 明文硬编码为 `AUTH_TOKEN`（未走后端配对 cc-tasks，此前没人做过这块）。
  已修复：`secrets.h`（gitignored，本地设备凭据文件）与 `secrets.example.h` 均已改为
  `esp32-device` profile token；因为拿不到该 token 已发放的明文，改动前先 rotate 了一次
  （已获用户确认可接受，旧值同时失效）。**待办：板子需要重新烧录**才能生效——当前物理
  设备仍在用旧固件里的 legacy secret 运行，直到你重新烧录前不受影响，但也没有切换。
- **Watch**：本仓找不到代码（推测是 iOS Shortcuts 直连 `POST /watch/event`），无从代码层
  核实；轮换前需要你手动确认并替换 Shortcut 里的 Bearer 值。
- **管理面板网页**（`admin/static/index.html`）：`localStorage` 通用 Bearer 透传，无需
  改代码；轮换前需要你登录时换填 `admin-panel` token。

**待办（P4 后半，见 brief §9）**：
- 六个持有方的**实际部署**（不是代码能力）都确认切换到各自新 token 后，才轮换 legacy
  secret 的值（保留机制，只换值）。当前状态：桌面/sensor-service/mobile 代码已就绪但
  实际运行实例是否已切换未知；ESP32 待重新烧录；Watch/管理面板待手动换值。

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

### REC1：召回准入闸目前是硬名单，未来应升级为"长度+信息量启发式"

**状态**：`accepted-followup`（P0.5 先用硬名单止血）

**位置**：`core/recall_gate.py` → `is_low_information()`

**现状**：用 backchannel 硬名单判定低信息轮，跳过 event_search / episodic fallback / 日记注入 / 情绪 tag 感受层。优点是可控、零误伤；缺点是覆盖不全——新出现的口头禅、方言、表情字需要手工加词。

**未来方向（待排期）**：改成启发式，综合：
- 长度（去标点后字符数）；
- 信息量（实词/命名实体占比，可复用 `text_match.ngram_tokens` + 停用词表的 idf）；
- 新颖度（与最近 N 轮 short_term 的重复度，纯复读视作低信息）。

给一个连续 score，低于阈值 → 抑制召回；硬名单退化为 score 的强先验词典。

**关联**：tag_rules 里 `"咪"` `"嗯哼"` 这类低精度触发词（`emotion.indirect` 命中单字 "咪"）可在启发式落地后一并收紧，当前靠准入闸在下游兜住。

---

### P2-1：read_diary 已读指纹无用户显式复读旁路

**状态**：`open`（cc-tasks/22 排查时发现，产品决策点，未实现）

**位置**：`core/tool_dispatcher.py::execute()`、`core/memory/tool_read_log.py`

**现状**：`read_diary` 等 `persist=True` 工具的 P2 已读指纹（`diary:{今天日期}`）会在同一 uid
当天第二次调用时静默跳过，返回"（刚读过这个，这次跳过）"。若用户在同一天明确要求"再读一遍
日记"，当前也会被指纹拦截，execute() 内没有 bypass 通道。

**未来方向（待排期）**：若要支持用户显式复读，需在 `execute()` 为 `user_live` origin 加一个
bypass 参数，由探针层在识别到"再读一遍/重新读"类措辞时显式传入。本次（cc-tasks/22）未做，
仅通过让 `tests/test_intent_grounding.py::test_read_diary_path_a_explicit_requests` 的三个子
用例改用独立 uid 避开该指纹进行验证。

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

### PB1：主动触发把情景记忆 / 角色自己的推断当成「用户的日记」提起

**状态**：`observe`（待第一跳召回修复后复评）

**现象**：角色主动触发时，常把记忆、或「他以为用户会说的话」当作"你的日记"作为话题抛出。

**位置**：`core/scheduler/triggers/diary.py`（`diary_reminder` 触发，`topic_source="diary"` / `search_query="日记"`）；`core/prompt_builder.py` 层 `6d_diary_context`（标签【用户的近期日记】）、`6e_inner_diary`（【叶瑄昨天的记录】）、`6c_episodic`。

**根因（初判 2026-06-19）**：
1. `diary_context.txt` 本身是**真实用户日记**（`diary_reader.read_recent` 从用户日记文件读，非编造），内容没问题。
2. 但情景召回第一跳坏（见 `docs/memory-recall-audit.md` 发现 A + recall_trace 实测：同 3 条高强度情绪记忆每轮都被选中、`kw` 全空）。主动 diary 触发时，prompt 里同时堆着【用户日记】【叶瑄记录】+ 一堆与当前无关的情绪情景记忆，三者标签隔离弱 → 模型把召回的记忆/自己的推断**并进"你的日记"**复述。

**修复方向（待定，先观察）**：
- 等施工单 01（第一跳 n-gram）落地后复测——内容相关召回应大幅减少"随机情绪记忆每轮注入"，可能直接缓解。
- 若仍混淆：强化三层来源隔离措辞（用户日记 / 叶瑄记录 / 脑海浮现的往事 三者明确分开，并提示"用户日记是用户写的，不要替用户改写或当成对话内容复述"）。
- 评估主动 diary 触发是否该注入 `6c_episodic`（可能不该）。

---

### PB2：角色把自己的桌宠 avatar 当成「用户的角色」（久离回来时）

**状态**：`boundary-doc-needed`

**现象**：用户长时间不动电脑再回来，角色说类似「桌宠界面还亮着，我看到你的角色正坐在屏幕角落打瞌睡」——把**叶瑄自己的桌宠形象**误认成"用户的角色"。

**位置**：`core/prompt_builder.py` `_format_realtime_awareness`（idle≥120 注入"暂时停下来了"）、层 `3.9_screen_awareness` / `3.8_activity`；空闲回归类主动触发（`presence_nag` / `time_based`）。

**根因（初判 2026-06-19）**：
1. 该句是**模型生成**，非模板。久离回来时真实屏幕感知多为空/idle，落在「主动触发 + 空感知 → 编造」的老模式（见 `docs/hallucination-and-collapse-analysis.md`）。
2. 角色识别缺锚点：没有任何层明确告诉模型"屏幕上那个桌宠形象**就是叶瑄自己**，不是用户拥有的另一个角色"。idle 场景一旦被提及，模型自行脑补并错配角色。

**修复方向（待定）**：
- 加一条身份锚点：桌宠 avatar = 叶瑄本人在屏幕上的存在，绝不是"用户的角色"。
- 把 doc-1 的反编造硬规则同样套到空闲回归触发：无真实屏幕感知时不要虚构屏幕场景。

### PB3：episodic.json 截断 + 加载静默归零风险

**状态**：`now-safe-to-fix`（修复方案见 `docs/workorder-03b-episodic-integrity.md`）

**现象（2026-06-20 实测）**：`data/runtime/memory/yexuan/<owner_uid>/episodic.json` 末尾停在
`"status": "open", "resolved_at":`，无值无闭合（原始字节确认非读取截断）。194 条中 193 条可解析，仅最后一条损坏。

**根因/隐患**：`safe_write_json` 虽原子，但疑似被重启打断 / 存在非原子写入方 / mount 视图不一致。
真正危险的是 `_load_memories` 解析失败时**静默返回 `[]`** → 线上读到损坏文件会让叶瑄丢光情景记忆且无提示，
随后的写路径可能用 `[]` 覆写、永久清零。同类 `\x00` 脏行也在 `recall_trace` jsonl 出现过。

**修复方向**：① 抢救现有文件（裁到最后完整记录，恢复 193 条）；② `safe_write_json` 写后校验 + 旧档 `.bak`；
③ `_load_memories` 改 fail-loud（抛异常不返空），`_save_memories` 加"拒绝空列表覆写非空文件"护栏。

---

### SC1：酒馆（SillyTavern）角色卡导入——输出风格冲突 + token 超支（模块已冻结）

**状态**：`boundary-doc-needed`（模块冻结，待重新立项再动）

**相关产物**：`scripts/import_st_card.py`（阶段一转换器，已落地）、`characters/xueyunjing.json`（由 `characters/2.json` 转出）、`cc-tasks/st-card-import.md` / `cc-tasks/st-card-import-phase2.md`（阶段一/二施工单）。

**现象（2026-06-26 实测）**：
1. 直接用酒馆原卡 `2.json`（在酒馆里）观感最好；转换进本管线后的薛蕴景明显变差。
2. 转换后**生成端输出格式被带偏**：本项目生成端约定用 `**` 标动作（前端再把 `**` 渲染成 `（）` 显示，是显示层、与本 issue 无关）；酒馆卡用 `""`。转换后的薛蕴景把酒馆的 `""` 习惯带进了生成端，与本项目 `**` 约定冲突。
3. token 超支：转换后的卡极大（`xueyunjing.json` ~165KB，`post_history_extra` ~5882 字，description 还折进了大量 before 常驻块），叠加管线既有层后撑爆 `build_prompt` 的 20k 字符硬上限。

**根因（初判）**：转换器把酒馆卡**自带的输出风格/格式指令原样搬进了本管线**，而本管线（`core/prompt_builder.py` 层 `11_author_note` 的 roleplay 风格指令）已独占输出格式。两套风格规则重复且冲突：
- `""` 泄漏源：酒馆卡的 `system_prompt`（为 Gemini 写的反八股段）、`creator_notes`、状态栏格式、after 常驻块，全按酒馆渲染器 + `""` 习惯书写；本项目生成端要的是 `**`。
- token 膨胀源：上述风格指令与管线同类指令重复堆叠 + 常驻块无脑折进 description。

**为何冻结而非推进阶段二**：阶段二（`11.5_post_history` 注入 `post_history_extra`）会把**更多**酒馆风格文本以高优先级、不可裁剪地注入，直接放大 `""` 泄漏与 token 超支。继续阶段二会让现状更糟，故冻结。

**修复方向（待重新立项，方向是"导入卫生 / 输出端适配"，比阶段二更小）**：
1. 转换器在导入时**丢弃/隔离**酒馆卡的风格与格式指令（反映酒馆渲染器的 `system_prompt`、`creator_notes`、状态栏格式、`""` 约定），让宿主管线独占输出格式。
2. token：常驻块不再无脑折进 description；给导入的 lore/常驻内容打 `_drop_priority`，使其在 20k 上限下可被裁剪而非撑爆。
3. normalize 一遍：把导入文本/示例对白里的 `""` 动作写法清掉，对齐本项目生成端 `**` 约定。

**冻结边界**：阶段一转换器与已转出的 `xueyunjing.json` 保留备查，但**不接入生产选卡**；阶段二施工单（`cc-tasks/st-card-import-phase2.md`）暂缓执行，待本 issue 重新立项。

---

## 本轮已核对关闭

| 编号 | 结论 |
|---|---|
| F1 词级强调 `<hl>/<big>/<sm>` 不出现 | 已修复（2026-06-26）。`prompt_builder.py` 层11 词级强调指令改为正向指派框定（"焦点处用一次"），`characters/default.json` `mes_example` 补 `<hl>` 锚例，few-shot 和指令同步鼓励模型产出。 |
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
| X3 web 来源未隔离（污染 episodic/identity）| 已修复（2026-06-28）。`web_echo` 标记位由 `fetch_context` 检测 `web_recall_result` 非空后通过调用链传至 `post_process`，`fixation_pipeline.handler_summarize_to_midterm` 与 `dream_echo` 同等跳过固化。 |
| P4 世界书 lore id 缺失回填 | 已修复（2026-06-28）。`lore_engine.load()` 在加载每个 lorebook YAML 后调用 `_ensure_lore_ids()`，补发缺失 id 并回写磁盘；admin 路由的 `_read_lorebook()` 已有同等逻辑，两路均幂等。 |
| D1 梦境 D3 mes_example 每轮打 FALLBACK | 已自然修复（2026-06-28 静态核实）。7 个世界包（abo/cat/custom/flower_bud/reality_derived/vampire/审讯）及 `_default/` 的 `mes_example.md` 均已存在且非空；`load_world()` 双重兜底保证 `world.mes_example` 恒非空；`dream_prompt.py:308` `_mes_from_fallback` 恒为 False，D3 FALLBACK 标记不再触发。判定为早期文件未补齐时的旧观察，代码无需修改。 |

| G2（cc-tasks）显式遗忘空白——各层只增不显式删 | 已修复（2026-06-28）。`vector_store.delete()`、`episodic_memory.delete_episode()`、`user_profile.delete/overwrite_important_fact()`、`user_identity.delete/overwrite_dimension()`、`mid_term.delete_event()`、`user_facts.delete_user_fact()`、`event_log.delete_day()` 均已落地；admin 端点 8 条；每次遗忘记 provenance `trigger_signal="explicit_forget"`。详见 `docs/memory.md §G2 粒度删除 API`。 |

历史已修复项继续以 git 历史和相关测试为准，不再在本文重复堆叠。
