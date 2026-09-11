# docs/known-issues.md — 已知问题与技术债

> 最近核对：2026-08-23（补充 SCHED-1/SCHED-2 调度器后续风险；23 点日记回归已修复）。
> 这里只保留仍需行动或观察的条目；已关闭条目的完整背景保留在 Git 历史。

## 当前仍存在

### 管理面新导航的原生容器验收

**状态**：`observe`（2026-09-10）。可折叠分类、文档快捷入口及返回历史已经在本地
admin 真实静态资源上完成 Chromium 桌面和窄屏验证，包含清缓存刷新、输入保留、
语言切换及快速跳转。浏览器回归拦截业务 API，不使用生产 token；真实登录、桌面
原生桥接窗口与手机 WebView 仍待部署验证。参见 `docs/admin-navigation-guide.md`。

### API 思考存档的展示接入

**状态**：`roadmap` / `observe`（2026-09-09）。后端默认独立保存已返回的思考并提供
admin-only 列表/详情。管理面板查看页、聊天 turn_id 关联、桌面/手机可选展开尚未实现，
当前 call_id 仅代表一次 API 尝试，不能按时间近似匹配聊天回合。跨三协议本地模拟回归
覆盖存档、流式中断与权限；真实中转返回字段及真机界面尚未验收。

### 空回复后的客户端恢复验证

**状态**：`observe`（2026-09-09）。共享 owner chat 已在写入前拒绝空/纯空白正文，
返回 HTTP 502 与安全提示，不写 assistant 记忆、不广播伪回复。回归覆盖手机与桌面
流式路径、工具循环开关、失败后同一用户再次发送成功及锁释放。
手机已有错误展示与 sending/typing 的 finally 清理；真实中转、手机及桌面 UI 连续
失败后恢复仍需部署验证。桌面 bridge 对 5xx 保留通用错误，不保证展示后端 detail。
原报告的“首次失败后一直失败”尚未复现，不能据此认定存在持久会话锁死。
空输出增加 `llm_client.empty_completion` 元数据日志：协议、结束原因、正文清理前后
长度、Chat Completions reasoning 长度与原始/归一化工具数，不记录响应正文。
`observe`：等待中转复现以区分上游空正文、仅思考内容及工具字段适配；目前不能
从 owner_chat 的空回复日志确认具体根因。Chat Completions 仅在 finish_reason 为
tool_calls 时接收工具字段，非标准中转结束标记可能使工具调用被忽略，尚未确认本例命中。

### OCR service and device verification

**Status**: `observe` (2026-09-09). Independent OCR supports GLM Layout Parsing and
OpenAI-compatible OCR. Focused regressions cover protocol, cache, configuration and
phone inheritance. Real GLM credentials and physical desktop/mobile uploads need
deployment verification; configuration readiness does not claim live provider success.
PDF input and combined scene-plus-OCR routing remain `roadmap`.

### BROWSER-240：兼容 owner bridge 退役审计

**状态**：`closed`（2026-09-06）

浏览器任务配置与提交已经统一到后端 admin `/settings/agent-runtime-browser`。Brief 72 的
当前客户端源码已删除 `load_agent_runtime_browser`、`create/run/confirm/pause/cancel` 等
Tauri command，`rg` 未发现 source caller；本工单删除了旧写/观测路由、请求 schema、UI
白名单和重复序列化，并在 OpenAPI/三仓总账中记录 retired。admin 仍 fail-closed：高风险
任务先 `waiting_confirm`，确认前不会执行。

### RPG Dream client handoff

The RPG backend contract is complete through Brief 222. Desktop dual-column
UI and mobile consumption remain `open`; clients must use generated
`/openapi.json` schemas and `docs/rpg-dream-api.md`, not runtime files or
proposed payload examples.

The shared legacy `/dream/enter` and `/dream/archive*` routes still publish
generic object schemas in OpenAPI. The client guide lists their bounded fields;
strict typed request/response models are a follow-up backend hardening item.

### AUTONOMY-195：生产校准观察

**状态**：`observe`（2026-08-27 已定位并修复一类假沉默；模型决策偏置仍待部署后观察）

