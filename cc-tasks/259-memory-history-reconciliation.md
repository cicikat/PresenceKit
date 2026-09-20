# 259 — 历史记忆盘点、分批整理与首夜回填

日期：2026-09-19。状态：A 只读盘点、B 基础可恢复状态机、C 隔离校准与生产准入冻结已实现；D–F 的生产运行、语义回填和退役仍未完成。依赖 [258](258-character-memory-dossiers.md) 的受控存储、工具、后台与实时接续。

本单单列旧数据运行工程：数据量、历史格式、来源缺失、进度和恢复与新增量不同；语义判断仍交给同一角色、同一个 capability，不另造分类模型或另一套记忆权威。当前实现只在隔离测试数据上运行；没有生产扫描、生产备份、生产模型调用或生产回填。

## 原则与来源

先建立完整清单，再分批读取必要正文。原始对话证据、旧摘要和角色感受分层；有 source_event_ids 就回查，无来源标 legacy_unknown，不能把旧概括导入成“确实发生”的原始事件。复用已有 Memory Event 历史迁移能力，但它与本单语义整理分开计数：导入事件不等于主题整理完成。

范围候选：Reality event_store、旧 event_log（含可读取归档）、mid_term、episodic、storyline、user_identity。life records/资料与实时感知按各自原有权限、来源合同和引用方式接入，不把所有业务库吞进一个总库。self 笔记只作带标签的角色理解，不自动升为事实。Dream、web、coplay 与思考存档保持既有隔离；不因历史回填解除隔离。

原文被处理过不意味着无需再看：用户更正、源 revision 改变、档案拆合、规则变更都会产生定向复查。只重跑受影响范围，不每夜全库扫描+全量模型重写。

## 进度合同：处理状态与语义去向分开

每条源项使用 scope + store kind + stable source ID + source revision/hash 标识，时间、旧文件 locator 仅用于定位；没有稳定 ID 时按版本化导入规则生成，避免不同分块制造重复事实。清单记录 snapshot/watermark、原始数量、可读性和所属批次。

| 处理状态 | 含义 |
|---|---|
| pending | 尚未处理，不得等同“无价值” |
| running | 已领取，有 task/attempt/lease；过期后查提交账本再恢复 |
| committed | 判定和产物/不产出理由已耐久提交 |
| retryable_failed | 超时、限流、写失败等，可预算内重试 |
| deferred | 缺来源、矛盾待核实、需要人工决定；保留原因与重访条件 |
| excluded | 确定超出 scope/source policy 或不可解析，明确 reason，不算已理解 |

源变更后保留旧处理记录，在新 revision 下生成 pending；取消/撤权暂停领取，不把未处理项批量标 excluded。运行完成、源项处理完和语义质量达标分别记录。

| 语义去向（可组合） | 由角色判断的依据 | 系统行为 |
|---|---|---|
| attach / create | 有明确经历或可复用主题 | 关联现有档案或自主命名新档案，引用证据 |
| revise / contradict | 新证据改变旧理解，或用户明确更正 | 替换当前理解，旧版本退出正常召回 |
| duplicate | 同一经历的重复记录/摘要 | 引用已存在 occurrence，不重复计数；不删原文 |
| evidence_only | 一次性细节或暂不值得归纳 | 仅保留证据及查找入口，不强制分类 |
| tentative | 有可能的模式，证据不足 | 保存候选理解/感受，不能冒充稳定事实 |
| retire_derived | 旧摘要空泛、误归属、已失效或被证据否定 | 可逆退役派生条目，记录理由与依赖影响 |
| needs_review | 来源丢失、矛盾无法解决、归属不清 | deferred；不猜测、不丢弃 |

“丢弃”在本项目第一阶段指退出派生记忆/正常召回，或不产出新概括，不表示物理删除证据。未分类不代表失败，模型自信不等于已核实。程序不以字数短/情绪弱/低频直接删除事件。角色决定分类和理解，处理台账只是记录它做过什么，两者不矛盾。

## A — 只读历史清单与工作量估算

- [x] A1 按真实 scope/source 列出上述存储的计数、字节、时间范围、可读性、来源关联覆盖率、旧格式及隔离项；不打印私有正文、真实标识或本机路径到 track 文档。
- [x] A2 区分证据条数、独立经历数（尚未知则标未知）、派生摘要数、归档文件数，禁止把不同分母混算。旧摘要溯源不足单列。
- [x] A3 定义版本化清单与源变更检测、回填 snapshot watermark、新增量分界和 manifest 分片；盘点只读，不创建/迁移源证据库。
- [x] A4 选取经脱敏的代表样本：近期/陈旧、重复/矛盾、来源缺失、跨年/迟到、活跃主题长链、不同角色；先估 token，模型校准留到 C。
- [x] A5 形成首夜候选范围和剩余历史范围，报告未知项；新增持久盘点状态须有只读观测。独立提交。

