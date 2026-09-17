# 9.17 后端综合工单：角色隔离、固定会话与架构债收敛

状态：A/B/C/E 已提交；D 自动化收尾完成、运行联调待执行；F 已提交；G 已提交（G4 删除候选未授权，保持未勾）；H1 已提交；H2 已提交；H3–J 未开始。日期：2026-09-17。
用户已授权按本单施工。每张施工子单完成相关验证与差异检查后独立 commit，再开始下一张。
勾选规则：完成一项且具备证据才勾一项；源码存在、自动测试通过、部署验收分别记账。每张施工子单完成相关验证与差异检查后独立 commit，再开始下一张。

## 输入与复核基线

- [x] 阅读 [后端审计](9.17审计结果.md)、[桌面会话交接](../../Emerald-client/cc-tasks/2026-09-17-session-scope-backend-handoff.md)、[桌面 244](../../Emerald-client/cc-tasks/244-history-turn-id-backend-handoff.md)。
- [x] 对照后端 HEAD `7412339` 的关键入口和接口总账；对照手机 HEAD `e70e083` 的 [21 号工单](../../Emerald-mobile/cc-tasks/21-9.17审计评判与工单.md)及集成文档。原审计基线 `3f4dd95` 不能当作当前全部事实。
- [x] 确认 `chat_log._parse_day` 已解析可信 assistant footer 的 `turn_id`；244 是回归、部署与联调收尾，不重做解析器。
- [x] 确认 event_log 目录、单日和 union/list 路径仍存在 uid-only fallback；`desktop_chat` 仍走 legacy context，上传和媒体读取仍取 active character；Dream/Perform 的显式角色仍有未传入 LLM 调用的落点。
- [x] 确认手机 21 的 A/B/D/G 已有实现/测试完成记录，A5/G5 真机项仍开放。此处仅复用记录，未重跑其测试。
- [x] Agent Runtime 权威文档已有“同一角色副链”表述，根 ARCHITECTURE 仍有未来时态及 Dream Stage fail-closed 描述，按剩余差异修订。

除以上定点核查外，其余审计项作为施工前待复核候选，不宣称全量复审。原始审计、已有删除和其他未提交文件均保留。

## A — P0：封住 legacy event_log 跨角色读取

对应审计 P0-1、P1-10；优先独立修复，不等待新客户端契约。
先读 `docs/memory.md`、`docs/data-taxonomy.md`。落点：`core/memory/event_log.py`、resolver、`core/pipeline.py`、`admin/routers/chat_log.py`。

- [x] A1 明确 legacy 日志合法归属；区分历史默认角色、配置默认角色和当前 active 角色，不随切换重新认领旧数据。
- [x] A2 统一目录、单日、search/get_recent_days、list_days/count 及导出/删除调用的读取资格；非归属角色不得读到 uid-only 数据，canonical 桶仍按 owner+char 隔离。
- [x] A3 修正 `test_event_log_resolver_integration.py`、`test_event_log_union.py` 中保护错误 fallback 的断言；覆盖非默认仅旧目录、双目录、默认合法兼容、不同 owner、历史 API 与 prompt 检索。
- [x] A4 同步 memory/taxonomy 与接口总账；不删除旧日志、不自动分配 ownership。验证默认角色历史仍可用，非默认历史日期也不泄漏。
- [x] A5 运行上述相关回归并记录结果，检查差异/换行，独立提交。

影响：后端修复；桌面/手机历史消费者做隔离回归，无须为本项增加客户端设置。

## B — P1：冻结会话 scope 与身份契约

对应桌面会话交接；是 C/D 和手机新工单的前置。先读 `docs/channels.md`、`docs/security.md`、`docs/interaction-event-model.md`、接口总账及两端协议。

- [x] B1 建立端点矩阵：desktop/mobile chat、上传/图片、媒体读取、wake、历史日期/单日、reasoning、WS/segments、主动通知、mobile activate/poll/ack/relay；列当前 scope 来源、权限、缺口和消费者。
- [x] B2 定义本机固定角色的授权来源、能力/version 声明、角色不可用/删除/撤权响应；客户端不能把可见角色列表当授权。旧服务端不支持时明确提示，不静默发给 active 角色。
- [x] B3 定义请求开始冻结 owner/domain/char_id/request_id；明确 domain 与内部 realm 的映射、group round_id、早期 stream 后绑定 canonical turn_id 的事件/顺序。字段为拟议合同，落定前不得写成已上线。
- [x] B4 区分 request_id 关联、transport msg_id、persisted turn_id；列出重试 ID 复用、幂等键作用域/窗口、相同 ID 不同负载、并发重试、超时/未知结果、附件重复提交的行为与错误码，不承诺 exactly-once。
- [x] B5 明确主动消息所属角色及接收设备选择；核对 queue seq 的真实作用域，决定过滤、消费与 ack 的关系，禁止按角色跳过消息却推进共享 cursor 导致丢失。
- [x] B6 交付可评审字段表、兼容矩阵、错误码和协议 fixtures；更新实际受影响协议与总账，列入手机/桌面接入依赖。Dream settings 归属另走 F，不由本单顺带决定。