Brief 195 已提供按 source/disposition 的 24h/7d 漏斗和隔离 `talk_sent` 回归。  
**2026-08-27 现场数据（Brief 224）**：`yexuan/<owner_uid>` 当日 `evaluations=266`、`talks=0`，
留存 runs 全是 `blocked_user_active`。根因不是“没候选/模型太保守”，而是：

1. 卡住的 `dream_seed` ActivitySession（自 2026-08-06 `active`）让 admission 永久返回 `blocked_user_active`
2. admission-only 失败仍计入 `daily.evaluations` 并推进 `last_evaluated_at`，打爆预算并触发跨 source 冷却

已做：运维关闭僵尸 session + 重置当日计数；代码侧 per-type activity TTL、dream_seed 放弃也关闭、
admission-only 不计评估预算、`talks==0` 预算兜底。详见 `cc-tasks/224-autonomy-admission-stale-activity-budget.md`。

部署后继续观察漏斗。只有高价值 signal 已稳定到达模型、`talk_owner` 可用且仍长期
`evaluated_silent` 时，才评估 prompt 决策准则；不得新增 festival/period 直发出口，也不得恢复
legacy `_pipeline_send()`。

### SCHED-1：`practice_help` 的 autonomy 边界审计

**状态**：`roadmap`（中工单；不阻断 `inner_diary_write`）

`practice_help` 的 `UrgencyTier.AMBIENT` 回归已经修复，且 proposer 单点异常现在不会越过
scheduler tick 边界。但该 proposer 的 `execute` 回调仍通过 `execute_prompt()` 直接发言，尚未完成
signal-first/autonomy 契约审计。后续必须单独决定它是迁移到 autonomy，还是作为明确登记的兼容路径保留。

若迁移，只能传递受限事实和稳定标识，不得把练习作品原文或任意 prompt metadata 直接写入 signal，
并补齐 TTL、dedupe、talk gate 和 autonomy 观测。若暂不迁移，必须保持独立开关或默认关闭，不能让
它以未登记状态混在普通 proposer 中。该工单不得改变 `inner_diary_write` 的静默维护边界，也不得
让 `daily_journal` 重新承担日记落盘副作用。

### SCHED-2：`inner_diary_write` 持久任务化

**状态**：`roadmap`（大工单；当前不改变触发器行为）

当前角色日记仍由 scheduler 在 23:00-05:00 维护窗口内直接生成，并以 logical day 文件存在性做幂等。
进程重启、LLM 超时或维护轮次中断时，仍可能错过当日窗口；这不是本次 `AMBIENT` 回归的修复范围。

长期方案应评估带幂等键的后台维护 job，例如
`inner_diary_write:{char_id}:{logical_day}`：scheduler tick 只负责发现并入队，worker 负责锁、重试、
成功确认和失败退避。实施前必须明确持久 slow queue、只读观测端点、跨角色并发、备份和恢复策略，
不能通过修改 `daily_journal` 或恢复旧的主动发言路径来替代。

### SENSOR-1：sensor signal-first 尚未恢复旧行为 action payload

**状态**：`open`（文档/代码边界已确认，未在本轮改业务代码）

`core/scheduler/triggers/sensor_aware.py::handle_tick()` 在 signal-first 分支只把
`behavior_id` 等事实放入 `emit_trigger_signal()`，随后由 autonomy `talk_owner` 通过
`talk_gate.send()` 进入普通 `record_assistant_turn()`。旧的 `build_action_packet()`、
`DesktopChannel.send(..., behavior=...)` 组装和 `SENSOR` turn sink 分支位于显式 `return`
之后，当前不可达。因此 `passive_speak` 等文字候选仍可进入 autonomy 评估，但
`pet_emote` / `notify` / `execute` 不能从这条 signal 自动执行。

若要恢复行为动作，需另立 autonomy payload、危险模式闸门、desktop/mobile 协议和验收方案；
不能仅把旧的不可达分支重新放开。

### PROF-1：user_profile 场景类字段的反抖动阈值曾经"锁死"（已修复 2026-07-25）

**状态**：`closed`（记录于此供以后同类字段参考，不是待办）

