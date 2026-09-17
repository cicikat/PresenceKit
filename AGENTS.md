# AGENTS.md — Codex / Claude Code 工作入口

> 开始任务时使用本文件的当前规则；上下文已有完整有效版本时无需重读，文件变化或上下文缺失时再读。根据实际影响面，只读对应专题的相关章节。
> Codex 默认读取本文件。`CODEX.md` 与 `CLAUDE.md` 仅保留协作入口；工程规则以本文件和任务对应专题文档为准，不重复维护架构副本。

---

## 项目定位

角色（PresenceKit）是一个单用户 AI 陪伴系统，通过 QQ、桌宠和手机轮询通道与用户交互。

---

## 代码根目录

仓库克隆目录（即本文件所在目录）。

---

## 任务 → 读哪个文档

| 实际涉及的任务类型 | 按需读取的文档或章节 |
|---|---|
| 理解系统全貌、pipeline 流程 | `ARCHITECTURE.md` |
| 改 Agent Runtime、长期任务、同一角色的 durable/specialized 副链工作会话、capability 适配或迁移 | `docs/agent-runtime-architecture.md`；再读对应 Brief 230-237。Agent Runtime 不是第二个 Agent，走 Work Session 只代表执行链不同 |
| 改记忆相关逻辑（episodic / user_identity / growth legacy / mood / event_log / fixation_pipeline / user_hidden_state） | `docs/memory.md` |
| 改 prompt 层结构、tag 规则、token 裁剪 | `docs/prompt-layers.md` |
| 改工具系统（新增工具、探针规则、桌面动作、execute() origin 闸门） | `docs/tools.md` |
| 改调度器（定时触发、主动消息） | `docs/scheduler.md` |
| 改 QQ / 桌宠通道、广播、WebSocket、跨通道接续 | `docs/channels.md`；三仓协议总账见 `docs/three-repo-interface-catalog.md`，桌面 v0.1 字段见 `Emerald-client/docs/protocol-v0.md` |
| 整理或修改三仓接口、跨端设置/观测、调用链 | `docs/three-repo-interface-catalog.md`；精确 REST schema 以 `/openapi.json` 为准 |
| 改多角色群聊、Stage session、共享 transcript、回合仲裁 | `docs/stage.md` |
| 改花园系统（情绪花槽、自动/被动浇水、采后处理、管理面板状态） | `docs/garden.md` |
| 理解事件/交互三维 envelope（realm/kind/lifecycle）、stimulus 边界、v0.1 约束 | `docs/interaction-event-model.md` |
| 修已知 bug / 查技术债 | `docs/known-issues.md` |
| 不确定设计意图、准入标准、禁止行为 | `DESIGN.md` |
| 改并发/锁/数据安全 | `docs/memory.md` → 七、并发保护 |
| 在 Codex / Claude Code Windows 环境运行测试、跨仓验证、处理沙箱报错 | `docs/dev-environment.md` |
| 改多模型接入、preset 路由、LLM provider 适配、prompt_style 转换 | `docs/model-presets.md` |
| 改鉴权/token/scope（`admin/auth.py`、`admin/scopes.py`、`admin/token_registry.py`） | `docs/security.md` |
| 改启动、关闭、生命周期或资源所有权 | `docs/runtime-lifecycle.md` 对应章节 |
| 改信任边界、执行权限或安全模式 | `docs/security_model.md` 对应章节；鉴权实现另见 `docs/security.md` |
| 改 ESP32 具身硬件固件（`firmware/presence-device/`） | `docs/presence-device-firmware.md`（协议/WS 通道侧见 `docs/channels.md`） |

---

## 关键文件速查

