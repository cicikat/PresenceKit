# T3：删除 Pipeline 已迁移职责的兼容入口

基线：`9c72ee01fcf02c4f08918e6d345d22fc6950dcd5`。T2/T2.1 已合并且合并检查通过。
本单不启动 T4–T9 或 HINT，不部署、不迁移生产数据；交付后等待用户协调的独立审查。

## 1. 当前调用图与唯一所有者

```text
main._init_modules
  ├─ Pipeline：角色/世界书生命周期、prompt 组装、模型调用与输出治理的实际代码
  │    ├─ context = RealityContextReader(runtime)
  │    ├─ tools = ToolTurnRunner(generator)
  │    └─ effects = TurnEffects(runtime)
  ├─ pipeline_registry.register(runtime)：唯一进程级实例引用
  └─ post_process.handlers.register_slow_handlers(runtime)：显式绑定同一实例

QQ / owner-chat / scheduler / Stage / dream reality-continuation
  → context.fetch（冻结 scope 后读取）
  → build_prompt
  → run_llm / run_llm_stream 或 tools.run
  → turn_sink.record_assistant_turn
      → effects.capture_critical → capture_turn → 角色隔离记忆桶
      → fanout
      → effects.run_deferred_effects（发送后异步调度）
```

| 原入口 / 实际调用者 | 当前所有者与变化 |
|---|---|
| Pipeline.fetch_context；main、owner_chat.prepare、scheduler 两处、Stage view、dream.reality_continuation | `core.memory.reality_context.RealityContextReader.fetch` 持有完整原检索实现，显式接收运行实例；原转发方法删除 |
| Pipeline.run_agentic_loop；main、owner_chat.generation 流式/非流式、scheduler 工具任务 | `core.owner_chat.tool_loop.ToolTurnRunner.run` 持有完整工具回合实现；display_chain 和 scoped_tool_turn 在此处唯一应用；原转发方法删除 |
| Pipeline.post_process_critical / post_process_slow；turn_sink | `core.post_process.turn_effects.TurnEffects.capture_critical / run_deferred_effects` 持有实际实现；turn_sink 直接调用，原两个转发方法删除 |
| Pipeline.post_process 组合旧入口；turn_sink 非关键 capture 分支 | 全仓没有非关键 capture 模式的生产调用者，删除组合方法和 await_critical_post_process 参数/兼容分支；始终关键落盘后发送 |
| Pipeline._send_tts、梦境来源判定、avatar 投影 | 实现迁入 turn_effects；avatar 从显式注入的运行实例检查当前角色，不反查 registry |
| Pipeline 模块 slow handlers / register_slow_handlers | 实现迁入 `core.post_process.handlers`；main 注入实例，consistency handler 不再自行定位全局实例 |
| 未注册且无调用者的 _do_compress_episode / _validate_episode | 联删死实现和三个仅服务旧 validator 的测试；当前 fixation_pipeline 的验证、压缩和固化链不变 |

三个领域组件持有其实际算法，不是转调旧方法的 adapter/facade；旧方法、旧函数名别名和转发实现没有保留。
组件共享运行实例的角色/世界书引用，不另存一套角色状态、记忆 scope 或 Pipeline。

## 2. Pipeline 保留范围与 registry 决策

本单缩小 Pipeline，而非删除仍有真实职责的运行实例。保留的方法为：

- 构造与角色/世界书热切换、`_current_reality_scope`；
- `build_prompt` 的实际组装、pending perception、跨通道接续和 author note 消费；
- `run_llm` / `run_llm_stream` 及完成声明、反坍缩、工具协议泄漏等实际生成治理。

这些职责尚未在其他模块存在另一份实现。它们保留实际代码，不是为了旧调用而保留转发门面。
不把上述不同职责混入新的通用 service.py，也不改变生成顺序或模型重试语义。

生产 Pipeline 创建点：main._init_modules。`core.recovery_validation` 还会构建独立离线校验实例，
不注册为生产实例，不构成第二个运行系统。tests 下的 eval/benchmark 创建点属于各自隔离测试。

registry 保留为单一、进程生命周期的类型明确的实例引用：没有任意 service key、命名查找、
工厂、动态重建或第二份状态。其唯一生产注册者是 main；现有 HTTP、通道和后台入口需要取得
同一运行实例，移成另一份全局 app.state/模块变量不会消除这个生命周期需求。
它仍承担这些进程入口的全局实例定位；本单保留此窄边界，不声称消除了所有全局查找。
本单不盲目改写这些入口的启动管理。进入回合后，OwnerChatTurn、Stage view、turn_sink 和
三个领域组件均显式持有/传递依赖；slow handler 与 avatar 的既有 owner 已明确，因此改为显式注入。

完整生产读取/注册清单由 AST 检查导入别名与 get/register 调用得出，列于文末。

## 3. 语义保持和删除项

- ingress 冻结的 scope 仍贯穿检索、prompt、工具和记忆；非 active 角色读取仍使用独立 scoped card/lore。
- owner conversation lock、工具授权、execute_structured origin 和消息脱敏入口不变。
- critical 仍只做本地捕获和必要状态快照；LLM、情绪、画像、TTS 和慢队列仍在发送后。
- pending perception 仍在成功关键捕获后确认；跨通道/作者 note 的消费时机不变。
- 旧 DLQ payload 的既有 scope 解析兼容原样迁入 handlers，不扩大 fallback；后续数据/兼容切断仍归对应工单。
- 没有新增功能开关、运营状态、持久 trace、后台 worker、HTTP/WS 字段或客户端协议。

