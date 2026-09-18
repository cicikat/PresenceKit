# 229 — 单角色文件、自有空间与 Agent 自主能力扩展

日期：2026-09-18。状态：工单已编制，功能施工未开始。

本单历史权威见 [Agent Runtime](../docs/agent-runtime-architecture.md)。

前置假设：[9.17 后端综合工单](2026-09-17-backend-audit-session-scope-work-order.md)全部收尾。本次按该假设设计，但没有替前单勾选、宣称其实际完成；开工时必须复核其最终提交与 scope 合同。当前源码核对基线为 `e35d0ec`，下述缺口届时按差异复核。

执行规则：先落工单，再逐项实施；有证据才勾选。A–H 每张子单完成相关验证和差异/换行检查后，立即独立 commit，再开始下一张。运行验收与自动测试分别记账；不得为了勾选而扩大无关修复。此次仅新增此文档，不迁移生产数据、不修改功能。

## 一、确定的产品决策

权限限制能做什么，不规定角色应该追求什么。仍是同一角色的聊天主链与持久工作副链，不引入第二个人格。

| 空间/能力 | 本单目标 | 写入与授权边界 |
|---|---|---|
| backend | 任意时刻可读系统代码、配置、日志及内部文件；主动循环明确告知可使用 | 默认只读；去除按 `config.yaml`、`data/`、项目目录整类拒绝；统一脱敏及极高风险内容拒绝 |
| 外部指定路径 | 可读 OS 权限允许的本机任意普通文件，不要求逐目录 allow_roots | 用户提供路径是通常用法，不增加每次读文件确认或“本轮必须出现路径”的硬锁；不主动全盘扫描；不因此获得写权限 |
| self | 持久、按角色隔离的自主文件空间，自由组织目录/文件 | 授予后可自主创建、读、修改、移动、删除、恢复；可逆删除不逐次确认 |
| workspace | 用户/项目授权工作目录 | 延续操作级 grant、确认、manifest、预算；self 的权限不能向外传播 |
| core / 权限配置 | 系统代码、鉴权、安全策略及授权来源 | 角色和它启动的任务均不可自行修改；workspace 配置重叠时保护规则优先 |
| Agent task | 聊天和主动循环都能发起工作副链 | 使用现有 Runtime；已有授权内直接执行，越权操作进入真实用户确认，不把模型参数当授权 |

“后端任何时候可读”取消目录类别封禁，但不取消 owner/char/realm 数据隔离。共享系统资料可读；其他角色私有桶与 Dream 隔离资料仍按既有合同保护。不得通过外部绝对路径别名绕过此边界。

本机外部路径指运行后端的机器。remote_server 可以读自身后端资料、写自身 self，不等于可操作用户电脑；保留外部本机文件、process、browser 等部署闸门。手机/桌面提供的路径不伪装成服务器同名文件。

## 二、已核实的现状与待补范围

| 当前落点 | 源码事实 | 本单处理 |
|---|---|---|
| `core/tools/fs_browse.py` | allow_roots 强制；内部 data 整体拒绝；deny_names 用子串拒绝 config/token 等；文本读取直接返回，未统一脱敏 | 改读策略和模型出口，不连带放开 workspace 写权限 |
| `core/tools/toybox.py` | `very_formal_project` 只有 diary/wishlist/doodle 三个枚举；每文件 4000 字；可覆盖/追加，无自由建文件/删除 | 重新定义为 self authored 文档，保留兼容入口后迁移 |
| `core/data_paths.py::very_formal_project_dir` | 路径 accessor 未接收 char_id，返回共享位置 | 不能将历史内容自动复制给每个角色；冻结归属再迁移 |
| `core/post_process/toy_autogrow.py` | 直接写旧目录，还会裁掉头部；自身有冷却状态 | 改为 self writer 的调用方；保留角色选择权，退出第二写入权威 |
| `core/tools/reminder.py` | add 已调用 Runtime scheduler；get/mark_done/prune 仍操作 legacy JSON；当前 add 使用默认角色 | 统一权威、冻结调用角色；与前单重叠部分按最终基线扣除 |
| `core/tool_dispatcher.py` | reminder 仅注册 add；已有 workspace CRUD/undo、process、browser、自管理及其他领域写工具 | 补 list/update/cancel/restore，而非只给 UI 增按钮 |
| `core/agent_runtime/scheduler_capability.py` | 有 create/list/cancel；list 不返回 content；无通用 update；状态写入与错误处理须补一致性验收 | 角色需要可识别内容的 scoped 查询；管理观测继续脱敏投影 |
| `core/agent_runtime/work_sessions.py` | Task 绑定工作会话、有限 artifact manifest；上下文只存 digest/长度 | 不是已经具备通用 coding worker；补执行循环及可恢复输入引用 |
| `core/agent_runtime/process_runner.py` | 受限 interpreter/program/args，不使用 shell | 本单不伪称有通用 shell；coding 可用受限执行器，无法执行的命令明确报告 |