| 功能 | 文件 |
|---|---|
| 消息处理主流程 | `main.py` |
| Pipeline 四步骤 + tool loop（Brief 28 · Path C，`run_agentic_loop()`） | `core/pipeline.py` |
| Prompt 组装 | `core/prompt_builder.py` |
| Prompt 层级消融开关（对比/消融测试，只过滤注入不短路检索） | `core/prompt_ablation.py` |
| 话题标签规则 | `core/tag_rules.py` |
| 工具注册 + 调度 + 探针 | `core/tool_dispatcher.py` |
| Intiface / Buttplug 硬件控制 | `core/hardware/buttplug_client.py` / `core/hardware/device_registry.py` / `core/tools/hardware_tools.py` |
| 通道注册与广播 | `channels/registry.py` |
| 桌宠通道 WebSocket + 文件降级 | `channels/desktop_ws.py` / `channels/desktop.py`；跨仓接口总账 `docs/three-repo-interface-catalog.md`，桌面消息细节见 `Emerald-client/docs/protocol-v0.md` |
| 桌宠聊天 HTTP 入口 | `admin/routers/chat.py` → `/desktop/chat` |
| 手机通道 + 轮询接口 | `channels/mobile.py` / `admin/routers/mobile.py` |
| 统一 assistant turn sink | `core/turn_sink.py` |
| Agent Runtime（同一角色的 durable/specialized 副链：task / work session / workspace；不是第二个 Agent） | `core/agent_runtime/`；合同 `docs/agent-runtime-architecture.md` |
| 多端 owner 对话串行锁 | `core/conversation_gate.py` |
| 多角色 Stage session / 共享 transcript / 回合仲裁 | `core/stage/models.py` / `core/stage/store.py` / `core/stage/arbiter.py` / `core/stage/runner.py` |
| 群聊梦境（Dream Stage，Brief 100：仅 sandbox、零回流、hard_exit 绝对） | `core/stage/dream_runtime.py`（`run_dream_stage_turn()`）/ `core/stage/dream_views.py`（`DreamStageCharacterView`）/ `core/stage/dream_state.py` + `dream_store.py` + `dream_settings.py`；端点 `admin/routers/group_dream.py`；详见 `docs/stage.md` §六 |
| 情景记忆 | `core/memory/episodic_memory.py` |
| 查询侧时间意图解析（Brief 48：解析"上周/前天/N天前"等，供 episodic/event_log/向量预取按时间窗过滤召回，纯规则无 LLM） | `core/memory/temporal_query.py` → `parse_query_time_range()`；接线点 `core/pipeline.py::fetch_context()` |
| Memory Event shadow recall（Brief 204：默认关闭、按 uid/char 灰度；并行 reality 事件账本评估只写 recall trace，不进 prompt；观测 `/observability/memory-event-shadow-recall`） | `core/memory/event_shadow_recall.py` → `run_shadow_recall()`；接线点 `core/pipeline.py::fetch_context()` |
| Memory Event 历史迁移与可逆遗忘（Brief 205：dry-run 默认、离线备份后小批次可重入导入；墓碑不物理删证据） | `core/memory/event_migration.py` / `scripts/migrate_memory_events.py`；墓碑 `core/memory/event_store.py::tombstone_event()`；观测 `/observability/memory-event-migration` |
| 情景记忆淘汰暂存（遗忘=降级而非删除；上限裁剪批次存进 storyline_inbox，等周频聚合统一消费；原即时 digest 压缩已退役） | `core/memory/fixation_pipeline.py` → `handler_storyline_evicted_input()` |
| storyline 叙事弧层（append-only 存储 + 写API open_arc/append_node/set_arc_status；周频聚合 storyline_weekly；tagged 召回层 6h_storyline，Brief 80） | `core/memory/storyline.py` / `core/scheduler/triggers/storyline_weekly.py` |
| event_log 过期前抢救持久事实（age 27-29 天，产出走 important_facts 冲突裁决入口，不发言） | `core/scheduler/triggers/event_log_salvage.py` |
| 闲时整合 pass：episodic 存量近似重复合并（v1 零 LLM，复用写入时去重的同一相似度函数/阈值，核心记忆不参与，单轮上限10对）+ 向量库孤儿一致性核对（超阈值触发 rebuild） | `core/scheduler/triggers/memory_janitor.py` |
| 情绪状态 | `core/memory/mood_state.py` |
| 用户稳定行为模式 | `core/memory/user_identity.py` |
| 用户隐性状态 schema + primitives（Phase 3：apply_time_decay / reinforce_body_memory / consolidate_baselines 等已实现；source 类型守卫） | `core/memory/user_hidden_state.py` |
| 用户隐性状态 integrator（中期层 integrate_event/impression + Phase 3 长期层 integrate_body_cue*；TypeError 类型守卫；_assert_not_long_term；Brief 88：RealityEventType 扩至 5 类，get_trigger_counts() 观测计数） | `core/memory/user_hidden_state_integrator.py` |
| 用户隐性状态现实侧信号映射（Brief 88：对话侧五类事件判定 + body_memory cue 接线，挂 pipeline.post_process_slow） | `core/memory/user_hidden_state_reality_signals.py` |
| 用户隐性状态持久化（load / save 原子写入；load_dream_snapshot 只读 bucket 快照） | `core/memory/user_hidden_state_store.py` |
| 用户隐性状态衰减调度（12h decay tick + 7d consolidate tick，stamp_trigger，不发言；Brief 88：12h tick 内顺带 NO_INTERACTION 判定，逻辑日去重 stamp） | `core/scheduler/triggers/hidden_state_decay.py` |
| Dream snapshot 接入（Phase 4：tag-gated D4.5 只读注入；tag_gate helper；fail-closed） | `core/dream/dream_context.py` + `core/dream/dream_prompt.py` |
| Dream exit afterglow 回流接线（Phase 6：wire_afterglow_from_summary；tone 推导；fail-closed） | `core/dream/dream_exit_afterglow.py` |
| Reality prompt afterglow 软提示（Phase 7：_format_afterglow_soft_hint；只读；fail-closed；layer dream_afterglow_soft_hint） | `core/prompt_builder.py` → `_format_afterglow_soft_hint()` + `read_afterglow_residue()` |
| 调度器主循环 | `core/scheduler/loop.py` |
| 调度器状态机 / gating / proposer | `core/scheduler/state_machine.py` / `core/scheduler/gating.py` / `core/scheduler/proposer_registry.py` |
| 出梦主动开口触发器 | `core/scheduler/triggers/dream_exit.py` |
| 花园系统 | `core/garden/manager.py` / `core/garden/constants.py` |
| 花园工具 | `core/tools/garden_tools.py` / `core/tool_dispatcher.py` → `water_garden` |
| 花园调度器 | `core/scheduler/triggers/garden_water.py` / `core/scheduler/triggers/garden_daily.py` |
| 花园管理面板接口 | `admin/routers/garden.py` |
| 用户私有 authored 资产（贴纸/角色卡/reality/dream 素材；非 `data/`） | `core/data_paths.py`（`userdata_*` / fallback accessor）+ `core/asset_registry.py`；分类见 `docs/data-taxonomy.md` |
| 表情包输出（QQ 图片 + desktop/mobile sticker payload） | `core/output/sticker.py`；通道 payload 见 `docs/channels.md` |
| 媒体文件解析与落盘 | `core/media_processor.py` |
| 沙盒路径管理 | `core/sandbox.py` ← 所有 data/ 路径必须经过此处 |
| 管理面鉴权（scoped tokens，SEC-AUTH-2） | `admin/auth.py`（`resolve_token` / `require_scopes` / `authenticate_ws`）/ `admin/scopes.py`（scope+profile 表）/ `admin/token_registry.py`（token 加载/热重载/create/rotate/delete/set_disabled） |
| Token 管理 API（whoami/profiles/disable 等，DX Brief 22） | `admin/routers/auth_tokens.py` |
| 首次配置 CLI：生成 secret_key + 五个标准 token + 本地密码本（DX Brief 22） | `scripts/setup_auth.py`（见 `docs/token-rotation.md`） |
| ESP32 具身硬件固件（presence-device，非 `_achieve_Emerald-hello` 废弃测试项目） | `firmware/presence-device/src/ws_client.cpp`（WS 客户端+鉴权）/ `include/secrets.h`（gitignored，本地 token）/ `src/display.cpp`（渲染） |
| 从情景记忆提取用户观察（手动维护） | `tools/extract_observations.py` |
| 角色人设提醒轮换 | `core/author_note_rotator.py` |
| 情绪状态软提示生成 | `core/mood_text.py` |
| 安全写入工具（atomic write） | `core/safe_write.py` |
| LLM 多 preset adapter + 路由 | `core/llm_client.py` |
| Model registry（preset 构建、路由解析、参数合并）| `core/model_registry.py` |
| Prompt style 转换钩子（narrative / xml） | `core/prompt_style.py` |
| LLM输出校验与失败计数 | `core/llm_output_validator.py` |
| 外部 API 调用总账（按日轮转、只读查询） | `core/api_call_log.py` → `GET /observability/api-calls`（`state.read`） |
| Reality stimulus 审计查询 | `core/perceive_event.py` / `core/perceive_event_audit.py` → `GET /observability/perceive-events`（`state.read`） |
| 并发锁池 | `core/memory/locks.py` |
| 感知暂存（两阶段提交） | `core/memory/pending_perception.py` |
| 中期记忆 | `core/memory/mid_term.py` |
| 信息固化 pipeline（capture → mid_term → episodic → identity；growth handler legacy） | `core/memory/fixation_pipeline.py` |
| 元数据规则纠察 | `core/integrity_check.py` |
| user_identity 文件 | `data/user_identity/{uid}.yaml`（当前 prompt 层6a 主入口） |
| toy 自主写入（自生长，走 post_process，非探针） | `core/post_process/toy_autogrow.py` → `handler_toy_autogrow`；配置 `toy_autogrow:` |
| web 搜索沉淀（X3）：结果写入 vector_store source="web" | `core/tools/web_search.py` → `vector_store.upsert`；限频配置 `web_autosearch:` |
| web 资料召回（X3）：semantic 召回 web 来源，注入 `web_recall` 层 | `core/pipeline.py` `fetch_context()` → `vector_store.query_with_preview(sources=["web"])` → `web_recall_result` → `prompt_builder.build(web_recall_result=)` |
| web 与梦境来源同等隔离，不固化 | `web_recall_result` 非空时 `post_process` 携带 `web_echo=True`，`fixation_pipeline.handler_summarize_to_midterm` 与 dream_echo 同路跳过 mid_term/episodic/identity 写入；同一判定经 `event_log.append(source=)` 写入 event_log meta，`event_log_salvage` 抢救链按块过滤 `source:` 非空内容（Brief 79，见 `docs/memory.md` §三点八「来源隔离」） |
| 工具探针（声明式） | `core/tool_dispatcher.py` → `get_probe_prompt()` / `_TOOL_REGISTRY` |
| 工具已读指纹日志（P2，去重防重读） | `core/memory/tool_read_log.py`（`persist=True` 工具：read_diary / read_watch / read_toy_file / search_diary） |
| 工具动作痕迹（Brief 27，跨轮"你最近做过的操作"，层 `10.5_action_trace`） | `core/memory/action_trace.py`（`execute()` 收口埋点 + `event_log_echo` 经 `capture_turn` 回流） |
| trusted_user_text / probe grounding | `main.py` `_trusted_user_text` 在 media merge 前捕获；`admin/routers/chat.py` `run_owner_chat_turn(trusted_user_text=)` |
| execute() origin 闸门 | `core/tool_dispatcher.py` → `_EXECUTE_ALLOWED_ORIGINS`（`user_live` / `assistant_loop` / `assistant_loop_relay`） / `execute(origin=)` |
| tool loop 多步工具执行器（Path C，function_calling 模型专用） | `core/tool_dispatcher.py` → `tool_loop_active(uid)`；`core/pipeline.py` → `run_agentic_loop()`；全局 `config.tool_loop.enabled` 默认关，活跃角色卡可用 `presence_ext.tool_loop: "on"|"off"` 覆盖；设置接口 `admin/routers/settings_tool_loop.py` |
| MCP（Model Context Protocol）外部工具客户端（Brief 29 · 4，只接工具不接 resources/prompts/记忆库，默认关） | `core/mcp_client.py`（`init_mcp_servers()` / `shutdown_mcp_servers()`）；配置 `config.mcp_servers`；工具只经 tool loop 暴露 |
| per-char 兼容钩子（Brief 29 · "本我"模式：注入过滤/路由/发言闸门/工具暴露面） | 角色卡 `presence_ext` 块 → `core/character_loader.py`（解析 + `is_proactive_disabled()`）；消费点分别在 `core/prompt_ablation.py` / `core/model_registry.py` / `core/scheduler/gating.py`+`execution.py` / `core/pipeline.py::run_agentic_loop()`；示例卡 `bundled/examples/benwo.example.json`（发行内置模板/示例统一位于 `bundled/`） |
| speaker-aware history + 风格脱敏 | `core/memory/short_term.py` → `speaker_id` / `_group_turns()` / `_sanitize_assistant_message()` |