`core/memory/user_profile.py::update()` 的 pending-override 反抖动机制默认要求同一
新值被"连续 2 次一致提取"才落盘覆盖，本意是防止单次幻觉提取翻转已确认的值。但
`location` 这类"此刻状态"字段用户通常只提一次（"我现在在绍兴"），`extract_and_update`
每次只喂最近 10 条用户发言，话题一岔开就再也凑不出第二次一致提取，`location` 从此
追不上现实——连带天气工具跟着一直查旧城市（`main.py` 把 `profile['location']` 当天气
查询的默认城市）。已加 `_PENDING_OVERRIDE_THRESHOLD_BY_FIELD`，`location` 单独降为
阈值 1，其余字段（name/pets/interests/occupation）保持阈值 2。同时修了 `main.py` 里
`profile.get("location", "杭州")` 的经典 dict.get 语义坑（key 存在但值为 None 时不
会触发 default，改用 `profile.get("location") or "杭州"`）。回归见
`tests/test_user_profile_override.py`（8/8b 用例）、`tests/test_profile_location_fallback.py`。

若未来再给某个"易变但用户通常只说一次"的字段加反抖动保护，请复用
`_PENDING_OVERRIDE_THRESHOLD_BY_FIELD`，不要照抄默认阈值 2。

### GROUP-1：跨角色印象摘要语气曾无约束、易读出暧昧色彩（已修复 2026-07-25）

**状态**：`closed`

`core/stage/char_relations.py::_relation_prompt()` 生成的角色间"第三人称印象"摘要
会被 `core/stage/context.py::render_presence()` 原样当作既有事实注入每一轮群聊
prompt（无 tag 门控、无相关性判断，roster 里任意两个角色只要有过互动就会被注入）。
旧 prompt 对措辞语气没有约束，LLM 生成摘要时容易往暧昧/亲密方向靠，角色会把这段
"既有印象"当真，没来由地说出"今晚我跟{另一角色}聊天的时候……"这类话（茶茶反馈）。
已给 `_relation_prompt()` 加中性克制的措辞约束。这是内容侧修复；`render_presence()`
本身"每轮无条件注入所有已知关系"的设计没有改动——这是群聊"在场感"功能故意要的
效果（让角色记得彼此），如果以后还想加相关性门控（比如只在话题涉及对方时才注入），
需要专门评估，不在这次修复范围内。

### TRACE-1：recall trace 三元组解包报错（已修复 2026-07-25）

**状态**：`closed`

`core/pipeline.py::fetch_context()` 拼 recall trace 字典时，`semantic_hits` 那行按
`for _sid, _dist in _semantic_hits` 二元解包，但 `core/memory/vector_store.py::
query_async()` 返回的是 `(source_id, distance, ts)` 三元组，线上天天报
`"[pipeline.fetch_context] recall trace write failed: too many values to unpack
(expected 2, got 3)"`（茶茶反馈实际日志）。已改用下标取值（`h[0], h[1]`），与旁边
debug log 那行一直用的写法一致。回归见 `tests/test_recall_trace.py::
TestFetchContextSemanticHitsTraceUnpack`。

### PB4：意图解析旁路（已退役 2026-07-30）

**状态**：`closed`

已删除回复文本反向解析桌面动作的旧旁路、配置开关、120 秒幂等窗口及预留 origin。
原定 2026-08-10 的纯日期观察门槛已取消：删除依据是功能覆盖审计，不是数据质量或资金安全
观察期。审计确认 7 项旧能力均已有正式 desktop 工具路径：`desktop_minimize`、`play_song`、
`desktop_open_url`、`desktop_play_pause`、`desktop_notify`、`dream_invite` 与
`toy_invite`；它们保持既有危险模式闸门、active-character 工具暴露面、action payload 和
前端协议不变。替代验收为 focused/full pytest 及真机 desktop action、ToyWindow、DreamWindow
冒烟；不再以“无历史日志”或日期未到阻塞删除。

**2026-07-25 新增工具**：管理面板「观测」区新增三块面板，供日常自查：
- `GET /observability/resource-completeness`（`core/resource_completeness.py`）——扫描
  各功能开关/素材配置状态，标出"关着"和"开了但缺素材"；附一份人工维护的"功能压根还没
  做"清单（当前含移动端 TTS 投递、桌宠语音条 UI 解耦、Live2D/3D 绑定前端消费三项，来源
  见 `cc-tasks/124`/`125`/`docs/tools.md`）。
