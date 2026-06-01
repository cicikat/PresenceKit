# docs/system-readiness.md — 系统可承载性盘点

> 只读盘点记录。目标是标出当前主干上哪些模块适合继续承载、哪些只适合小心维护，以及最小补强点。本文不提出新功能，不替代 `ARCHITECTURE.md`。

---

## 1. 调度器 / 主动消息

**当前状态**：半稳定

**入口与调用方**
- 主循环：`core/scheduler/loop.py` → `_loop()`
- 启动入口：`main.py` → `main()` 调 `scheduler.start()`
- 真实发送入口：`core/scheduler/loop.py` → `_pipeline_send()`
- 状态机入口：`core/scheduler/state_machine.py` → `notify_owner_turn()` / `feed_sensor_tick()`
- gating 决策：`core/scheduler/gating.py` → `run_shadow_tick()`（函数名保留 shadow，但当前 `EXECUTE_MODE="live"` 会执行 winner）

**关键状态文件/日志**
- `data/scheduler_cooldowns.json`
- `data/runtime/scheduler_user_state.json`
- `data/logs/trigger_state.jsonl`
- `data/logs/gating_shadow.jsonl`
- `data/logs/execute_dryrun.jsonl`

**已有测试或缺口**
- 已有：`tests/test_state_machine.py`、`tests/test_gating.py`、`tests/test_native_proposals.py`、`tests/test_execute_dryrun.py`、`tests/test_rhythm.py`
- 缺口：Watch 仍有独立事件驱动 live 开关；维护型 legacy 扫描与 proposal 缓存的端到端覆盖仍可补强。

**是否适合继续叠新功能**
- 适合继续承载小型触发器，但应走 `proposer_registry.register_proposer()` + `execute_prompt()`。
- 不建议继续直接扩 legacy `_check_*` 真实触发路径。

**最小补强**
- 明确 `core/scheduler/execution.py` → `EXECUTE_MODE` 的配置来源和启动日志。
- 为 Watch 独立开关、维护型扫描和 proposal 缓存补齐集成测试。

---

## 2. execute / dry-run / live 发送链路

**当前状态**：临时偏半稳定

**入口与调用方**
- proposal 执行：`core/scheduler/execution.py` → `execute_prompt()`
- dry-run 日志：`core/scheduler/execution.py` → `write_execute_dryrun()`
- live 发送：`execute_prompt(dry_run=False)` → `core.scheduler.loop._pipeline_send()` → `_mark()`
- shadow tick：`core/scheduler/gating.py` → `run_shadow_tick()`

**关键状态文件/日志**
- `data/logs/execute_dryrun.jsonl`
- `data/logs/gating_shadow.jsonl`
- `data/scheduler_cooldowns.json`
- `data/runtime/scheduler_user_state.json`

**已有测试或缺口**
- 已有：`tests/test_execute_dryrun.py` 覆盖 dry/live、reminder mark、weather cache、gating live winner。
- 缺口：live 模式当前不是运行时配置；watch event-driven triggers 被 `WATCH_EVENT_DRIVEN_TRIGGERS` 跳过 shadow execute。

**是否适合继续叠新功能**
- 适合继续接入 dry-run 观测。
- 可继续作为统一执行层承载小型迁移；新增发言型 trigger 不要再扩 legacy 即时发送路径。

**最小补强**
- `EXECUTE_MODE` 配置化。
- live 模式下 `legacy_tick_should_send()` 的旧路径关闭行为补集成测试。

---

## 3. Watch / 旧即时发话路径

**当前状态**：临时

**入口与调用方**
- HTTP 入口：`admin/routers/watch.py` → `receive_watch_event()`
- 睡眠合并：`admin/routers/watch.py` → `_flush_sleep_buffer()`
- 真实触发：`core/scheduler/triggers/watch.py` → `on_watch_event()`
- proposal 缓存：`get_last_heart_rate_event()` / `get_last_sleep_end_event()`

**关键状态文件/日志**
- `data/runtime/memory/{char_id}/{uid}/profile.json`
- `_last_watch_data`、`_LAST_HEART_RATE_EVENT`、`_LAST_SLEEP_END_EVENT` 为内存状态。

**已有测试或缺口**
- 已有：`tests/test_native_proposals.py`、`tests/test_execute_dryrun.py`
- 缺口：`/watch/event` 路由、sleep buffer cancel/flush、即时 live 与 proposal shadow 组合缺端到端覆盖。