## C — P1：后端落实请求隔离和全链关联

依赖 B 合同明确；复用 `core/owner_turn_service.py` 和已有 frozen scope，不另造全局角色切换锁。

- [x] C1 chat/上传/wake 在入口验证并冻结 scope，贯穿 pipeline、工具、模型路由、turn sink、媒体引用及异步后处理；禁止临时改 active character 实现隔离。
- [x] C2 历史、reasoning、媒体读取使用授权的相同 owner/char 边界；拥有 turn_id/sha256 不等于读取权限。`/upload/ingest` 为同一次上传并发送，不另发 upload_id；重复请求由同 session/request receipt 收敛。
- [x] C3 response/stream/channel/segments 保留 request/msg/turn 与冻结 char/domain；早期 stream 允许无 turn_id，以同 msg_id canonical 收敛。Dream/Activity/group 未改，不能认领 Reality session。
- [x] C4 按 B 落实 completed replay、in-flight、payload conflict 与保守 unknown-result；进程内 session/receipt 有界，`/observability/session-scope` 只读脱敏展示 capability/effective、TTL、计数与拒绝原因。
- [x] C5 新增 session scope 回归，并复用 chat/media/reasoning/history/turn sink/WS/auth/wake 回归；覆盖桌面 A/手机 B 独立 grant、active 改 C 不改已签发 scope、未授权/撤权、迟到等待取消、同 ID 重试/冲突/并发和上传 request 去重语义。
- [x] C6 已同步 capability、控制面、API、channels、接口总账与 provider fixture。未改 admin 静态资源，不触发缓存版本/浏览器验收；桌面/手机消费者、真实服务与真机验收均未执行。
- [x] C7 定向回归 262 passed（1 条第三方弃用 warning）；完成换行/diff 检查并独立提交。交付能力 `session_scope=v1`、provider fixture `tests/protocol_fixtures/v1/session_scope.json`；桌面/手机消费者、真实服务、浏览器与真机验收均 not-run。

## D — P1：244 历史 canonical ID 收尾与三端联调

已有实现不重复建设；解析回归可先做，全链会话验收依赖 C。

- [x] D1 已复用 `tests/test_chat_log_turn_id.py`、`tests/test_turn_sink.py`、`tests/test_reasoning_turn.py` 与 session scope 角色隔离回归；包含同分钟多轮、多段、无 ID、不同 user/assistant ID、正文伪造与归档关联。均包含在 C 的 262 passed 中。
- [x] D2 已核对 persisted assistant footer → chat-log `turn_id` → reasoning bind/query 链；HTTP 分开返回 `turn_id`/`msg_id`，无 persisted ID 时仅 mint transport ID，旧日志不回写且不按正文/时间补造。
- [ ] D3 部署版本、真实发送、桌面重启后历史→思考读取：**not-run**。无 ID 无推测入口与无归档空态已有自动回归；不能替代真实运行验收。
- [ ] D4 跨端 A/B/C、WS 真实重连/乱序、前后台 poll/ack、撤权及旧服务器降级：自动隔离/撤权/重试已覆盖；浏览器/桌面/真机均 **not-run**。
- [ ] D5 后端总账已标 current/open/observe；本机桌面 244 与会话交接文件在当前桌面 HEAD 不存在，手机 21 已被并行删除、22 仍在且待消费 C 合同。待两端实际接入时回填各仓与固定 SHA matrix，本轮不伪造完成。

## E — P1：specialized LLM 显式角色路由

对应 P1-5、P1-11。先读 `docs/model-presets.md` 及对应 Dream/Agent Runtime 合同。