- `GET /observability/api-contract-check`（`core/api_contract_check.py`）——扫后端
  `_push_desktop_action` 产出的 type 字符串，和前端 `ws.ts` 的
  `_dispatchAction` switch 取差集。前端仓库不存在时优雅跳过
  （约定与本仓同级目录，或设 `EMERALD_CLIENT_REPO` 环境变量）。
- `GET /observability/character-permissions` + `POST .../test`（
  `core/character_permissions.py`）——按类目（info/desktop/memory/system/fs/
  phone_control）列出对某角色的暴露面、是否受危险模式闸门约束，以及身份固化管线
  （角色自己改 identity.yaml 那条后台链路）的状态；测试按钮对 identity_consolidation/
  fs 两条安全链路真实执行一次，其余会产生真实副作用的类目（弹通知/震动/关机等）只做
  就绪检查清单，不会代用户触发。

### ACT-1：阅读动向跨角色串桶

**状态**：`observe`（前端已分桶，待复现观察）
**位置**：后端 activity 路径；前端 `SubFlow.tsx`

后端已确认按 `char_id + uid` 隔离且无角色默认参数。PresenceKit-desktop 已于 2026-07-16 将时间轴改为 `subflow_timeline:{charId}`，旧全局桶一次性迁入当时激活角色并删除。若仍复现，再核对操作时的 `active_character` 与后端请求。

### ACT-2：反坍缩重试未覆盖流式路径

**状态**：`observe`（方案 B 已落地，观察单轮坍缩率）
**位置**：`core/pipeline.py::Pipeline.run_llm_stream()` / `Pipeline._check_stream_collapse()`

`cc-tasks/105` 已裁决并实现方案 B——流式路径不丢弃重试（暂缓前 N token 的方案 A 已封存），
命中句首同质坍缩（S2 同源检测）时只记观测日志（`[anti_collapse] stream_soft_degrade`）+ 写入
下一轮一次性信号，由下一轮 `build_prompt` 注入 `stream_collapse_hint` 层纠偏，详见
`docs/prompt-layers.md` §反坍缩治理 · ACT-2。**观察项**：若上线后单轮内坍缩率（同一条流式回复
内部即出现重复句首，而非跨轮）显著高于非流式路径，需复议启用方案 A。

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

Brief 104 §3 已落地两块基础设施，供后续判断：
- **量化**：`consolidate_to_identity()` 检测到某用户首次出现 confidence>=0.5 的维度时，
  记一条 `identity_coldstart` 日志到 `fixation.jsonl`（真实轮数取自
  `event_log.count_real_turns()`，即 full_log.md 里 `speaker:user` 计数，不受
  short_term 20 轮滑窗影响）。跨用户汇总见
  `GET /memory/fixation/identity-coldstart-summary`；单用户明细见
  `GET /memory/fixation/status?uid=...`。
- **降级体验**：`user_identity_text` 为空但已有真实交互历史（复用
  `core/scheduler/rhythm.has_real_interaction_history()` 同一冷启动阈值）时，注入
  `6a_user_identity_coldstart` 层，如实表达"还在慢慢认识你"，不编造记忆内容（详见
  `docs/prompt-layers.md`）。
- 积累到有意义的样本量后，再回来看 `avg_real_turns` 决定是否调
  `_should_consolidate()` 的阈值。

### TD-1：`sandbox.py` 兼容层

**状态**：`observe`

`core/data_paths.py` 已承接实现，但大量调用与测试 fixture 仍依赖 `core.sandbox.get_paths()`。当前把它当稳定兼容层，不为命名整洁做大范围替换。

### Brief 28/29 运行观察

**状态**：`observe`

- Brief 136 已收口 QQ/desktop/mobile 的前置路由：快速路径成功后从 Path C schema 排除同名工具，
  快速失败才允许 loop 重试；原“QQ 快速路径同轮重复执行”观察项已关闭。快速白名单仍只允许无参、
  无副作用工具，新增成员前必须重新评估。