**是否适合继续叠新功能**
- 不建议继续在 `on_watch_event()` 里叠即时分支。

**最小补强**
- 等 execute live 稳定后，再统一 watch 即时路径与 proposal/live mark 语义。

---

## 4. Prompt 构建 / prompt layer

**当前状态**：半稳定

**入口与调用方**
- pipeline 入口：`core/pipeline.py` → `Pipeline.build_prompt()`
- 构建函数：`core/prompt_builder.py` → `build()`
- tag 规则：`core/tag_rules.py` → `get_tags()`

**关键状态文件/日志**
- `characters/*.json`
- `characters/reality/jailbreak_entries.json`
- `data/runtime/characters/{char_id}/inner/activity_snapshot.json`
- `data/runtime/characters/{char_id}/inner/mood_state.json`
- `data/runtime/characters/{char_id}/inner/diary/*.md`
- logger：`prompt_builder.token`、`prompt_builder.debug`

**已有测试或缺口**
- 已有：`tests/run_eval.py`、`tests/test_short_term.py`、`tests/test_short_term_weighting.py`
- 缺口：`_layer` 元数据仍需核对是否会透传 LLM。

**是否适合继续叠新功能**
- 适合叠小 prompt 层；必须加 `_layer` 并检查裁剪顺序。

**最小补强**
- 核对并处理 `_layer` 透传。

---

## 5. 记忆链路

**当前状态**：半稳定

**入口与调用方**
- 读取入口：`core/pipeline.py` → `fetch_context()`
- 关键写入：`core/pipeline.py` → `post_process()`
- turn 捕获：`core/memory/fixation_pipeline.py` → `capture_turn()`
- 慢队列：`core/post_process/slow_queue.py` → `worker()`
- 短期历史：`core/memory/short_term.py` → `load_for_prompt()` / `append()`
- 中期记忆：`core/memory/mid_term.py` → `append()` / `format_for_prompt()`
- 情景记忆：`core/memory/episodic_memory.py` → `retrieve()` / `write_episode()`
- 长期模式：`core/memory/user_identity.py` → `format_for_prompt()`
- 事件流水：`core/memory/event_log.py` → `append()` / `search()`

**关键状态文件/日志**
- `data/runtime/memory/{char_id}/{uid}/history.json`
- `data/runtime/memory/{char_id}/{uid}/event_log/{date}.md`
- `data/runtime/memory/{char_id}/{uid}/mid_term.json`
- `data/runtime/memory/{char_id}/{uid}/episodic.json`
- `data/runtime/memory/{char_id}/{uid}/memory_index.json`
- `data/runtime/memory/{char_id}/{uid}/identity.yaml`
- `data/runtime/memory/{char_id}/{uid}/fixation_state.json`
- `data/logs/fixation.jsonl`
- `data/logs/dead_letter_queue/*.json`

**已有测试或缺口**
- 已有：`tests/test_fixation_pipeline.py`、`tests/test_post_process_ordering.py`、`tests/test_slow_queue.py`、`tests/test_short_term.py`、`tests/test_short_term_weighting.py`
- 缺口：`event_log.append()` 为普通 append 文件写；`episodic_memory._save_index()` 为普通 `write_text()`；`fetch_context()` 读写竞态已记入 `docs/known-issues.md`。

**是否适合继续叠新功能**
- 适合沿 `capture_turn → summarize_to_midterm → reflect_to_episodic → consolidate_to_identity` 主链继续承载。
- 不建议新增旁路长期记忆写入。

**最小补强**
- `B12` 核心记忆裁剪保护。
- event_log/index 安全写或锁边界。

---

## 6. Channel / adapter / QQ / mobile / desktop

**当前状态**：半稳定

**入口与调用方**
- QQ 入口：`main.py` → `handle_message()`
- 桌宠/手机 owner 入口：`admin/routers/chat.py` → `run_owner_chat_turn()`
- 手机路由：`admin/routers/mobile.py` → `mobile_chat()` / `mobile_poll()`
- 统一下行：`core/turn_sink.py` → `record_assistant_turn()`
- 通道广播：`channels/registry.py` → `broadcast()`
- 桌宠 WS：`channels/desktop_ws.py` → `handle_connection()` / `push_message()` / `push_action_and_wait()`
- 桌面叙事展示视图：`core/narrative_parser.py` → `parse_narrative_segments()`；`turn_sink` 额外推 `message_segments`

**关键状态文件/日志**
- `data/runtime/channel_queue.json`
- `data/runtime/mobile_queue.json`
- `data/runtime/agent_actions.json`
- `data/runtime/pending_perception/`