---

## 启动方式

```bash
# 正常模式（连接 NapCat）
python main.py

# 单机模式（只跑 HTTP，桌宠用）
# config.yaml: standalone_mode: true

# 测试模式（数据隔离，不污染生产）
python run_test.py
```

---

## 改代码前的强制规则

1. **所有 `data/` 路径必须通过 `core/sandbox.get_paths()` 获取，不得硬编码。**
2. **新增工具必须注册进 `_TOOL_REGISTRY`，并补充 `examples` 和 `keywords` 字段，探针 prompt 会自动同步。**
3. **新增 prompt 层必须加 `_layer` 字段，裁剪逻辑才能识别。**
4. **tag 规则改动后，用 `python tests/run_eval.py` 验证层激活情况。**
5. **改 assistant 消息写入或截断逻辑前，必须先看 `_sanitize_assistant_message`，避免绕过脱敏。**
6. **新增记忆写入点（identity / episodic / mid_term / trait / author_note）时，必须同步调用 `provenance_log.append()`（fail-open），否则改动无法追溯。详见 `docs/memory.md` §改动溯源。**
7. **新增需运营排障的持久运行状态、trace 或台账时，同单提供只读观测能力，可复用现有端点；scope 按数据敏感度选取。临时文件、测试产物和无需独立排障的可重建内部缓存不要求新增端点。**
8. WebSocket 客户端必须绕过系统代理。`websocket-client` 库会自动读取
   `HTTP_PROXY` / `HTTPS_PROXY` 环境变量，必须在 `run_forever` 调用前
   临时清除（连接结束后恢复）。`http_proxy_host=""` 这种参数不顶用。