以上是定点源码核查，非全量写入审计。既有写入还包括 memory mutation、chat artifact、garden、硬件/设备、能力自管理等；A 单补全角色实际可达的入口、落盘及权限矩阵，不能再沿用“唯一写出口是 toybox”的旧描述。

## A — 基线与能力合同

先读 tools/security_model/security、agent-runtime-architecture、data-taxonomy、memory 并发章节；按变化读控制面与接口总账。

- [ ] A1 复核前单完成证据和最终 SHA；记录与本单现状表的差异，只处理剩余缺口。
- [ ] A2 枚举 `_TOOL_REGISTRY`、autonomy 可用集合、post_process writer、scheduler writer、Runtime adapter；记录 origin、角色来源、realm、部署、读/写/删/执行、物理路径 accessor、确认方式、回滚和观测。管理 API 存在不等于角色可调用。
- [ ] A3 落定 backend/external/self/workspace 权限合同和稳定拒绝码；读取脱敏与写入授权分开；grant 由服务端冻结 principal 获取，模型不能指定 owner/char/realm 或自签授权。
- [ ] A4 自有空间确定为 Reality 的 owner+char 桶，经 `get_paths()` accessor 和 data registry 登记；建议逻辑布局 `self/AGENT.md`、`notes/`、`habits/`、`ledger/`，这些目录只是示例，无业务枚举限制。用户内容与系统维护的 revision/trash/audit 分库存储。
- [ ] A5 规定配额与预算的默认值、上限及授权调整方式：文件大小/总量/数量、历史保留、读取字节/时间、任务 token/时间/步骤/并发/费用。耗尽可查询可调整，不能无声裁掉角色记录。
- [ ] A6 更新对应权威文档的拟议合同，完成链接/差异/换行核对并独立提交。

## B — 统一只读访问与敏感信息出口

依赖 A。以 `fs_read/fs_list` 兼容升级，backend 默认读可用，allow_roots 降为外部常用目录/发现提示，不再是外部普通文件读取的唯一准入。

- [ ] B1 独立解析 backend 与外部目标；配置显式关闭仍生效，旧配置迁移展示 effective state。保留文件分页、目录深度、条数、字节和耗时限制；不默认枚举全盘。普通未知扩展名先做受限文本检测；PDF/Office 等走已有安全解析器或明确 unsupported，不承诺任意二进制均可理解。
- [ ] B2 建立统一 sensitive-redaction 服务：结构化键值及文本上下文识别 API key、token/Bearer、password/secret、cookie/session credential、URL 认证参数、Authorization header；配置嵌套/数组、多行、日志与代码字面值均纳入。保留字段名、模型名、代码结构、正常数字和英文。
- [ ] B3 私钥、密码库、浏览器凭据库、keystore 等采用精确文件类型/内容判定拒绝或整体隐藏；高风险判定不得只靠扩展名。禁止旧 `token` 子串规则误杀普通源文件。脱敏失败返回拒绝，不退回原文；不声称能识别所有未知秘密编码。
- [ ] B4 脱敏先于截断、分页、模型调用及任何可读回缓存；多行秘密跨页仍不泄露。逐项接入 fs、workspace_read、self read/prompt、Agent 输入/产物/命令输出与其他可绕回同一文件的工具；复用已有脱敏而非各工具各写一套。审计/异常/观测不保存原始秘密。
- [ ] B5 规范 Windows drive、UNC、ADS、设备路径、junction/symlink/reparse、hardlink 和路径替换竞态；普通读不因跨 allow_roots 拒绝，仍不能通过别名读凭据或跨角色桶。UNC 可能涉及网络认证，不当作普通本机文件隐式访问。写保护不能只做字符串前缀判断。
- [ ] B6 使用合成秘密测试配置/代码/日志正常可读且秘密不出模型请求、工具结果、cache、错误及审计；覆盖 PEM 跨页、脱敏故障、并发替换、非文本、大文件、目录限额、scope 和 remote 行为。复用 `tests/test_fs_browse.py` 等，替换旧整目录禁止断言。
- [ ] B7 同单提供只读 effective/redaction 观测（版本、计数、原因，不含正文）；更新 tools/security_model/控制面，验证后独立提交。