- [x] E1 已逐调用核对：Dream solo `dream_turn` / retention 用 `_state_char_id`、`call_category=chat`；invariants `observe`/`_relation` 用会话 `char_id`、`summary`；postcard 用 `chat`；reality continuation 用 `run_llm(..., char_id=)`；Perform 用 `perform`；Coplay close 用会话 `char_id`、`summary`；observer VLM 用会话 `char_id`、`vision`（独立 vision 连接，不经文本 preset）；prefix-retry / agentic 收尾 / relay probe / scheduler `_pipeline_send` 用已有会话或 frozen scope 角色。已有 `dream_summary` / `distill_impression` / `scenario_reconciler` 未重缝。未向不支持 realm 的 API 加 realm 参数。
- [x] E2 已将上述拥有角色贯穿最终 `llm_client.chat` / `run_llm` / `get_model_client`；Coplay commentator 改读 `session.list_active_character_ids`，不读 live active。未改 coplay_watch 扫描语义，未开工 F。
- [x] E3 `tests/test_char_routing.py` 覆盖 Dream solo/invariants、Perform、Coplay close、prefix-retry、agentic 收尾、`_pipeline_send` frozen scope、VLM `char_id` 透传；`char_id` 存在但未传入解析即失败；Dream solo 断言不写 Reality mood/episodic/history/mid_term。
- [x] E4 已更新 `docs/model-presets.md` Brief 30 冻结 `char_id` 段与 `docs/coplay.md`。定向回归 208 passed（不含因当前 venv 缺 Pillow 无法收集的 `tests/test_coplay_observer.py`；VLM 断言改由 `test_char_routing.py` 覆盖）。普通 diff 与 `--ignore-cr-at-eol` 一致。两端无新设置 UI；桌面/手机相关场景回归 not-run。

## F — P1：Dream settings 归属决策后收口

对应 P1-6。当前 `dream_settings._path(user_id)` 未传角色，而物理 accessor 位于角色树。

- [x] F1 已确认归属为 per-character，不是真正共享的 per-user authority。物理 accessor 本就在 `runtime/dreams/{char_id}/settings/{uid}.json`；registry 由误标 `per_user` 改为 `per_char_user`。live active / `character.default` 目录偶然位置不当语义。旧 `data/dreams/settings/{uid}.json` 仅冻结历史默认角色可读。
- [x] F2 `load`/`save`/`set_field` 关键字 `char_id`；pipeline `enter_dream`/`dream_turn`/`build_snapshot` 贯穿已有会话角色。HTTP GET/PATCH/HUD/jailbreak/世界 rename-delete 用 `_active_dream_char_id()`，不新增 query/body char 字段。世界改名只改当前角色 `world_layer`。
- [x] F3 首次兼容读 uid-only 文件时，把当时的 `character.default` 冻结到 `runtime/dreams/global/{uid}/legacy_dream_settings_owner.json`，之后切 default/active 不改认领。不复制到其他角色树，不删除原文件。canonical 存在但不可读时回 `_DEFAULTS`，不穿透 legacy。
- [x] F4 `tests/test_dream_settings_ownership.py` 覆盖多角色隔离、中途切 active、冻结认领、坏 JSON、HTTP 当前角色、世界改名不改 peer、观测无正文。已同步 taxonomy / dream / 控制面 / 接口总账 / security。定向回归 530 passed。无新管理面 UI，不 bump fragment。桌面/手机相关场景回归 not-run。

## G — P1：Scheduler 迁移收口与 sensor 死代码删除候选

对应 P1-1/2/3、P2-3/5。先读 `docs/scheduler.md`、`docs/autonomy.md`。

- [x] G1 已按 registry 盘点：migrated 为 signal producer；maintenance-only 为状态/产物工人；`letter_writer` 为 gating `active` SMTP executor；未注册名走 compat `_pipeline_send` 或拒绝。双 gate 不是 live 已复现双发；未移除有效 admission。
- [x] G2 已从 live gather 删除已迁移发言 `_check_*`；保留天气缓存、sensor 候选、Runtime 备忘录与维护扫描。`letter_writer` 仍由 gating active executor 发送。force=`legacy_tick_should_send(True)` 仍放行；manual migrated 只排队 autonomy；maintenance 返回 queued；未注册拒绝直发。
- [x] G3 发言冷却只写 `{char_id}:{name}`，不再双写裸 `name`。读者 `_is_ready` / `triggered_on_logical_day` / gating / overflow / presence_nag 按角色；维护省略 `char_id` 保持 uid 全局。`ProactiveLedger.can_send(uid=)` 仍是跨触发器全局限流。测试覆盖多角色互不抑制与 ledger 跨角色 gap。
- [ ] G4 删除候选：sensor return 后旧 LLM/action/send/sink/cooldown 分支及无生产引用 helper；同删仅保护死分支的测试/守卫/文档，保留 signal-first 行为测试。取得该删除范围施工授权后执行。
- [x] G5 定向回归 154 + 40 passed：migrated winner 只产 signal、不跑历史 executor；weather/inner_diary 维护正常；sensor `handle_tick` 不直发；compat `_pipeline_send` 仍对未迁移名开放。已修 registry/ledger 注释与 scheduler/autonomy/memory 文档。G4 未执行。独立提交。