- MCP 工具描述和结果是不可信输入；v1 只有截断和来源边界，后续需要时按 web 召回同级做内容隔离。

## design-backlog

### Memory Event external relation endpoints

**status:** `open` / `roadmap`

`triggered_by`, `derived_from`, `correction_of`, and `media_of` are written
only when both endpoints already exist in the same reality ledger. Scheduler,
sensor, stimulus, and media references do not yet have a shared typed event
node contract, so the system deliberately does not synthesize IDs or dangling
edges for them. A future implementation needs an explicit read-only reference
node lifecycle plus scoped endpoint validation before these relations can gain
additional production sources.

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
- `data/runtime/auth/audit.jsonl` 的历史 `ip=testclient` 噪音不作为当前问题：不删除既有审计记录；2026-08-02 已补回归测试，测试环境的真实 401 审计必须落在活动测试沙箱，不能再写生产台账。
- **浏览器系统代理导致远程管理面板首次请求失败** — `observe` / Brief 194。桌面端已提供 desktop-only 的 loopback native bridge；普通浏览器直接访问远程 tailnet hostname 仍由用户的 DIRECT/PAC 决定，手机端不继承该 bridge。待 Windows 系统代理开启且远程 hostname 未列入 DIRECT 的实机 whoami、设置读写和上传验收完成后关闭本观察项。

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
| SCM-1 | Self Capability P0 only stores capability IDs and safe state in its audit. Before adding more MCP-facing capability metadata, review every new log and UI projection to ensure no complete connection URL, header, bearer token, or raw tool payload is persisted or returned. |
# Memory Event repair status (Brief 214-215)

- `closed`: append schema maintenance/linear migration work, duplicate legacy
  import, source-loss migration, storyline partial commits/date-only cursor,
  double shadow comparison, oldest-seed selection, permissive proposer
  discovery, and isolated-inventory `COUNT(*)` observability were addressed by
  MER-09. Its gate was reopened by the post-gate audit, then closed by MER-10
  after Markdown recall isolation, real migration dry-run, dual-source
  storyline cursor v3, parallel bounded shadow reads, and proposer source
  filtering passed the focused regression matrix.
- `roadmap`: production producers for `triggered_by`, `derived_from`,
  `correction_of`, and `media_of` remain limited to evidence-backed call sites.
  No synthetic stimulus/media events were added to close the repair gate.

## Memory Event repair status (Brief 216)

- `closed`: MER-11 fixed dry-run JSON serialization, aggregate indeterminate
  migration status, cross-date Markdown source filtering, and storyline physical
  date deduplication. Backend control and content-free observability now expose
  desired/effective/route/run distinctions without projecting bodies or IDs.
- `observe`: historical migration remains an operator action after read-only
  dry-run and verified backup. Shadow and proposer stay disabled by default and
  must be rolled out per scope; candidate relations are not accepted edges.
- `roadmap`: production producers for `triggered_by`, `derived_from`,
  `correction_of`, and `media_of` remain unchanged and limited.

## Memory Event identity soak (Brief 217)

- `observe`: EventContext now carries the frozen Reality ingress scope through
  the turn sink to ledger evidence provenance. The observer defaults to
  `disabled`; enforcement is intentionally unavailable until the focused S0
  matrix and S1 short soak have accumulated the required restart, duplicate,
  multi-channel, and ledger-unavailable samples. No recall, prompt, ranking,
  Dream/Stage lifecycle, or client protocol consumes this trace.

## Agent Runtime architecture (Briefs 229-233)

- `current`: the Reality-only general Task Manager and its metadata-only
  `state.read` observability endpoint are implemented. It provides atomic scoped
  storage, idempotent creation, lease/attempt/TTL/cancel handling, and restart
  recovery to `outcome_unknown` without replay.
- `current`: Brief 231 gives every registered scheduler producer one lifecycle. Proactive candidates
  become autonomy signals, maintenance workers remain silent, and `dream_postcards` is an independent
  scheduled artifact-delivery entry rather than a `dream_exit` alias.
- `current`: Brief 232 provides Reality-only bounded Agent Work Sessions and migrates
  `inner_diary_write`; work context is digested in observation and no work session is an ingress,
  assistant turn, evidence item, or automatic memory fact.