**已有测试或缺口**
- 已有：`tests/test_turn_sink.py`
- 缺口：`DesktopChannel` / `MobileChannel` 文件队列并发与损坏恢复测试不足；QQ 主入口仍是 legacy 直发；`message_segments` 目前只覆盖桌面 WS。

**是否适合继续叠新功能**
- 适合承载 mobile/desktop 主动消息。
- 不建议继续扩 `/desktop/trigger` 或 QQ legacy 直发路径。

**最小补强**
- 队列文件安全写。
- QQ 回复逐步对齐 `turn_sink` 的关键写入模型。

---

## 7. Tools / hardware / sensor

**当前状态**：半稳定偏临时

**入口与调用方**
- 工具注册：`core/tool_dispatcher.py` → `_TOOL_REGISTRY`
- 探针 prompt：`core/tool_dispatcher.py` → `get_probe_prompt()`
- 工具执行：`core/tool_dispatcher.py` → `execute()`
- 桌面动作：`core/tool_dispatcher.py` → `_push_desktop_action()`
- 实时 sensor 入口：`admin/routers/sensor.py` → `receive_realtime_snapshot()`
- sensor 主动出口：`core/scheduler/triggers/sensor_aware.py` → `handle_tick()`

**关键状态文件/日志**
- `data/runtime/agent_actions.json`
- `data/runtime/pending_perception/`
- `data/runtime/characters/{char_id}/inner/activity_snapshot.json`
- `data/runtime/memory/{char_id}/{uid}/profile.json`
- sensor realtime 与 audit ring buffer 主要是内存。

**已有测试或缺口**
- 已有：`tests/smoke_sensor_*.py`
- 缺口：工具 registry/schema/权限矩阵缺单元测试；memory 类工具已注册但未进入正式 LLM tool round。

**是否适合继续叠新功能**
- info/desktop 工具可继续承载小改。
- memory/system 类工具不适合只靠 Author's Note 承诺继续叠。

**最小补强**
- 工具注册表 schema / keywords / examples 测试。
- `F11` 等重构期再统一工具通道。

---

## 8. Garden / mood / diary

**当前状态**：半稳定

**入口与调用方**
- 花园核心：`core/garden/manager.py` → `water()` / `auto_water_tick()` / `daily_check()`
- 自动浇水触发：`core/scheduler/triggers/garden_water.py` → `_check_garden_water()`
- 每日扫描触发：`core/scheduler/triggers/garden_daily.py` → `_check_garden_daily()`
- 被动浇水工具：`core/tools/garden_tools.py` → `water_garden()`
- 情绪状态：`core/memory/mood_state.py` → `update()` / `get_current()`
- 日记上下文：`core/memory/diary_context.py` → `save()` / `load()`

**关键状态文件/日志**
- `data/runtime/characters/{char_id}/garden/plants.json`
- `data/runtime/characters/{char_id}/garden/storage.json`
- `data/runtime/characters/{char_id}/inner/mood_state.json`
- `data/runtime/characters/{char_id}/inner/diary/*.md`
- `data/runtime/memory/{char_id}/{uid}/diary_context.txt`

**已有测试或缺口**
- 已有：`tests/test_native_proposals.py` 覆盖 garden proposal。
- 缺口：`core/garden/manager.py` 本体状态迁移、并发写缺单测。

**是否适合继续叠新功能**
- 适合叠只读展示和低频事件。
- 不建议在 `G2` 补强前继续增加多写入口。

**最小补强**
- garden 专用锁与 `safe_write_json()`。
- `daily_check()` 分支迁移测试。

---

## 总判断

最适合继续承载的主干：
- `admin/routers/chat.py` → `run_owner_chat_turn()`
- `core/turn_sink.py` → `record_assistant_turn()`
- `core/memory/fixation_pipeline.py`
- `core/scheduler/proposer_registry.py` + `core/scheduler/execution.py`

不适合继续叠的旧旁路：
- `core/scheduler/triggers/watch.py` → `on_watch_event()` 内继续加即时发话分支
- QQ / `/desktop/trigger` legacy 直发路径
- 没有锁和安全写的队列/花园写路径

当前优先级应以小补强和边界文档为主：`B12`、`S1`、`G2` 可现在修；`D2`、watch、diary_share 等等 execute live soak；`F10`、`F11`、`D7` 等重构期处理；`B11`、`G4`、DESIGN 感知/主动原则先文档化边界。