9. **新代码禁止字面角色名/用户名。** 进入 LLM prompt 或展示给用户的文本用
   `char_name`（现实侧 `core.character_name_provider.get_char_name()` /
   梦境侧 `character.name`）与 `user_name`
   （`core.config_loader.get_user_display_name()`）插值，不写死"叶瑄"/"风谕"这类
   具体名字；路径默认参数用 `char_id: str = DEFAULT_CHAR_ID`
   （`from core.data_paths import DEFAULT_CHAR_ID`），不写死 `"yexuan"`。
   守门测试：`tests/test_no_hardcoded_character.py`（字面角色名/用户名 + 协议兼容
   字段白名单）、`tests/test_r3_scope_lint.py`（`char_id="yexuan"` 默认参数）。
10. **新增 send 前 await 时，先判断是否为回复生成所必需。** 非回复生成依赖的后处理 LLM/网络调用必须
   挪到 send 之后异步执行；生成回复必需的模型与工具调用不在此限（Brief 37 的教训：`detect_emotion` 曾经堵在
   `post_process` 里，每条消息多付一次 LLM 往返延迟）。`core/pipeline.py` 的
   `post_process_critical`（send 前，只做毫秒级本地落盘）/ `post_process_slow`
   （send 后，`asyncio.create_task` 调度，装 detect_emotion / mood_state /
   slow_queue 等）是这个原则落地的参考实现，见 `core/turn_sink.py`
   `record_assistant_turn`。