- `current`: Brief 233 provides local-only controlled workspace read/list/create/update/delete/undo
  with explicit roots and grants, durable version snapshots, Task receipts, and redacted observation.
- `current`: Brief 234 provides a local-only, workspace-scoped bounded process runner with no shell/network
- `partial`: backend now contains a Reality-only, opt-in browser worker with per-task isolated
  profiles, domain/operation checks, owner confirmation states, pause/cancel/timeout and
  `outcome_unknown` handling, bounded redacted results, and Workspace-only file transfer hooks.
- `open`: Playwright is optional and disabled by default; no desktop/mobile task/result surface or
  production soak is claimed until Brief 239 and an operator-reviewed adapter deployment exist.
- `partial/open` (Brief 239): request fingerprints, one-shot confirmation, redaction, and route
  guards are implemented and covered by backend tests. The local acceptance environment lacks the
  Playwright Python binding, so an actual Chromium fixture and client-side owner bridge remain
  unverified; no completion claim is made.
### KNOW-228: character knowledge client surface

The scoped character knowledge library and admin observability endpoint are
implemented. Desktop/mobile browsing and upload-management UI are intentionally
not added in this brief; they remain `roadmap` and must be specified in the
three-repo interface catalog before client work begins.

## Memory Event identity soak repair (Brief 217)

- `observe`: startup and the enable endpoint now upgrade/check existing ledgers;
  durable trace aggregation survives process restarts. Enforcement remains
  unavailable until the focused S0 matrix and S1 short soak collect the required
  restart, duplicate, multi-channel, and ledger-unavailable samples.

### Forced streaming verification

`observe` (2026-09-09): Chat Completions presets support opt-in force_stream for ordinary generation and tool decisions; incomplete calls are rejected. Real gateway reproduction remains pending, so the original empty completion cause is not confirmed. Admin browser verification is incomplete because this session lacks Browser execution tools; alternatives are JS syntax, configuration API and protocol regressions. Responses/Anthropic forced streaming remains roadmap and unsupported combinations are explicitly rejected.

### Mobile typography verification and historical fidelity

`observe` (2026-09-09): reality replies and mobile queue delivery now carry optional inline display_text; Flutter renders hl/big/sm, including reveal and selection. Widget/controller and backend regressions cover live delivery; no physical phone visual verification has been performed. `roadmap`: chat-log history already stores plain text and cannot reconstruct discarded tags after reload; independent Dream/group typography transport is not included in this reality change.

## Brief 242 验收边界（2026-09-10）

- `observe`：设置重整已通过后端 UI/API、桌面类型/构建与浏览器夹具检查；尚未完成真实
  Tauri 原生窗口和手机设备回归。浏览器使用真实静态/React 资源、夹具 API/IPC，不能
  代表真实 provider 连通、生产配置保存或 native bridge 实机结果。设置与协议事实见
  `docs/three-repo-interface-catalog.md` 的 Brief 242 条目。
- `open`（既有）：`tests/test_admin_tools_mcp_ux.py` 的工具说明覆盖检查发现 `reread_image`
  缺中英文语义说明。此次未修改该工具或放宽断言；需独立补工具文案。

## 聊天回合思考读取（2026-09-11）

`current`：GET `/chat/turns/{turn_id}/reasoning` 要求 memory.read，标准 desktop/mobile
profile 可读取已关联的 Reality owner 回合，返回 available/entries（含 parts）。
桌面/手机共享 owner 入口在生成期间关联调用，工具循环子任务同样关联，post-process
前停止采集关联，防止后台思考串入；关联失败不影响回复。旧归档不推测关联。
原 admin-only 全局归档接口不变。无新增生成开关，不修改正文、WS、poll、ack 或 TTL。
`roadmap`：客户端按气泡展示和真机验收、QQ/主动消息/Dream/Stage 回合关联。
前端工单见 cc-tasks/244-frontend-reasoning-handoff.md；按用户要求未跨仓修改。
## IME v2 接收预备（2026-09-11）