## C — self 持久空间及通用文件工具

依赖 A/B；提供 `self_list/read/create/update/move/delete/restore`（名称施工时统一），不新增记账/音乐/待办专用系统。

- [ ] C1 实现 scoped accessor、存储和通用相对路径工具；支持自定目录与文件名，首次使用创建空间。默认授予本角色 self 操作，管理员可撤销；不依赖 danger 模式才能写自己的笔记。
- [ ] C2 写入用锁、原子替换、expected_revision 冲突检查；移动同时校验源/目标，目标覆盖明示。删除进入有界回收站，支持按 revision 恢复；self 内可自主删，不强加每次用户确认。不可逆清空依单独策略。
- [ ] C3 校验目录每层与临时文件，处理 links/reparse/hardlinks 和竞态；禁止 self 指向系统文件、其他角色或 workspace；审计/版本库不能经 self 工具改写。OS 权限不足明确报错。
- [ ] C4 自主创建可执行文本不等于获准执行；process 必须再次走 Runtime capability。所有操作附 principal、origin、causation、revision 与结果元数据；状态/历史能在后端只读观测。
- [ ] C5 注册 examples/keywords/effect 和角色 schema；聊天与 autonomy 的实际 discovery 都能发现并调用。配额超限不自动删老内容；历史清理保留恢复策略。
- [ ] C6 验证增删改移恢复、重启持久化、多角色/realm 隔离、冲突、损坏状态、磁盘失败、撤权和逃逸；检查脱敏链，独立提交。

## D — self-authored AGENT.md 上下文与旧笔记迁移

依赖 C。`self/AGENT.md` 是角色自己编写的工作习惯，不是仓库 AGENTS.md，也不是修改权限的入口。

- [ ] D1 增加有 `_layer`、长度预算、裁剪/消融及 revision 标记的上下文层；显式标记 self-authored 来源，低于系统安全/权限和用户指令。聊天、主动循环、工作副链复用同一 scoped 版本；冻结本轮快照，修改从下一轮/下一任务生效。
- [ ] D2 内容允许决定习惯、笔记组织与工作方式，不能改 grant/manifest/预算或伪装用户确认；文件内引用不自动递归加载，不执行指令字符串。进入模型前执行 B；文件为空/缺失正常，损坏有可观测降级。
- [ ] D3 dry-run 盘点三个旧文件与 autogrow 状态，生成归属、hash、目标和冲突报告；历史共享文件只能按可验证历史归属迁移，归属不明保留待认领，不能复制给所有角色或按当前 active 猜测。
- [ ] D4 备份后可重入导入 self/notes 等；旧 tool key 做到新文件的薄兼容映射，不双写；迁移冻结旧 writer，autogrow 改调统一 self writer，去除静默头部裁剪，允许角色停用该习惯。
- [ ] D5 保持 authored 与用户事实/长期记忆的来源边界：不自动固化为 identity/episodic；如果触及已有记忆写入流程，遵循 provenance 与 assistant 脱敏规则。迁移报告、回滚方法和源文件保留期限明确。
- [ ] D6 验证重复导入、归属、冲突、崩溃恢复、回滚不覆盖新版本、prompt 优先级/预算/跨角色和三种执行链；独立提交。