11. **禁止把真实密钥/token、真实 QQ 号或手机号、真实邮箱、本机绝对路径
    （`C:\Users\...`、`D:\ai\...` 等）写进任何会被 track 的文档或代码。**
    举例、排查记录、交接文档中需要引用敏感值时，用占位符或脱敏措辞（如
    "旧 admin token（已轮换）"），不留原始明文；确需记录本机路径时用
    `<用户目录>`/`<仓库路径>` 这类通用占位。commit 前如发现已写入，直接改掉
    再提交，不要留到事后清理。

## 测试

- 默认只运行任务相关测试；优先指定路径或使用 `pytest --testmon`，局部测试可按兼容性串行。
- 仅明确要求全量，或影响面与相关失败表明有必要时运行全量；全量必须用 `pytest -n auto`。
- 现有测试已覆盖时直接复用，覆盖不足才补测；纯文档或指令改动只做相应结构、链接与差异检查。
- 身份连续性场景 eval：`pytest -n auto tests/identity_eval/`；脚本入口：`python tests/run_identity_eval.py`


---

## 工作惯例

### 三仓 Android 正式签名交接

涉及 `Emerald-mobile`/`PresenceKit-mobile` 正式 APK、GitHub Release 或 release CI 重跑时，不要假设签名材料在 mobile
仓库内。先读 `docs/release-guide.md`，再检查 mobile 的 `android/key.properties`；如果配置或 keystore 不在当前仓库，
继续在本工作区的相邻仓库、发布文档和维护者指定的本地凭据/密码文件中寻找既有 keystore，并按 certificate SHA-256
确认它与历史 public APK 一致。若只缺少本地密码配置，应从 `android/key.properties.example` 创建 mobile 的本地
`android/key.properties`；若找不到历史 keystore，必须 fail-loud，不能生成新 key 或使用 debug key 代替。

