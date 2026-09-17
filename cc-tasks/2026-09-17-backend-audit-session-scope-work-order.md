# 9.17 后端综合工单：角色隔离、固定会话与架构债收敛

状态：A 已提交；B 拟议合同已提交（未实现）；C–J 未开始。日期：2026-09-17。
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

- [ ] C1 chat/上传/wake 在入口验证并冻结 scope，贯穿 pipeline、工具、模型路由、turn sink、媒体引用及异步后处理；禁止临时改 active character 实现隔离。
- [ ] C2 历史、reasoning、媒体读取使用授权的相同 owner/char 边界；拥有 turn_id/sha256 不等于读取权限。附件与后续发送作用域不一致时拒绝或明确处理。
- [ ] C3 response/stream/channel/segments 保留关联身份；允许早期无 turn_id，以显式绑定收敛。迟到、重复、乱序、重连、Dream/Activity/group 并发不得相互认领。
- [ ] C4 按 B 落实幂等和 unknown-result 语义；新增 receipt/队列/trace 同单配只读观测。优先复用管理面，无正文、无凭据，展示 capability/effective state 和拒绝原因。
- [ ] C5 补最小集成回归：桌面 A/手机 B 并发、管理面改 C、A 附件/历史/思考仍属 A；未授权角色、撤权、迟到回包、超时重试与附件重复均覆盖。
- [ ] C6 同步控制面/接口文档；若改 admin 静态资源，统一版本更新与浏览器硬刷新验收。未实测明确记录，不以源码检查替代。
- [ ] C7 验证、差异检查、独立提交，交付后端版本、能力值、fixture 和未完成运行项给两端。

## D — P1：244 历史 canonical ID 收尾与三端联调

已有实现不重复建设；解析回归可先做，全链会话验收依赖 C。

- [ ] D1 复用 `tests/test_chat_log_turn_id.py`、turn sink 相关测试：同分钟多轮、多段、无 ID、不同 user/assistant ID、正文伪造、归档关联及角色隔离；无真实覆盖才补测。
- [ ] D2 核对 persisted assistant footer、HTTP turn_id、reasoning archive 的真实关联；transport id 不得填充缺失 canonical ID，旧日志不回写、不按文本/时间补造 ID。
- [ ] D3 确认部署版本；真实发送后重启桌面，从历史每回合唯一入口读取对应思考；无 ID 无推测入口，无归档空态可重试。手机历史对账与通知回放复用现有 D 成果。
- [ ] D4 完成跨端 A/B/C 会话、WS 重连/乱序、前后台 poll/ack、撤权及旧服务器降级验收；证据分别标自动/浏览器/真机/not-run。
- [ ] D5 回填桌面 244、会话交接及运行验收矩阵，后端总账 current/open/observe 与手机工单同步；提交本单实际变更。

## E — P1：specialized LLM 显式角色路由

对应 P1-5、P1-11。先读 `docs/model-presets.md` 及对应 Dream/Agent Runtime 合同。

- [ ] E1 逐调用核对 Dream solo/invariants、Perform、Coplay close 和相关 background worker：记录拥有的 realm、char_id、call_category 及最终路由来源。
- [ ] E2 将已有会话角色贯穿提取、judge、summary 和主 LLM；不依赖 active character，不擅自向不支持 realm 的 API 增加参数。
- [ ] E3 用 A/B 不同路由、任务中途切 active C 的测试证明最终 provider/preset 仍属于原角色；参数存在但未传到模型解析应失败。保持 Dream 不写 Reality evidence。
- [ ] E4 更新必要路由说明，定向测试、差异检查、独立提交。两端仅需相关场景回归，不新增设置 UI。

## F — P1：Dream settings 归属决策后收口

对应 P1-6。当前 `dream_settings._path(user_id)` 未传角色，而物理 accessor 位于角色树。

- [ ] F1 对照产品意图、API、registry、所有读写和实际旧数据，形成 per-user 与 per-character 两种方案及迁移/回滚差异，确认归属后再施工。
- [ ] F2 若 per-character，贯穿请求/dream state 的 char_id；若 per-user，建立真正共享 authority。两者都不得把 active/default 目录偶然位置当语义。
- [ ] F3 设计备份、dry-run、冲突和重入规则；不得无条件复制默认配置到所有角色或删除原数据。
- [ ] F4 覆盖多角色切换/并发、旧配置、读写失败及迁移；同步 taxonomy、控制面与受影响客户端合同，验收后独立提交。

## G — P1：Scheduler 迁移收口与 sensor 死代码删除候选

对应 P1-1/2/3、P2-3/5。先读 `docs/scheduler.md`、`docs/autonomy.md`。

- [ ] G1 按 registry 盘点 signal producer、maintenance、active/compat executor 及真实调用；不把双 gate 判成已复现双发，不移除有效 admission。
- [ ] G2 从 legacy gather 迁出/删除已迁移 speech checks，保留真实 maintenance 与 active letter_writer；逐项证明 force/manual/debug 路径的处理。
- [ ] G3 先列全 cooldown reader 并按角色或真实全局维护语义迁移，再移除不需要的 global 双写；测试多角色互不抑制及必要全局限流。
- [ ] G4 删除候选：sensor return 后旧 LLM/action/send/sink/cooldown 分支及无生产引用 helper；同删仅保护死分支的测试/守卫/文档，保留 signal-first 行为测试。取得该删除范围施工授权后执行。
- [ ] G5 验证每 tick migrated 只产 signal、maintenance 正常、compat 正常、无直接 sensor delivery；修 registry 和 ledger 注释，定向测试后独立提交。

## H — P1：工具决策 authority 与 causation 身份

对应 P1-7/8/12；先读 tools/security_model/agent-runtime 合同。以下按独立小单提交。

- [ ] H1 核对 eligibility 与 final_schema 差异；统一可解释 decision 输出供 schema、管理面、audit 使用，保留执行时权限复查。全局 read-only MCP 继承是现有设计，不擅自改为禁用。
- [ ] H2 回归 global/deployment/self-capability/MCP/autonomy policy、危险操作与确认；覆盖展示允许但执行时撤权。更新控制面和浏览器验收后提交。
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