## H — P1：工具决策 authority 与 causation 身份

对应 P1-7/8/12；先读 tools/security_model/agent-runtime 合同。以下按独立小单提交。

- [x] H1 已统一 `AutonomyToolDecision`：`tool_eligibility()` 只做 allowlist 准入；schema / `GET /admin/autonomy/tools` / 管理面矩阵 / run audit 共用 `allowed`+`decision_source`（`autonomy_allowlist` 或 `global_read_inheritance`）。全局只读 MCP 继承保留，执行前仍复查当前矩阵。定向 83 passed；管理面浏览器验收留 H2。
- [x] H2 回归 global/deployment/self-capability/MCP/autonomy policy、危险操作与确认，以及展示允许但执行时撤权。控制面沿用 H1 `AutonomyToolDecision` 合同，未改字段。管理面浏览器验收在当前代码隔离端口完成。
- [ ] H3 Workspace causation 使用真实 turn ID 或明确的 request fingerprint 类型；更新类型验证、调用者、测试及无正文 lineage 观测，不把 hash 伪称 reality_turn，也不重写历史证据。验证后提交。
- [ ] H4 盘点 tuple execute 的全部生产消费者，迁往 structured outcome，保留 origin/confirmation/unknown 语义；零生产依赖且兼容窗口结束后才提出删除 wrapper 和旧 shape-only 测试。此轮不直接删。

## I — P1/P2：Memory 迁移退场标准与观测所有权

对应 P1-9、P2-9；保留现有业务 authority，不建万能 ledger。

- [ ] I1 定义 shadow coverage、未映射 legacy、迁移完整度、fallback 命中率的分母、阈值、观察窗口和回退条件，批准后才能作为退场门槛。
- [ ] I2 保持 event_store 为 evidence、旧栈为当前 recall、shadow/proposal 不进 prompt；通过隔离与迁移测试后才考虑切换，不因重复存储直接删旧召回。
- [ ] I3 为 owner receipt/task/work session/process/autonomy/proactive/event/action/mail/API ledger 列 ownership map、关联 ID、保留期及现有观测入口；无缺口不新增台账。
- [ ] I4 文档/指标检查与必要回归完成后提交；没有达到退场阈值的项保持 open。

## J — P2：文档真值与兼容删除候选清单

对应 P1-4、P2-1/2/4/6/7/8/10/11；不重复修已完成措辞。

- [ ] J1 对照实现更新 ARCHITECTURE 与 agent-runtime 文档 current/roadmap，核实 Dream Stage sandbox 状态；memory path census 与 taxonomy 一致。
- [ ] J2 分别盘点 source_policy.record_rejections 的现存调用、user_profile.get_period_info shim、context.max_turns alias；列消费者、迁移条件、守卫/测试/文档联删范围，获授权后再删。
- [ ] J3 保留 Dream tension alias、sensor raw fields、flat llm synthesis 兼容；定义客户端最低版本/配置迁移方法/弃用提示/退出版本，不凭空指定发布日期。
- [ ] J4 核实 core/paths.py 是否仍无生产引用；删除或显式 experimental 二选一。核实 spend/mandates 无 writer 后按历史/roadmap 标注，端点删除前查全部消费者及权限；不删历史数据。
- [ ] J5 明确 self_management.enabled=false 是关闭 overlay、恢复 global 行为，不是关闭能力；优先修说明/展示，若改字段名须另附旧配置兼容与客户端接入。
- [ ] J6 文档链接/结构/差异验证后提交；若改 UI 行为须额外完成缓存版本和浏览器验收。删除候选尚未授权的保持未勾选。

## 执行顺序与交付边界

1. A 优先；B 合同先于 C 和手机接入；D 解析回归可先做，真实联调在 C 后。
2. E、F 决策、G 盘点、H、I、J 可独立规划；共享 pipeline/router/文档的施工串行，避免覆盖并行工作。
3. 手机对应 [22 号配合工单](../../Emerald-mobile/cc-tasks/22-session-scope-backend-coordination.md)，桌面沿原交接接入。本次不修改桌面代码。
4. 首次测试前按 `docs/dev-environment.md` 选相关环境和测试；不为文档任务跑全量。每单提交前逐文件比较普通与 ignore-cr-at-eol diff stat、执行 diff --check，只暂存本单文件。
5. known-issues 只登记核实的剩余缺陷；接口总账记录实际 open/roadmap/observe。工单勾完源码项不代表部署/浏览器/真机已验收。