## E — 备忘录重新定义与完整生命周期

依赖 A/B；与 C/D 内容区分：无时间的自由笔记放 self，有到期/重复语义的提醒归 Runtime scheduler。self 写“明天提醒”不会隐式创建定时任务。

- [ ] E1 提供角色工具 list/get/add/update/cancel/restore；返回稳定 schedule_id、revision、正文安全投影、到期时间、重复规则和状态；内部 task_id 仅关联，不让角色靠文字匹配删除。
- [ ] E2 principal 由 frozen scope 传入到 store/dispatch；授权内角色可自主修改、取消自己的提醒，包括用户交办的本角色提醒；保留修订记录及恢复，不要求用户再次说“删除”。不能操作其他角色/realm 的提醒。
- [ ] E3 更新正文/时间/周期使用 CAS 和原子一致性策略，scheduler payload 与 Task Manager 状态不可分裂；cancel/delete 的用户语义是停止未来提醒并可恢复，不物理删除审计；恢复生成可运行的新生命周期关联，不能复活已完成 lease。
- [ ] E4 明确 due 与 update/cancel 的竞态：未交付旧 revision 作废；交付前复核 revision/取消状态，已进入发送的标记 in-flight，已发送不能撤回。修正重复提醒 task 终态与后续轮次关系，避免 task 完成后仍靠旁路发送。
- [ ] E5 legacy JSON 做 dry-run、备份、明确历史归属与幂等导入；禁止失败回落双权威。统一查询及 prompt/管理面读者，防止新增成功但列表看不见；已完成/取消不再次调度。
- [ ] E6 覆盖时区、过去时间、跨年、重启、重复规则、并发增改删、取消/发送竞态、持久化失败、跨角色；同单给后端观测，更新文档，独立提交。

## F — 从聊天/主动循环调用可执行的 Agent task

依赖 A/B/C；复用 Task Manager、Work Session、workspace、process_runner，不另造任务数据库。D 的 authored prompt 可随后接入，但 F 最终验收需包含它。

- [ ] F1 注册 `start_agent_task`、`get_agent_task`、`cancel_agent_task`；入参为目标、授权 workspace_id、任务类型、有限输入引用和请求预算。服务端取冻结 principal/causation，解析 registry manifest 和 grants；模型不能注入 shell、任意 manifest、确认票据或路径越权。
- [ ] F2 补齐真正的 bounded coding worker：加载脱敏上下文→模型规划/工具调用→获准读写/受限程序执行→产物检查→完成/失败/待确认。选择既有角色模型路由，保留 char_id。任务 receipt 创建成功不等于 coding 已完成。
- [ ] F3 Context digest 之外新增有期限的 scoped 输入/产物引用，支持恢复必要输入；不把原始聊天、凭据或执行全文塞进 Task receipt。按既有 store 边界保存，所有模型出口接 B。
- [ ] F4 发起工具立即返回 task_id/status，不等待长任务阻塞回复。可查询进度、结果摘要、产物引用和验证状态；任务结束通知走既有 Interaction/new ingress，保留关联而不复用原 turn，不直接写入记忆证据。
- [ ] F5 授权 = origin/realm/deployment/角色策略/workspace grant/manifest/实际操作/预算交集。审批绑定 principal、动作、目标、payload digest、有效期和 grant revision；模型传 `confirmed=true` 无效。撤权后下一步停止，重复票据不能重放。
- [ ] F6 同一调用重试幂等，同目标的新请求可建新任务；限并发、递归派生、步骤/token/时间/费用；每步验证 lease/cancel。重启未知副作用保留 outcome_unknown，不盲重跑；部分产物和可回滚变更清单保留，不能宣称所有副作用可撤销。
- [ ] F7 不新增无限 shell。本单编码验收用现有受限 process capability；若实际 executor 不能覆盖所需安全边界，明确 unsupported，另列受控 shell/sandbox 扩展，不用字符串转义冒充隔离。
- [ ] F8 验证聊天及 autonomy 发起→worker 实际修改授权样例文件→运行获准检查→产物/摘要返回；覆盖拒绝/审批/撤权/超限/取消/重启/重复请求/离线结果通知。独立提交。

