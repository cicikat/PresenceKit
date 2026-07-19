# docs/known-issues.md — 已知问题与技术债

> 最近核对：2026-07-16（cc-tasks/28 三仓技术债清盘）。
> 这里只保留仍需行动或观察的条目；已关闭条目的完整背景保留在 Git 历史。

## 当前仍存在

### PB4：Path B 降级观察期

**状态**：`observe`
**到期倒计时**：2026-08-10 到期。到期无缺口记录就开删除 brief。

`config.intent_reflex.enabled` 默认关闭，旧 Path B 守卫暂留。观察期若出现 tool loop 已启用但“角色说了要做却没做”的用户可感缺口，在此登记触发消息、期望动作和实际结果；到期仍无记录则整删 `_parse_and_execute_intent`、守卫、幂等窗口及对应测试。

### ACT-1：阅读动向跨角色串桶

**状态**：`observe`（前端已分桶，待复现观察）
**位置**：后端 activity 路径；前端 `SubFlow.tsx`

后端已确认按 `char_id + uid` 隔离且无角色默认参数。PresenceKit-desktop 已于 2026-07-16 将时间轴改为 `subflow_timeline:{charId}`，旧全局桶一次性迁入当时激活角色并删除。若仍复现，再核对操作时的 `active_character` 与后端请求。

### ACT-2：反坍缩重试未覆盖流式路径

**状态**：`open`（需独立设计）
**位置**：`core/pipeline.py::Pipeline.run_llm_stream()`

非流式路径可在发现重复句首后丢弃并重试；流式 token 已对用户可见，不能直接套用。下一步需在“暂缓前 N token”与“流式只接受软降级”之间完成设计、延迟评估和协议验收。

### F8：管理面板对话 UI 右键历史未实现

**状态**：`post-v0.1`
**位置**：`admin/static/index.html`

不影响主链路。需要时另开管理面体验工单，不在后端技术债清盘中扩张范围。

### DREAM-1：身份稳定性测试仍是弱代理

**状态**：`observe`

人称与依恋关键词只提供最低限度信号；`GET /dream/invariants` 已补跨梦矛盾观测。继续以实际游玩和 identity eval 双轨观察。

### identity-2：identity 注入有冷启动期

**状态**：`observe`

新用户需经过 mid-term → episodic → consolidate 才开始注入。先观察首个有效维度需要的轮数，再决定是否调阈值。

### TD-1：`sandbox.py` 兼容层

**状态**：`observe`

`core/data_paths.py` 已承接实现，但大量调用与测试 fixture 仍依赖 `core.sandbox.get_paths()`。当前把它当稳定兼容层，不为命名整洁做大范围替换。

### Brief 28/29 运行观察

**状态**：`observe`

- tool loop 与 QQ 关键词快速路径理论上可能在同轮重复执行幂等工具；出现有副作用的快速路径前重新评估。
- MCP 工具描述和结果是不可信输入；v1 只有截断和来源边界，后续需要时按 web 召回同级做内容隔离。

## design-backlog

**2026-07-16 全部拍板关闭**，裁决与理由见 `DESIGN.md` §十一（决策 3–8）。摘要：

- D7：**不回流**（自产内容不固化原则）。
- G4：**最小方案**，全落 `storage.json` history；gift 可触发一次性主动消息（走 ledger）。
- DESIGN-1：**默认只影响态度**，直说需 tag 命中 / 健康告警 / 用户显式问三者之一。
- DESIGN-2：**追认现状三级**（健康可打断 / 情感 QUIET+ledger / 信息可 defer）。
- SC1：**维持冻结**。
- REC1：**observe**，出现实际坏召回样本再动。
- PB1：**并入决策 1**（数据级来源标记原则，Brief 79 模式），召回链复评时执行。

需要写码的两条（G4 最小方案、P2-1）已各自出单落地（Brief 83 / Brief 82），见「本轮已核对关闭」。
其余为纯设计裁决无代码工作。

## 用户动作（代码侧无事可做）

