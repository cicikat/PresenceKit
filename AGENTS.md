# AGENTS.md — Codex / Claude Code 工作入口

> 每次开始任务前必读此文件。根据任务类型，再按需读对应的详细文档。
> Codex 默认读取本文件。`CODEX.md` 是 `CLAUDE.md` 的兼容镜像，保留其协作偏好；若两者与本文件的当前工程约束冲突，以本文件和任务对应专题文档为准。

---

## 项目定位

角色（PresenceKit）是一个单用户 AI 陪伴系统，通过 QQ、桌宠和手机轮询通道与用户交互。

---

## 代码根目录

仓库克隆目录（即本文件所在目录）。

---

## 任务 → 读哪个文档

| 任务类型 | 必读文档 |
|---|---|
| 理解系统全貌、pipeline 流程 | `ARCHITECTURE.md` |
| 改记忆相关逻辑（episodic / user_identity / growth legacy / mood / event_log / fixation_pipeline / user_hidden_state） | `docs/memory.md` |
| 改 prompt 层结构、tag 规则、token 裁剪 | `docs/prompt-layers.md` |
| 改工具系统（新增工具、探针规则、桌面动作、execute() origin 闸门、Path B 守卫） | `docs/tools.md` |
| 改调度器（定时触发、主动消息） | `docs/scheduler.md` |
| 改 QQ / 桌宠通道、广播、WebSocket、跨通道接续 | `docs/channels.md`；桌面 v0.1 协议入口见 `docs/desktop-client-protocol.md` |
| 改多角色群聊、Stage session、共享 transcript、回合仲裁 | `docs/stage.md` |
| 改花园系统（情绪花槽、自动/被动浇水、采后处理、管理面板状态） | `docs/garden.md` |
| 理解事件/交互三维 envelope（realm/kind/lifecycle）、stimulus 边界、v0.1 约束 | `docs/interaction-event-model.md` |
| 修已知 bug / 查技术债 | `docs/known-issues.md` |
| 不确定设计意图、准入标准、禁止行为 | `DESIGN.md` |
| 改并发/锁/数据安全 | `docs/memory.md` → 七、并发保护 |
| 在 Codex / Claude Code Windows 环境运行测试、跨仓验证、处理沙箱报错 | `docs/dev-environment.md` |
| 改多模型接入、preset 路由、LLM provider 适配、prompt_style 转换 | `docs/model-presets.md` |
| 改鉴权/token/scope（`admin/auth.py`、`admin/scopes.py`、`admin/token_registry.py`） | `docs/security.md` |
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
| 桌宠通道 WebSocket + 文件降级 | `channels/desktop_ws.py` / `channels/desktop.py`；协议权威指针 `docs/desktop-client-protocol.md` |
| 桌宠聊天 HTTP 入口 | `admin/routers/chat.py` → `/desktop/chat` |
| 手机通道 + 轮询接口 | `channels/mobile.py` / `admin/routers/mobile.py` |
| 统一 assistant turn sink | `core/turn_sink.py` |
| 多端 owner 对话串行锁 | `core/conversation_gate.py` |
| 多角色 Stage session / 共享 transcript / 回合仲裁 | `core/stage/models.py` / `core/stage/store.py` / `core/stage/arbiter.py` / `core/stage/runner.py` |
| 情景记忆 | `core/memory/episodic_memory.py` |
| 查询侧时间意图解析（Brief 48：解析"上周/前天/N天前"等，供 episodic/event_log/向量预取按时间窗过滤召回，纯规则无 LLM） | `core/memory/temporal_query.py` → `parse_query_time_range()`；接线点 `core/pipeline.py::fetch_context()` |
| 情景记忆淘汰归档（遗忘=降级而非删除；上限裁剪批次压缩成"时期摘要"，v1 不进 prompt） | `core/memory/fixation_pipeline.py` → `digest_evicted_episodes()` / `handler_digest_evicted_episodes()` |
| event_log 过期前抢救持久事实（age 27-29 天，产出走 important_facts 冲突裁决入口，不发言） | `core/scheduler/triggers/event_log_salvage.py` |
| 闲时整合 pass：episodic 存量近似重复合并（v1 零 LLM，复用写入时去重的同一相似度函数/阈值，核心记忆不参与，单轮上限10对）+ 向量库孤儿一致性核对（超阈值触发 rebuild） | `core/scheduler/triggers/memory_janitor.py` |
| 情绪状态 | `core/memory/mood_state.py` |
| 用户稳定行为模式 | `core/memory/user_identity.py` |
| 用户隐性状态 schema + primitives（Phase 3：apply_time_decay / reinforce_body_memory / consolidate_baselines 等已实现；source 类型守卫） | `core/memory/user_hidden_state.py` |
| 用户隐性状态 integrator（中期层 integrate_event/impression + Phase 3 长期层 integrate_body_cue*；TypeError 类型守卫；_assert_not_long_term） | `core/memory/user_hidden_state_integrator.py` |
| 用户隐性状态持久化（load / save 原子写入；load_dream_snapshot 只读 bucket 快照） | `core/memory/user_hidden_state_store.py` |
| 用户隐性状态衰减调度（12h decay tick + 7d consolidate tick，stamp_trigger，不发言） | `core/scheduler/triggers/hidden_state_decay.py` |
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
| 并发锁池 | `core/memory/locks.py` |
| 感知暂存（两阶段提交） | `core/memory/pending_perception.py` |
| 中期记忆 | `core/memory/mid_term.py` |
| 信息固化 pipeline（capture → mid_term → episodic → identity；growth handler legacy） | `core/memory/fixation_pipeline.py` |
| 元数据规则纠察 | `core/integrity_check.py` |
| user_identity 文件 | `data/user_identity/{uid}.yaml`（当前 prompt 层6a 主入口） |
| toy 自主写入（自生长，走 post_process，非探针） | `core/post_process/toy_autogrow.py` → `handler_toy_autogrow`；配置 `toy_autogrow:` |
| web 搜索沉淀（X3）：结果写入 vector_store source="web" | `core/tools/web_search.py` → `vector_store.upsert`；限频配置 `web_autosearch:` |
| web 资料召回（X3）：semantic 召回 web 来源，注入 `web_recall` 层 | `core/pipeline.py` `fetch_context()` → `vector_store.query_with_preview(sources=["web"])` → `web_recall_result` → `prompt_builder.build(web_recall_result=)` |
| web 与梦境来源同等隔离，不固化 | `web_recall_result` 非空时 `post_process` 携带 `web_echo=True`，`fixation_pipeline.handler_summarize_to_midterm` 与 dream_echo 同路跳过 mid_term/episodic/identity 写入 |
| 工具探针（声明式） | `core/tool_dispatcher.py` → `get_probe_prompt()` / `_TOOL_REGISTRY` |
| 工具已读指纹日志（P2，去重防重读） | `core/memory/tool_read_log.py`（`persist=True` 工具：read_diary / read_watch / read_toy_file / search_diary） |
| 工具动作痕迹（Brief 27，跨轮"你最近做过的操作"，层 `10.5_action_trace`） | `core/memory/action_trace.py`（`execute()` 收口埋点 + `event_log_echo` 经 `capture_turn` 回流） |
| trusted_user_text / probe grounding | `main.py` `_trusted_user_text` 在 media merge 前捕获；`admin/routers/chat.py` `run_owner_chat_turn(trusted_user_text=)` |
| execute() origin 闸门 | `core/tool_dispatcher.py` → `_EXECUTE_ALLOWED_ORIGINS`（`user_live` / `assistant_intent` / `assistant_loop`） / `execute(origin=)` |
| Path B 守卫（意图反射去重） | `core/pipeline.py` → `_parse_and_execute_intent()` guards (a/b/c/d) + `_INTENT_LAST_ACTION` c2 幂等；guard (d) 为 `loop_executed=True` 时短路 |
| tool loop 多步工具执行器（Brief 28 · Path C，function_calling 模型专用，默认关） | `core/tool_dispatcher.py` → `tool_loop_active(uid)`；`core/pipeline.py` → `run_agentic_loop()`；`core/llm_client.py` → `chat_turn()`；配置 `config.tool_loop`；设置接口 `admin/routers/settings_tool_loop.py` |
| MCP（Model Context Protocol）外部工具客户端（Brief 29 · 4，只接工具不接 resources/prompts/记忆库，默认关） | `core/mcp_client.py`（`init_mcp_servers()` / `shutdown_mcp_servers()`）；配置 `config.mcp_servers`；工具只经 tool loop 暴露 |
| per-char 兼容钩子（Brief 29 · "本我"模式：注入过滤/路由/发言闸门/工具暴露面） | 角色卡 `presence_ext` 块 → `core/character_loader.py`（解析 + `is_proactive_disabled()`）；消费点分别在 `core/prompt_ablation.py` / `core/model_registry.py` / `core/scheduler/gating.py`+`execution.py` / `core/pipeline.py::run_agentic_loop()`；示例卡 `examples/benwo.example.json`（`characters/` 根目录不放模板/示例文件，见 `tests/test_authored_assets.py`） |
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
7. **新增落盘状态、trace 或台账时，必须同单提供只读观测端点；scope 按数据敏感度选取。没有观测端点的落盘物不可验收。**
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
10. **任何要 await 进 send/关键路径的调用，先问它是不是 LLM/网络往返。** 是的话必须
   挪到 send 之后异步执行（Brief 37 的教训：`detect_emotion` 曾经堵在
   `post_process` 里，每条消息多付一次 LLM 往返延迟）。`core/pipeline.py` 的
   `post_process_critical`（send 前，只做毫秒级本地落盘）/ `post_process_slow`
   （send 后，`asyncio.create_task` 调度，装 detect_emotion / mood_state /
   slow_queue 等）是这个原则落地的参考实现，见 `core/turn_sink.py`
   `record_assistant_turn`。

   ## 测试（新增）