## B — 可恢复批次与进度台账

- [x] B1 经 sandbox/data registry 落地清单、每项状态/去向/理由、attempt、目标档案 revision、operation receipt、规则版本、输入 digest、last_error、重访条件。正文保留在各权威存储，运行台账不复制。
- [x] B2 批次小而有界，按源稳定排序；同主题跨批次可查询已有档案和相关旧证据，不能每批生成一本重名档案。成员列表和主题名不作为幂等键。
- [x] B3 接入 258 的原子派生提交和 durable operation receipt；跨库崩溃先 reconcile 再重试。模型成功但写失败不得标 committed；进度写失败不能导致重复计数。
- [x] B4 状态互斥且覆盖整个冻结清单：total = pending + running + committed + retryable_failed + deferred + excluded。分类去向可组合，禁止用去向数量冒充完成数。
- [x] B5 同时报告 committed/total、excluded/total、deferred/total、剩余可执行项及新增量积压；全部排除不能显示“记忆全部整理完成”。删除/源更新后旧完成标志不继续代表当前版本已处理。
- [x] B6 管理面元数据展示暂停/恢复/预算/错误/批次；memory.read 受控查判定与证据。覆盖重启、重复执行、锁冲突、坏数据与断电提交恢复；独立提交。

## C — 隔离演练、备份与首夜准入

- [x] C1 在隔离副本 dry-run；原生产源不改。保存 proposed patch、理由、血缘、样本统计；“生成建议”与“已生效”明确区分。
- [x] C2 同角色副链小样本校准调用耗时、输入/输出 token、重试率和人工抽检质量。重点检查重复事实、感受冒充事实、错误丢弃、分类碎片化、旧结论未退役。
- [x] C3 输出实际速率区间：预期工时依据待处理 token/批次实测耗时和共享配额，留出限流/失败/前台让行余量；不以记录数乘固定常数假装精确。首次预算未定不得无限额运行。
- [x] C4 首夜范围优先：明确更正/撤回依赖 → 活跃主题及关联旧证据 → 最近 30 天候选 → 剩余历史时间段；30 天仅初始建议，按实际盘点冻结。保留部分预算公平推进冷门主题，避免永久饥饿。
- [x] C5 对准备改动的生产派生库和相关状态做一致性备份，记录安全备份位置（本地运行配置，不进 track 文档），实际验证恢复；复用既有 backup-state 要求，不能拿旧快照冒充本批备份。
- [x] C6 冻结上线日期、时区、首夜 manifest、范围总量、角色授权、preset、调用/token/费用硬预算、停止点与恢复策略。仅当 258 B–E、恢复与抽检通过才准入生产。
- [x] C7 独立提交校准和准入记录；未满足则明确 blocker，不能为赶“今晚”跳过完整性保障。

## D — 首夜生产小批次回填与次晨核验

- [ ] D1 使用已验收后台 worker，对首夜 manifest 分批 apply；不经主聊天链，不发送“正在整理/已完成”消息，管理面可主动查看。
- [ ] D2 每批提交后核对源/档案 revision、计数、血缘、进度守恒与预算；异常停止该批并保留其他 scope 的可执行进度。
- [ ] D3 聊天新证据进入增量通路，不能漏在冻结历史 watermark 两侧；前台修改与后台回填冲突时重新读取重算。
- [ ] D4 到晨间截止时间停止启动新调用，有限等待/超时收尾。报告首夜实际处理量、有效档案/修订、重复/证据保留/退役、待核实、失败、未处理和费用；无自动用户通知。
- [ ] D5 以同一冻结分母验收，不把所有 excluded/deferred 当理解完成。检查若干完整事件链、偏好变化、具体计数、反例与原文细查；和上线前同题对比，标注未覆盖历史。
- [ ] D6 与 258 F 共同验收“当晚实际静默运行 + 次晨首夜范围可用”，保留真实运行证据后独立提交。没有真实运行不得勾选。

## E — 剩余历史滚动整理与质量修复

- [ ] E1 按主题与时间窗持续回填，引用展开受预算限制；无关历史不为凑全量一次塞入 prompt。新增量优先，同时为旧积压预留可配置份额。
- [ ] E2 对来源缺失和冲突项保留可查清单，按补证/用户纠正/规则更新定向重试；确实无法核实就长期保留 unknown，不伪造结论。
- [ ] E3 展示全历史固定 snapshot 的处理率及 snapshot 后新增量，预计剩余夜数随实测速率更新；恢复/重启不清空失败或预算。
- [ ] E4 通过冻结样本评估过度泛化、重复、错误关联、召回缺失及 prompt 体积，记录质量问题；必要时回退坏 revision 并重处理依赖范围，不整库覆盖。
- [ ] E5 全历史终验分别列 committed、excluded、deferred 及其原因；仍有 pending/failed 不宣布全量完成，仍有 deferred 不宣布全量理解清晰。独立提交。