keystore、`key.properties`、密码/凭据说明、base64 内容和 token 始终只能保留在 ignored 的本地文件或 GitHub Secrets，
不得写入受版本控制的文档、代码或 Release 资产。

**每张施工工单在相关测试通过、差异检查完成后，必须立即提交一次独立 Git commit，再开始下一张工单。**

**阶段规划时提出删除 brief 候选；本次未授权的清理不执行。** 只加法、不做减法会让测试从安全网
变成防腐层——迁移化石从不拆、legacy 分支越叠越厚。删除 brief 中，测试随功能一起
删除是合法且必须的：测试是跟随功能的，不是功能的遗产。删除必须连同其守卫、测试、
文档条目一起删——不留只测已删除代码的"僵尸测试"。（Brief 35 是第一个这样的删除
brief，遵循“删除必须连同守卫、测试和文档条目一起删除”的审计原则。）

---

## Windows Agent 验证须知

在当前会话首次运行测试或跨仓验证前，按需读 `docs/dev-environment.md` 的相关环境章节；已读且未变化时复用。测试范围统一遵循上面的“测试”规则。

## Windows 换行（必读，提交前核对）

仓库文本以 `.gitattributes` 为准：已跟踪文本 **LF**，`*.bat` / `*.cmd` **CRLF**。本机保持 `core.autocrlf=false`，不要用改 `autocrlf` 代替 gitattributes。守卫测试：`tests/test_line_endings.py`。

功能提交里不要把换行转换当格式化，也不要整文件重写只为「顺便收成 LF」。归一化已经做过（工单 254）；之后若再出现上千行假 diff，说明这次编辑把 `\r\n` 写回去了，应停下来修换行，而不是混进功能提交。

**提交前必须做：**

- 对每个将要 `git add` 的文件，对比 `git diff --stat -- path` 和 `git diff --ignore-cr-at-eol --stat -- path`。两者差很多 → **不要提交**，先处理换行。
- 只替换目标片段；不要用 HEAD 整文件 checkout/restore 覆盖他人未提交改动。
- 新文件用 LF（`.bat` / `.cmd` 除外）。`Path.write_text` 在 Windows 上会按 `os.linesep` 展开，写仓库文本时用 `newline="\n"` 或 `write_bytes`。

## Admin Static Asset Cache

每批影响管理面显示或运行时行为的静态修改，在交付前统一完成一次版本更新和浏览器验收；纯注释或非运行内容修改不触发。