- SEC-AUTH-2 P4 后半：各持有方切换新 token；ESP32 重烧录；Watch Shortcut 与管理面板换值；全部确认后再轮换 legacy secret。
- `data/runtime/auth/audit.jsonl` 约 200 条 `ip=testclient` 测试噪音：由用户决定是否手动清空，本工单不删除数据。

## 本轮已核对关闭

| 编号 | 结论 |
|---|---|
| ADMIN-1 | `jailbreak_entries.py` 已导入 `pathlib.Path`。 |
| F11 | Brief 28/29 tool loop 默认 categories 已包含 `memory`，生成侧接线完成。 |
| P2 `_layer` | `llm_client.py` 在 provider 边界统一调用 `sanitize_messages()`。 |
| PB3 | episodic 加载 fail-loud；空列表覆写非空文件护栏、写后 JSON 校验和 `.bak` 均存在。 |
| TEST-1 | `test_sandbox_paths.py` 已断言 `runtime/channel_queue.json`，旧 `_identity_file` 全仓零命中。 |
| B11 / F10 / D2 / P1 / SEC-AUTH-1 / SEC-WS-1 / identity-1 / TD-2 / TD-3 | 均已完成，已从当前问题区移除。 |
| R6 final | 单出口稳态已完成（2026-06-11）：R1-D 后 QQ 路径完整接入 `turn_sink`，全部 LLM_ASSISTANT_REPLY 均经 scrub 链。守卫：`tests/test_r6c_reality_scrub_final.py`。 |
| PB2 | 2026-07-16 在 `1.5_fact_boundary` 加桌宠身份锚点；空屏幕感知时明确禁止虚构屏幕场景，并有专项测试。 |
| P2-1 | Brief 82：`tool_read_log.detect_bypass_intent()` 探测显式重读短语常量表，命中给本轮 `execute()` 传 `bypass_read_log=True`，`is_recently_read(bypass=True)` 放行拦截但指纹照常刷新。 |
| G4 | Brief 83：`garden_manager.daily_check()` 里 `dry`/`gift`/`ask` 处理完成后统一落 `storage.json.history`（`kind/flower/mood_source/ts/note`）并离开 `harvest`；`garden_handle_self` proposer 收窄为仅 vase，`ask`/`dry` 不再发消息，仅 gift 保留经 `ProactiveLedger` 记账的主动消息；`GET /garden/state` 新增 `history_recent`。 |
| H1 | Brief 88：`user_hidden_state` 现实侧写入链已全量接线——`RealityEventType` 扩至 5 类（新增 `BODY_TOPIC` / `AFFECTION_EXPRESSED`）；对话侧判定落在新模块 `core/memory/user_hidden_state_reality_signals.py`，挂 `pipeline.post_process_slow` detect_emotion 之后，trigger 轮零参与；`NO_INTERACTION` 挂现有 `hidden_state_decay` 12h tick，presence gap ≥24h 且逻辑日未记账时 accrue，去重 stamp 落盘于 `hidden_state_no_interaction_stamp.json`；`body_memory` 长期层经 `integrate_body_cue_and_save` 接线，仅在调用方 envelope.can_write_memory=True 时写入；`hidden_state_debug` 观测端点新增 `trigger_counts`。见 `cc-tasks/88-hidden_state现实侧接线-全量信号映射.md`；测试 `tests/test_hidden_state_reality_signals_brief88.py`。 |
| P3 | Brief 102：`build()` 强制裁剪后从最终 `messages` 按 `_layer`（含 `_report_layer` 覆盖）重算 `layers_activated`，新增 `layers_before_trim` 保留裁剪前全集；`_layers` 构建期累加器已删除。`6c_episodic` fallback 分支新增 `_report_layer="6c_episodic_fallback"`，保持与 `_layer` 共享消融规则的同时不破坏 memeval `layers_absent` 对"命中检索 vs 兜底注入"的区分。测试 `tests/test_prompt_trim_layers_recompute.py`。 |