`current`：POST `/v1/ime/drafts`（sensor.write）按设备 token label + id + revision
事务覆盖完整草稿；默认 ime_ingest.enabled=false，通过 feature-flags 的 ime_ingest
控制。admin-only GET `/observability/ime-drafts` 提供三小时内原始字段与 effective 状态。
独立 inbox 不接入 LLM/记忆/任务/广播；原 sensor、聊天、WS、ack、TTL 不变。
`observe`：尚未启用真实 IME 传输；私网 HTTPS、证书与设备配对需部署验证。
只读核对输入法真实 v2 代码，未跨仓修改。详细协议与限制见 docs/ime-ingest.md。
## 周信发送断链修复（2026-09-11）

`current`：letter_writer 曾被标为 migrated，winner 仅转入通用 TALK 信号，
而 autonomy 没有邮件发送适配器，导致原邮件回调永远不执行。现将邮件能力准确标为
active，仍经原 QUIET/活跃窗口/DND/角色主动开关与周契约准入，执行仅走邮件子系统。
无论 autonomy 是否开启，scheduler 都经 run_shadow_tick：已迁移的聊天触发器只入队，
不执行旧回调；原生邮件/明信片可继续交付。没有恢复 retired assistant speech outlet。
周契约未到期（已发送、退避、租约或窗口外）时不再落入事件理由旁路。

`current`：GET `/observability/mail-weekly`（state.read）显示配置齐备、scheduler 开关、
窗口、scope_ready、当前周状态及下一次重试，沿用 `/observability/mail-executions` 查阶段。
周信是“每 ISO 周最多成功一封，到期可候选”，不是固定星期保证送达；阻断可导致本周未发。
原手动 trigger 仍只排队测试信号，不会直接发信，不能再按旧文档把它当 SMTP 验证入口。

`observe`：本地配置中 mail/scheduler 已开启且 SMTP 字段齐备，历史台账最后记录为
empty_content/quality_rejected，没有 SMTP 成功证据。本次仅 mock 回归，不发送真实邮件，
不修改收件地址、凭据或运行配置；部署后下一次合法调度需要观察生成质量和 SMTP。
`roadmap`：Task/Agent authored artifact 邮件迁移尚未完成，不把通用 TALK 信号当邮件实现。
管理面继续使用邮件配置和只读观测；桌面/手机不用新增发送控件，未跨仓修改。

验证：41 项邮件/周契约/gating/信号回归通过；相邻测试文件另有一个既有 MCP 静态版本
断言失败（要求早期 brief-195 版本字串），与邮件执行无关，未修改该旧断言。
## 小红书分享工具（2026-09-11）

`current`：read_xiaohongshu 为默认关闭的 info 工具，管理面工具页通过
GET/PUT `/settings/xiaohongshu` 配置读取服务与数量上限；开关共用
`tools.read_xiaohongshu.enabled`。返回正文摘录、有限图片识别及评论样本。
QQ/desktop/mobile 共用既有探针/工具循环、暴露白名单和 origin 闸门，无新客户端协议。
元数据观测复用 `/observability/api-calls?caller=read_xiaohongshu`；remote_status
明确为 not_checked，配置就绪不代表站点登录或连接健康。详见 docs/xiaohongshu-reader.md。
`observe`：需用户部署并登录 xiaohongshu-mcp 读取服务后实测；本次未开启真实功能。
真实帖子与真机效果、评论完整性不作完成承诺；评论始终标记样本，图片失败明确降级。
`roadmap`：视频转录、完整评论遍历不在本次范围；无跨仓原生客户端代码修改。

## IME Tailscale deployment (2026-09-11)

current: Admin Observation home now displays IME reception, device filtering, pagination and collapsed escaped draft content. Independent ime-main sensor token is in ignored secrets.local.yaml (ime_pairing). Receive-only ingress is enabled. Local and Tailscale HTTPS empty batches returned 204. The updated IME permits 100.64.0.0/10; older APKs require an update.

Validation: 10 backend tests passed; Chromium cache-cleared UI verified records, escaped/collapsed content, empty/error states using synthetic API data. IME unit tests and Debug build passed with Android Studio JBR.

observe: Phone installation, pairing, actual drafts and offline retry still require device validation. Desktop/mobile main apps do not consume this inbox; no WS/ack or memory changes.