- 跑测试用 `pytest -n auto`,不要用不带 -n 的全量单进程跑法
- 只改了部分代码时优先用 `pytest --testmon` 或指定路径跑相关测试,避免每次全量


---

## 工作惯例

**每张施工工单在相关测试通过、差异检查完成后，必须立即提交一次独立 Git commit，再开始下一张工单。**

**每积累若干个功能 brief，安排一个删除 brief。** 只加法、不做减法会让测试从安全网
变成防腐层——迁移化石从不拆、legacy 分支越叠越厚。删除 brief 中，测试随功能一起
删除是合法且必须的：测试是跟随功能的，不是功能的遗产。删除必须连同其守卫、测试、
文档条目一起删——不留只测已删除代码的"僵尸测试"。（Brief 35 是第一个这样的删除
brief，遵循“删除必须连同守卫、测试和文档条目一起删除”的审计原则。）

---

## Windows Agent 验证须知

在 Codex / Claude Code 环境里运行测试或跨仓修改前，**必须先读
`docs/dev-environment.md`**。特别注意：

1. `python` 可能不在 `PATH`，`py.exe` 也可能存在但没有已安装解释器；不要把这误判为项目失败。
2. pytest 默认临时目录可能因沙箱权限报 `PermissionError`；把 `TEMP` / `TMP` 临时指向仓库内 `.tmp`，测试后安全清理。
3. `PresenceKit-desktop`（当前目录名通常为 `Emerald-client`）的 Vite build 可能因沙箱禁止写 `node_modules/.vite-temp` 报 `EPERM`；应申请权限后原命令重跑。
4. 跨仓执行 git 时可能遇到 `dubious ownership`；优先按命令使用
   `git -c safe.directory=<Emerald-client 路径> ...`，不要擅自修改全局 git 配置。
5. 两个仓库经常存在其他 agent 的并行未提交改动。只改任务相关文件，完整测试失败时先判断是否与本任务相关，禁止顺手修复或回滚无关改动。