删除旧路径时联删其专属测试：非关键 capture 的两个组合分支用例；未使用 validator 的三个用例；
四个仅检查旧 Pipeline 调用字符串位置的断言。真实行为覆盖分别保留在 turn_sink 的慢段调度测试、
当前 fixation/episodic 测试、post_process_ordering 和新增 sink 入口集成测试中。
新增 sink 测试同时断言 frozen 角色桶实际写入、另一角色桶为空、pending 文件删除和 target 传递。
现有测试中的替身和 monkeypatch 随真实领域 API 迁移，业务断言不放宽。

## 4. 验证证据

Windows / Python 3.12.13；公共角色资产和隔离可写数据目录，不用生产记忆测试。

| 验证 | 结果 |
|---|---|
| 开工检索/工具/sink/后处理顺序基线 | 138 passed |
| 切断后同组 | 136 passed；少两项为已删除的非关键 capture 组合模式 |
| 首轮并行全量 | 8892 passed、32 failed、13 subtests passed；失败均为旧 fixture/API 位置绑定，未据此放宽断言 |
| 最终本地并行全量 | 8921 passed、13 subtests passed，无失败 |
| 最终 Smoke | 410 passed，8512 deselected，无失败 |
| 失败相关文件与新 sink 集成测试 | 165 passed；同组恢复空回复、流式失败、scope、来源隔离、pending 和 QQ metadata 覆盖 |
| 独立架构门禁 | 157 passed；新增 T3 断言先在旧五个方法上失败，再验证旧定义和生产访问均为零 |
| 真实模型对话 | 公共测试角色经 owner-chat.prepare → 真实模型 → turn_sink，非空回复，隔离 history 仅 user/assistant 两条；未投递真实 QQ/设备 |
| 实际初始化与 HTTP 启动 | main._init_modules 创建实例并注册 slow handlers；Admin startup/shutdown 完成，OpenAPI 200、501 path，未授权 Session 请求 401，无启动警告 |
| HTTP 完整快照 | 相同公开配置下，OpenAPI 完整 JSON、614 条 HTTP route 及其注册顺序与迁移前快照一致；不同测试默认角色会影响五个配置派生 schema default，因此比较前统一配置 |

最终并行全量、Smoke 和当前提交远端双版本 CI 的最终结果记录于审查 PR，不以旧 head 检查替代。
真实模型验收是一轮文本对话，不代表真实外部工具、设备、TTS、长时性能或完整客户端验收。
无 Admin 静态改动或跨端契约变化，无需缓存版本更新和客户端构建；没有生产部署或数据备份/迁移。

## 5. 生产 registry 读取与注册清单

| 源文件 | 所在函数 / 操作 |
|---|---|
| `admin/routers/chat.py` | `_ingest_chat_upload` get；`_ingest_chat_upload` get |
| `admin/routers/coplay.py` | `_active_char_id` get |
| `admin/routers/dream_common.py` | `_active_dream_char_id` get |
| `admin/routers/dream_rpg.py` | `_char_id` get |
| `admin/routers/dream_sessions.py` | `dream_enter` get；`dream_chat` get |
| `admin/routers/lorebook.py` | `_reload_lore_engine` get |
| `admin/routers/observability.py` | `dream_settings_observability` get；`character_file_autonomy_observability` get |
| `admin/routers/settings_mcp.py` | `_current_mcp_exposure_names` get |
| `admin/routers/settings_prompt_assets.py` | `_reload_lore_engine` get；`patch_prompt_assets` get |
| `channels/qq.py` | `send` get |
| `core/agent_runtime/computer_use.py` | `principal` get |
| `core/autonomy/effective_state.py` | `build_effective_state` get |
| `core/autonomy/runner.py` | `_character_for` get |
| `core/autonomy/talk_gate.py` | `send` get |
| `core/character_cleanup.py` | `_active_reasons` get |
| `core/character_loader.py` | `_active_character_id` get；`is_proactive_disabled` get；`reload_active_character` get |
| `core/character_name_provider.py` | `get_char_name` get；`get_char_pronoun` get |
| `core/companion/service.py` | `_runtime_context` get；`observability` get |
| `core/control_center/effective_state.py` | `_tool_loop_row` get |
| `core/coplay/device_collection.py` | `_active` get |
| `core/dream/dream_pipeline.py` | `dream_turn` get；`enter_dream` get；`_generate_retention_line` get |
| `core/dream/reality_continuation.py` | `_run_once` get |
| `core/memory/health_state.py` | `_resolve_character` get |
| `core/model_registry.py` | `_active_char_model_routing` get |
| `core/owner_chat/service.py` | `run_owner_chat_turn` get |
| `core/perception/phone_ambient.py` | `source_active` get |
| `core/prompt_ablation.py` | `_active_char_disabled_layers` get |
| `core/resource_completeness.py` | `_check_active_character_assets` get |
| `core/scheduler/loop.py` | `_char_name` get；`_pipeline_send` get；`_compose_trigger_reply` get |
| `core/scheduler/triggers/coplay_watch.py` | `_check_coplay_watch` get |
| `core/scheduler/triggers/festival.py` | `_active_character_card` get |
| `core/session_scope.py` | `owner_media_scope` get |
| `core/thinking_voice.py` | `preview` get |
| `core/tool_runtime/context.py` | `_active_char_id` get |
| `core/tool_runtime/policy.py` | `_effective_tool_loop_enabled` get |
| `core/turn_sink.py` | `_require_pipeline` get |
| `main.py` | `_init_modules` register |