## G — 控制面、工具可达性与完整验收

依赖 B–F。

- [ ] G1 后端集中展示 backend read、external read、self、Agent task 的 configured/effective、拒绝原因、配额、grant revision、脱敏版本；复用现有观测端点，按敏感度选 scope；凭据和私有正文不进入公共观测。
- [ ] G2 检查聊天工具发现、autonomy admission、模型 function calling 不可用的降级；主动提示明确“可读后端、可整理 self、可发起授权工作任务”，不强制每天写账本或使用固定业务目录。
- [ ] G3 用户确认复用真实受鉴权渠道；若客户端缺确认 UI，明确等待管理面审批，不能自动放行或假称客户端支持。结果摘要复用普通聊天路径；仅有新客户端字段/交互时才改对应端与协议总账。
- [ ] G4 同步 tools、security_model/security、agent-runtime-architecture、data-taxonomy、prompt-layers、memory（仅受影响部分）、feature-control-surface 与受影响接口文档；修正历史“229 不新增端点”在续篇实施后的适用范围，保留历史事实。
- [ ] G5 复用 fs_browse/toybox/agent_runtime workspace、work_sessions、task_manager、process_runner 既有测试；只补覆盖不足的回归。阅读 dev-environment，使用支持的 Python，隔离测试数据。tag 规则若改动运行 run_eval；纯新增层按其裁剪/消融回归。
- [ ] G6 场景验收：读脱敏配置解释自己的模型路由；按外部路径读普通文件；自主创建账本与音乐笔记并删改恢复；修改 AGENT.md 后下一轮生效；修改/取消提醒且旧时间不发送；聊天及主动启动实际 coding task；未授权 core 写入被拒。
- [ ] G7 管理面静态改动统一 bump 对应缓存版本，启动/复用本地服务硬刷新并验收。逐项记录自动测试、真实服务、浏览器、桌面/手机哪些完成、哪些 not-run；缺口按实际范围记 known-issues/接口总账。完成后独立提交。

## H — 删除候选与迁移收口（本轮仅列候选）

依赖迁移验收及明确删除范围授权。未授权不执行；不因新增 self 自动删除生产历史。

- [ ] H1 删除不再有读写者的 toybox 固定容量/枚举实现，保留必要兼容别名；同删仅测试旧限制的断言与文档。
- [ ] H2 删除 legacy reminder JSON 的 writer/reader、mark_done/prune 旁路及旧 scheduler 回退，前提是迁移报告和所有读者验收通过；连同僵尸守卫/测试/配置说明收口。
- [ ] H3 删除旧 fs 的整类目录封禁和重复敏感字符串规则（由 B 替换，不留下两个策略权威）；保留有效 scope/路径/高风险拒绝回归。
- [ ] H4 删除 autogrow 直写旧目录/静默裁剪实现及错误“唯一写出口”描述；保留角色自主整理和统一 writer 测试。
- [ ] H5 生产旧文件物理清理另交付精确清单、备份和恢复证明；未授权只保留只读档案。完成相关验证后独立提交。

## 交付证据表（施工时填写，不提前勾选）

| 子单 | commit | 定向验证 | 运行验收/限制 |
|---|---|---|---|
| A | 待施工 | 待执行 | 合同与能力矩阵 |
| B | 待施工 | 待执行 | 模型出口秘密不泄露 |
| C | 待施工 | 待执行 | self 生命周期 |
| D | 待施工 | 待执行 | prompt 与迁移恢复 |
| E | 待施工 | 待执行 | 提醒竞态与交付 |
| F | 待施工 | 待执行 | 真实 coding worker |
| G | 待施工 | 待执行 | 管理面/消费端按影响面 |
| H | 未授权删除 | 不执行 | 精确范围另确认 |

每次提交前逐文件比较普通 diff stat 与 ignore-cr-at-eol stat，执行 diff --check；只暂存本单文件，不覆盖并行修改。失败与未执行项如实保留，不能用源码存在或 mock 成功替代运行验收。