1. 直接由 `admin/static/index.html` 加载的 JS/CSS 更新对应 `?v=`；修改 `admin/static/pages/` fragment 时，同时更新 `ADMIN_UI_FRAGMENT_VERSION` 与 `core.js` 的 `?v=`。
2. 启动或复用本地 admin 服务，清除受影响页面缓存并硬刷新；打开受影响页面，核对文字、控件、状态和相关交互。
3. 同一批修改无需逐补丁重复验收；验收后若又改动相关行为，重新验证受影响部分。
4. 若浏览器启动或连接被环境阻断，最终报告明确写“浏览器实测未完成”，并记录替代验证；源码或构建成功不能代替可见结果验收。

## Commands

```bash
# Run the bot (QQ + NapCat mode)
python main.py

# Run in standalone mode (HTTP only, no QQ)
# Set standalone_mode: true in config.yaml, then:
python main.py

# Test mode (data-isolated sandbox, won't touch production data/)
python run_test.py

# Test scope follows the Testing section above; full suite requires parallel execution
pytest -n auto
pytest --testmon                     # partial changes: only affected tests
pytest tests/test_short_term.py -v   # single file
python tests/run_eval.py             # validate prompt tag/layer activation after tag_rules changes
```

1. `python` 不在 PATH 或 `py.exe` 无可用解释器不代表项目失败。优先项目支持的 Python 3.10–3.12 环境（推荐 3.12）；本机遗留 3.14 仅作备用，其测试结果不代表受支持环境验收。解释器发现与备用入口见 `docs/dev-environment.md`，不要在入口文件重复维护本机环境快照。
2. pytest 默认临时目录可能因沙箱权限报 `PermissionError`；把 `TEMP` / `TMP` 临时指向仓库内 `.tmp`，测试后安全清理。
3. `PresenceKit-desktop`（当前目录名通常为 `Emerald-client`）的 Vite build 可能因沙箱禁止写 `node_modules/.vite-temp` 报 `EPERM`；应申请权限后原命令重跑。
4. 跨仓执行 git 时可能遇到 `dubious ownership`；优先按命令使用
   `git -c safe.directory=<Emerald-client 路径> ...`，不要擅自修改全局 git 配置。
5. 两个仓库经常存在其他 agent 的并行未提交改动。只改任务相关文件，完整测试失败时先判断是否与本任务相关，禁止顺手修复或回滚无关改动。


## 设置控制面文档

修改模型路由、TTS、scheduler、relay、thinking、tool loop 或高级功能时，仅当配置字段、默认值、effective state、权限或用户可见行为变化，才同步 `docs/feature-control-surface.md` 与实际受影响客户端的设置审计文档。内部重构且契约、行为不变时无需文档改动。

## 按影响面执行闭环检查

先判断本次改动影响后端管理面、桌面或手机中的哪些部分，只检查实际受影响的端与调用链。无跨端影响时在交付说明中简述理由即可，不为完成清单扩展到其他仓库。

1. 后端：涉及设置、默认值、effective state、权限或运营排障状态时，检查对应控制面与观测能力；持久状态遵循上面的观测规则。
2. 消费端：仅检查实际消费该功能的桌面/手机设置、能力、权限、降级与生命周期；配置字段存在不等于用户已有设置 UI。
3. 调用链：沿实际变化的触发器、router/pipeline、队列/WS、IPC/channel 与展示路径，检查涉及的 scope、字段、关联键、去重、ack、TTL、锁及 fallback；不机械展开未受影响的链路。
4. 验证：运行相关既有回归，覆盖不足才补最小测试。仅实际遗留缺陷或验收缺口记入 `docs/known-issues.md`；跨仓缺口同时记入 `docs/three-repo-interface-catalog.md`，标明 `open`/`roadmap`/`observe`。不适用项无需入账，不能把“接口存在”写成“功能完成”。

跨仓接口实际变化时，同步受影响仓库的接口文档和本总账。

## 控制面责任边界

后端功能开关、effective state、权限和观测入口以 `docs/feature-control-surface.md` 为权威；跨端设置归属与接入差异查 `docs/three-repo-doc-index.md` 和 `docs/three-repo-interface-catalog.md`。仅任务涉及这些边界时读取相关章节。