## F — 历史退役候选（不执行物理删除）

- [ ] F1 列出已被新档案接管、无独立读写者的旧派生摘要、索引和 writer；与 258 G 对齐，附覆盖/恢复证据和精确范围。
- [ ] F2 先退出重复召回，再按单独批准范围清理；源证据、无法溯源的历史、其他角色与隔离域不在默认删除范围。
- [ ] F3 删除功能时同步移除仅服务它的测试/守卫/配置/文档；生产数据物理清理另行列明备份、保留期与恢复边界。

## 提交与验收记录

每张 A–F 子单完成相关验证和差异/换行检查后立即独立提交，再开始下一张；优先既有相关回归。运行批次记录放 ignored 的受控状态，提交仅留脱敏验收摘要，不提交私有记忆。

| 子单 | commit | 自动/结构验证 | 生产运行 |
|---|---|---|---|
| A | `d404e9b`, `b3f9d00` | read-only inventory / redaction tests；隔离数据上的分母、时间范围、首夜候选与剩余历史 | 未扫描生产正文 |
| B | `19bae90`, `07cda48`, `03e375a`, `2dc503f`, `0120b00` | resumable manifest/status tests；状态写入经 sandbox resolver；逐 source-item seed/list；有界领取/证据查档/lease 收据恢复；管理面缓存清除后桌面/手机布局验收 | 生产未运行 |
| C | `30a5f84`, `f5dc200`, `01a73a6` | verified-backup gate、verify-before-restore recovery drill、manifest freeze/revision gate、ledger transition tests；26 focused tests passed | 仅隔离 fixture；未创建生产 snapshot |
| C2–C4 | `8ef7efb` | 隔离副链校准记录耗时/估算 token/重试/结构质量旗标与速率带；冻结首夜优先级与冷门份额；管理面 `action=calibrate` 去敏展示 | 未调用生产模型、未 apply、未开调度器 |
| C6–C7 | `f673bea` | 生产准入冻结上线日期/时区/manifest/范围总量/grant/preset/硬预算/晨间停止/恢复策略；`action=run` 未准入则 deferred；管理面 `action=admit` | 未跑生产首夜、未开调度器、未发对话消息 |
| D | 本轮提交（代码与隔离测试；D1–D6 未勾） | operator-pass 绕过调度器 enable/夜窗、批次守恒停批、晨间截止收口、增量 watermark 与前台重算测试；管理面展示 `last_closeout` | 未跑生产 `action=run`，无次晨抽检，不得勾选 |
| E–F | E evidence-only 收口；F 未授权 | derived stores have durable evidence-only receipts and explicit reopen path; no physical deletion performed | semantic quality/retirement remain separate follow-up |

## Current blockers

The adapter imports event evidence in bounded batches and records derived
stores as source-specific evidence-only receipts; it never treats old prose as
a new fact. The verified offline snapshot and recovery drill passed, and the
first-night closeout is persisted in the reconciliation ledger. A real
same-scope dossier pass has committed once through the configured cheap
`便宜小模型grok-see` preset. Provider timeouts remain explicit failed receipts;
they do not block evidence completion or get reported as semantic success.

本轮施工（2026-09-20）：只读盘点在隔离数据上报告分母、时间范围、溯源缺失、
30 天首夜候选与剩余历史，不复制正文、不扫生产。dry-run manifest 会把
event_store / event_log / mid_term / episodic / storyline / identity 的稳定
身份写入 scoped `source_items`。有界领取按 store_kind + ingest_sequence +
source_id 稳定排序，并按证据 ID 查询已有档案；标题和成员列表不是幂等键。
lease 过期先对 `processing_commits` reconcile，有收据才标 committed，否则
回到 retryable_failed。写失败释放领取，不记完成。C2–C4 隔离校准可冻结首夜
范围并给出速率带。C6–C7 把上线日期、时区、硬预算、晨间停止和恢复策略写入
准入记录；校准 freeze 仍不是生产首夜。`action=run` 在未准入时 deferred。
D 切片补齐 operator-pass：不开调度器也能跑有界同角色档案 pass，每批核对
守恒，晨间截止后写 `last_closeout`，excluded/deferred 不算已理解。新聊天
证据走增量 checkpoint，不写进冻结历史 watermark。没有真实生产首夜不得勾 D。

未勾选项的准入边界：D1-D6、E1-E5 需要真实首夜/滚动运行、次晨抽检和费用证据；
F1-F3 涉及退役或删除范围，需单独批准。没有对应运行证据或批准，不得勾选。
