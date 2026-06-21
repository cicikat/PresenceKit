# AGENTS.md — Claude Code 工作入口

> 每次开始任务前必读此文件。根据任务类型，再按需读对应的详细文档。

---

## 项目定位

角色（qq-st-bot）是一个单用户 AI 陪伴系统，通过 QQ、桌宠和手机轮询通道与用户交互。

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
| 改 QQ / 桌宠通道、广播、WebSocket、跨通道接续 | `docs/channels.md` |
| 改多角色群聊、Stage session、共享 transcript、回合仲裁 | `docs/stage.md` |
| 改花园系统（情绪花槽、自动/被动浇水、采后处理、管理面板状态） | `docs/garden.md` |
| 理解事件/交互三维 envelope（realm/kind/lifecycle）、stimulus 边界、v0.1 约束 | `docs/interaction-event-model.md` |
| 修已知 bug / 查技术债 | `docs/known-issues.md` |
| 不确定设计意图、准入标准、禁止行为 | `DESIGN.md` |
| 改并发/锁/数据安全 | `docs/memory.md` → 七、并发保护 |
| 在 Codex / Claude Code Windows 环境运行测试、跨仓验证、处理沙箱报错 | `docs/dev-environment.md` |
| 改多模型接入、preset 路由、LLM provider 适配、prompt_style 转换 | `docs/model-presets.md` |

---

## 关键文件速查

| 功能 | 文件 |
|---|---|
| 消息处理主流程 | `main.py` |
| Pipeline 四步骤 | `core/pipeline.py` |
| Prompt 组装 | `core/prompt_builder.py` |
| 话题标签规则 | `core/tag_rules.py` |
| 工具注册 + 调度 + 探针 | `core/tool_dispatcher.py` |
| Intiface / Buttplug 硬件控制 | `core/hardware/buttplug_client.py` / `core/hardware/device_registry.py` / `core/tools/hardware_tools.py` |
| 通道注册与广播 | `channels/registry.py` |
| 桌宠通道 WebSocket + 文件降级 | `channels/desktop_ws.py` / `channels/desktop.py` |
| 桌宠聊天 HTTP 入口 | `admin/routers/chat.py` → `/desktop/chat` |
| 手机通道 + 轮询接口 | `channels/mobile.py` / `admin/routers/mobile.py` |
| 统一 assistant turn sink | `core/turn_sink.py` |
| 多端 owner 对话串行锁 | `core/conversation_gate.py` |
| 多角色 Stage session / 共享 transcript / 回合仲裁 | `core/stage/models.py` / `core/stage/store.py` / `core/stage/arbiter.py` / `core/stage/runner.py` |
| 情景记忆 | `core/memory/episodic_memory.py` |
| 情绪状态 | `core/memory/mood_state.py` |
| 用户稳定行为模式 | `core/memory/user_identity.py` |
| 用户隐性状态 schema + primitives（Phase 3：apply_time_decay / reinforce_body_memory / consolidate_baselines 等已实现；source 类型守卫） | `core/memory/user_hidden_state.py` |
| 用户隐性状态 integrator（中期层 integrate_event/impression + Phase 3 长期层 integrate_body_cue*；TypeError 类型守卫；_assert_not_long_term） | `core/memory/user_hidden_state_integrator.py` |
| 用户隐性状态持久化（load / save 原子写入；load_dream_snapshot 只读 bucket 快照） | `core/memory/user_hidden_state_store.py` |
| 用户隐性状态衰减调度（12h decay tick + 7d consolidate tick，stamp_trigger，不发言） | `core/scheduler/triggers/hidden_state_decay.py` |
| Dream snapshot 接入（Phase 4：tag-gated D4.5 只读注入；tag_gate helper；fail-closed） | `core/dream/dream_context.py` + `core/dream/dream_prompt.py` |
| Dream exit afterglow 回流接线（Phase 6：wire_afterglow_from_summary；tone 推导；fail-closed） | `core/dream/dream_exit_afterglow.py` |
| Reality prompt afterglow 软提示（Phase 7：_format_afterglow_soft_hint；只读；fail-closed；layer dream_afterglow_soft_hint） | `core/prompt_builder.py` → `_format_afterglow_soft_hint()` + `read_afterglow_residue()` |
| 角色认知（legacy/兼容） | `core/memory/character_growth.py` |
| 调度器主循环 | `core/scheduler/loop.py` |
| 调度器状态机 / gating / proposer | `core/scheduler/state_machine.py` / `core/scheduler/gating.py` / `core/scheduler/proposer_registry.py` |
| 出梦主动开口触发器 | `core/scheduler/triggers/dream_exit.py` |
| 花园系统 | `core/garden/manager.py` / `core/garden/constants.py` |
| 花园工具 | `core/tools/garden_tools.py` / `core/tool_dispatcher.py` → `water_garden` |
| 花园调度器 | `core/scheduler/triggers/garden_water.py` / `core/scheduler/triggers/garden_daily.py` |
| 花园管理面板接口 | `admin/routers/garden.py` |
| 媒体文件解析与落盘 | `core/media_processor.py` |
| 沙盒路径管理 | `core/sandbox.py` ← 所有 data/ 路径必须经过此处 |
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
| character_growth 三文件（legacy） | `角色_{uid}.md`（observer源）/ `.fingerprint.txt`（派生，存前150字）/ `.felt.md`（派生；当前主 prompt 不注入） |
| 工具探针（声明式） | `core/tool_dispatcher.py` → `get_probe_prompt()` / `_TOOL_REGISTRY` |
| trusted_user_text / probe grounding | `main.py` `_trusted_user_text` 在 media merge 前捕获；`admin/routers/chat.py` `run_owner_chat_turn(trusted_user_text=)` |
| execute() origin 闸门 | `core/tool_dispatcher.py` → `_EXECUTE_ALLOWED_ORIGINS` / `execute(origin=)` |
| Path B 守卫（意图反射去重） | `core/pipeline.py` → `_parse_and_execute_intent()` guards (a/b/c) + `_INTENT_LAST_ACTION` c2 幂等 |
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
6. WebSocket 客户端必须绕过系统代理。`websocket-client` 库会自动读取
   `HTTP_PROXY` / `HTTPS_PROXY` 环境变量，必须在 `run_forever` 调用前
   临时清除（连接结束后恢复）。`http_proxy_host=""` 这种参数不顶用。

---

## Windows Agent 验证须知

在 Codex / Claude Code 环境里运行测试或跨仓修改前，**必须先读
`docs/dev-environment.md`**。特别注意：

1. `python` 可能不在 `PATH`，`py.exe` 也可能存在但没有已安装解释器；不要把这误判为项目失败。
2. pytest 默认临时目录可能因沙箱权限报 `PermissionError`；把 `TEMP` / `TMP` 临时指向仓库内 `.tmp`，测试后安全清理。
3. `Emerald-client` 的 Vite build 可能因沙箱禁止写 `node_modules/.vite-temp` 报 `EPERM`；应申请权限后原命令重跑。
4. 跨仓执行 git 时可能遇到 `dubious ownership`；优先按命令使用
   `git -c safe.directory=<Emerald-client 路径> ...`，不要擅自修改全局 git 配置。
5. 两个仓库经常存在其他 agent 的并行未提交改动。只改任务相关文件，完整测试失败时先判断是否与本任务相关，禁止顺手修复或回滚无关改动。
