# 工单 330 · PresenceKit Mobile 自动记账：开源方案审计与原生接入

> 日期：2026-11-16<br>
> 优先级：P1<br>
> 范围：`cicikat/PresenceKit-mobile`（主）、`cicikat/PresenceKit`（必要的最小后端接口）<br>
> 类型：开源参考审计 + Android 原生能力 + Flutter UI + 可恢复的数据同步<br>
> 目标：在**现有 PresenceKit 手机应用**里实现微信 / 支付宝等支付通知的可选自动记账，不要求用户另外安装一款记账 App。<br>
> 与 **329** 的关系：329 负责记忆时效与 Tool Loop；330 独立施工，不擅自改写 329 的记忆机制。

## 决策结论（330-V2 · 固定选型，不再让 Agent 自选）

**主参考仓库：`huanghhcri/SmartLedger`，MIT License。**
- 上游：`https://github.com/huanghhcri/SmartLedger`
- 审计时 `master` HEAD：`d3a3ba009aa6463ab576390d7bc57df9523783eb`（施工时克隆后固定这个 commit 复核；若后续升级应单独审计）。
- 已定位真实核心文件：
  - `app/src/main/java/com/smartledger/service/NotificationParser.kt`（支付通知解析/置信度）
  - `app/src/main/java/com/smartledger/service/DedupHelper.kt`（跨渠道去重）
  - `app/src/main/java/com/smartledger/service/PaymentNotificationListener.kt`（通知事件接收）
  - `app/src/test/java/com/smartledger/service/DedupHelperTest.kt`（去重回归参考）
- **决定：移植解析规则与去重思路到 PresenceKit 自己的 Android Kotlin 模块，必要时经许可证审查移植小范围源码。不得整合 SmartLedger App、其 Compose UI、数据库、主 Activity 或保活体系。**

**次要参考：`FridayKoi/WhereIsMyMoney`（Apache-2.0）**，用于研究监听授权体验、人工纠错、CSV/XLSX 账单导入；不与主方案混成双监听。审计时 `main` HEAD：`e29ae0315fd66605057cd86c29466bd0b048b8e0`。

**暂缓：`Skybaby-T/AutoBook`**，仅研究 OCR 设计；未核实可用的代码许可证前不得复制源码。V1 不使用支付 App 无障碍操作或自动截图。

### 必须遵守的落地顺序

1. **克隆（Codex 执行，非用户手动准备）**：在 `<外部参考目录>/SmartLedger` 克隆主仓库，定位本节指定 SHA 并只读审计；备用仓库若需比较再克隆到 `<外部参考目录>/WhereIsMyMoney`。如目标目录已存在，先检查工作区，不强行覆盖。
2. **模块抽取/适配（不是“跨 App 接桥”）**：从上述源文件梳理输入、规则、测试与边界，将需要的算法移植或重写到现有 `<手机仓库>/android/app/src/main/kotlin/com/presencekit/mobile/` 的独立 payment/finance 模块；保持本应用包名和构建体系。
3. **Android 原生接线**：由 PresenceKit 自己的 `NotificationListenerService` 接收通知，在本地先筛选和去重，再将结构化候选写入独立持久队列；不要接触/复用它已有的 `MobileNotificationService`（后者负责角色主动消息）和 `YexuanAccessibilityService`。
4. **Flutter 接桥**：新增小范围 `MethodChannel` 能力（可选独立 `presence_mobile/finance`），只传权限状态、候选列表、用户确认与同步状态；通过现有 controller/service/widget 边界接入生活记录或财务入口，**不照搬 Jetpack Compose 页面**。
5. **后端接桥**：按 330-C 的合同新增专用财务候选 API 和持久化；同一笔交易保持 stable operation_id。Flutter/Android 和后端之间用现有 trusted-origin / scoped-token 体系交互，不用 SmartLedger 的独立数据库当云端真相源。
6. **联调验收**：匿名通知 fixture → 本机生成候选 → 同步 → 后端唯一入账 → Flutter 可见/可编辑 → 角色授权查询。没有真实 Android 通知监听真机验证的阶段标明 `NOT RUN`。

**不移植：** SmartLedger 的 `READ_SMS`/`RECEIVE_SMS`、应用内自更新与下载安装权限、常驻保活/重启 watchdog、悬浮窗确认实现、支付操作、独立 Room 数据库、Compose UI；严格限制新权限，保留 PresenceKit 自己的设置与后台工作机制。

**技术适配注意：** 源 `NotificationParser.kt` 的 `ParsedPayment.amount` 使用 `Double`，不适合作为正式流水的金额真值。移植时应在受控十进制解析后转为 `Long amount_minor` 或后端 Decimal，不能把 Double 原样存入正式财务表。源去重算法可作为参考和测试素材，但跨渠道是否同笔要保守判断，不能仅按金额与时间粗暴合并。

**最终架构**：系统支付通知 → PresenceKit Android 原生监听/解析 → 本地候选 SQLite → PresenceKit 后端财务领域 API → Flutter 生活账房页 / 角色授权查询。**不需要另外安装 SmartLedger，也不需要独立运行它或使用 IPC/HTTP 在两个 App 间通信。**

---

## 0. 交付原则（先读）

**这不是“找一个开源记账 App 把界面搬过来”。****主仓库已经固定选定 SmartLedger**。开源审计只确认选定版本的代码可移植边界、依赖与许可，除非出现无法克服的技术或许可阻断，否则不得重新选型。要能实际接收通知、处理消费候选、持久保存、同步后端，并在现有移动端页面中查看和纠正。

- **禁止新开分支**；使用当前工作分支，按模块独立提交。工作区已有改动不能覆盖。
- 用户自研代码使用现有三仓；外部开源克隆／阅读／试验代码统一放在 `<外部参考目录>` 下，**不得**把整个第三方 App 源码复制到 PresenceKit。路径按实际仓库位置确认，不强行重命名。
- 开工先读两个仓库各自的 `AGENTS.md`、`ARCHITECTURE.md` 以及移动端 `docs/android/native-capabilities.md`、`docs/mobile/flutter-structure.md`、`docs/backend/integration.md` 和后端 `docs/life-records.md`、`docs/three-repo-interface-catalog.md`。
- 复用既有主题配色、卡片、设置项、页面路由及状态组件。不要让 `lib/pages/app_shell.dart`、`MainActivity.kt` 或后端统一调度器再次成为巨型业务文件。
- 保持原有聊天、FCM / SSE、`MobileNotificationService`、`mobile/poll`、ACK、购物车辅助、无障碍屏幕感知、`LifeRecordsStore` 的数据契约不变。
- **默认关闭**自动收集；系统通知使用权由用户自行授权，可以随时暂停、关闭和撤销。不自动付款、不读取密码 / 验证码，不绕过 Android/支付 App 限制。
- 不得宣称“自动监听 = 微信或支付宝官方账单 API”。通知可能缺失、折叠、延迟或不包含完整商家信息。

## 1. 参考仓库与开源许可

| 来源 | 地址 | 查证到的能力 | 本单用途 | 许可证/处理 |
|---|---|---|---|---|
| SmartLedger | https://github.com/huanghhcri/SmartLedger | 多渠道通知解析、营销误报过滤、聚合通知、跨渠道去重、模糊交易确认；Kotlin / Room | **已选定主参考，优先移植解析/去重与测试样例** | MIT；如复制实质性代码需保留原许可及著作权信息 |
| WhereIsMyMoney（我钱呢？！） | https://github.com/FridayKoi/WhereIsMyMoney | 微信/支付宝通知监听、交易入账、误识别纠正、手动记录；Kotlin | **仅作备用参考：授权、手工纠错与导入** | Apache-2.0；按其 LICENSE / NOTICE 要求保留归属及修改声明 |
| AutoBook（SKY 自动记账） | https://github.com/Skybaby-T/AutoBook | 通知解析、截图导入 / 本地 OCR、可选无障碍截图骨架 | **仅评估备用 OCR 思路**，不作为第一版依赖 | 检查时仓库根目录未发现 LICENSE；未确认授权前禁止复制其实现代码 |

**开源审计必须实做：**

1. Clone 到 `<外部参考目录>`，记录 upstream URL、精确 commit SHA、许可证、Android 最低版本、构建命令、第三方依赖与来源。
2. 逐个定位真实的 `NotificationListenerService` / parser / duplicate detector / Room repository / permission flow，区分 README 声称和代码实际实现。
3. 输出一张能力比较表：支付渠道、收支 / 退款识别、通知折叠处理、重复防护、误报、异常、隐私、测试质量、许可证、接入难度。
4. 对每个候选明确 `reuse / adapt / rewrite / reject` 决策及理由。许可证不明或关键行为不可证实时选**独立实现**，不能直接搬运。
5. 如引入代码，维护 `THIRD_PARTY_NOTICES.md`（源文件、SHA、使用片段、许可证和本地改动说明）。复制代码前必须确认许可证与项目发布方式兼容。

## 2. 真实项目现状（施工前复核）

本次审查的 `PresenceKit-mobile` 主分支已有：

- Flutter `lib/controllers/life_records_controller.dart`、`lib/widgets/life_records_widgets.dart`、`lib/services/life_records_service.dart`。
- 原生 `presence_mobile/life_records` Channel；`LifeRecordsStore` 的 SQLite outbox、独立同步、JobScheduler、后端 `/life-records` 能力校验及本机缓存。
- `MobileNotificationService` 专供 PresenceKit 自己的**主动消息通知**；它不是用于监听微信支付通知的服务，不能混用或接管。
- `YexuanAccessibilityService` 已承担其他屏幕任务；本单第一版**不接入无障碍监听支付成功页面**。
- 当前 `LifeRecords` 是饮食 / 账单截图 / 购物车等用户记录；**不要无条件把支付通知伪装成图片 OCR 账单**，也不要绕过现有 `revision` 和权限控制。

后端已有 `read_life_records` 供角色读取生活记录；角色也可能已通过 `self_db_create_table` 创建 `inventory_log`。**该表是角色自有库存草表，不等于正式财务流水**；不得因为本单上线而迁移、删除、重命名或覆盖它。未来可通过明确的关联接口展示库存与消费，但本单不做自动扣库存。

## 3. 本轮范围：交付最小可用的自动记账链

**要做：** Android 通知监听授权 → 本地筛选解析 → 候选交易的持久化与去重 → 后端最小化接收 / 查询 → Flutter 待确认与流水列表 → 用户可修正 / 拒绝 → 后台可靠同步。

**暂不做：** 完整理财报表、投资账户、自动抓取银行账单、读取支付 App 内部数据库、无障碍截图自动识别、自动从购物通知生成 SKU 库存、自动用聊天消息充当付款证据。复杂预算与金融资产管理留给后续单。

### 3.1 Android 原生隔离模块

- 新建职责单一的监听服务，例如 `PaymentNotificationListenerService`，继承 Android `NotificationListenerService`，仅监听系统允许传来的通知事件；用 manifest 服务声明与 `BIND_NOTIFICATION_LISTENER_SERVICE` 绑定。不要把业务塞到 `MainActivity.kt`。
- 接口层在用户明确授权前完全不启用；关注 `isNotificationListenerEnabled` / 设置跳转 / 用户回到 App 后的真实状态刷新，**普通的 POST_NOTIFICATIONS 权限不能代替通知读取授权**。
- 筛选白名单支付渠道，不能只靠包名 + 金额就认定交易成功；拒绝广告、红包活动、推送优惠券、余额提醒、验证码、个人聊天等。
- 将通知原文停留在设备端解析流程。一般只保留规范化交易候选，必要的来源摘要应脱敏、短期保存，禁止向后台上传全量系统通知列表。
- 系统通知更新 / 分组 / 重发必须防止重复；监听重连后不应把旧通知全部当作新消费。
- 在 Android 版本或 ROM 无法提供可用通知内容时明确降级到手动记账 / 导入账单，不猜测金额。
- 不启用短信读取、广泛应用列表扫描、自动点击支付按钮或无障碍绕过系统安全窗口。

### 3.2 解析与候选记录模型

归一化交易对象至少具有：

```text
candidate_id (本机持久唯一 ID)
owner_scope (账号 / 服务端地址；不由通知内容决定)
source_app (渠道标识)
source_event_key / fingerprint (用于幂等，不包含原始全文)
occurred_at (原始通知时间，保留时区来源)
observed_at (设备实际捕获时间)
direction (expense / income / refund / transfer / unknown)
amount_minor (整数最小币种单位；未知则为空)
currency (如 CNY；无法判断则为空)
merchant (可为空)
category (可为空)
confidence / reason_codes (解析证据和不确定性)
status (candidate / confirmed / ignored / duplicate / sync_pending / sync_unknown)
linked_transaction_id (已确认为正式交易时填写)
```

- 金额不能用 `float` 计算；禁止将退款简化为普通收入，转账、信用卡还款等不得直接当作消费。
- 各种通知文案要有版本化规则、fixture、解析结果及“拒绝原因”；不在未审计的真机通知上硬猜格式。
- **两级去重：** (A) 设备同一通知事件 / 相同事件更新的精确去重；(B) 微信支付 + 银行扣款的跨渠道关联。B 类只在证据足够时合并，金额相同但确为不同时间购买的交易不能被误合并。模糊冲突进入待确认。
- 允许修改金额、方向、分类、商户；修改有版本记录，不得丢掉已经确认的交易 ID。
- 把状态明确分成“检测到了通知”“待确认候选”“服务端已记录”。这些不是一个成功状态。

### 3.3 本机可靠队列与后端最小接口

- 借鉴 `LifeRecordsStore` 的事务、operation_id、持久化回执、重试、冲突和断网恢复，但**不要直接复用其仅支持图片生活记录的 wire 格式**；若现有基础设施可安全抽取公共队列组件，先审计耦合再复用。
- 本地 SQLite 需要保证“先落盘、后尝试上传”；失败可重试，同一个候选使用稳定 operation ID，不因网络超时就生成另一条付款。
- 后端按既有 scoped-token / owner 鉴权、trusted origin、数据路径及备份策略新增 `finance_ingest` 领域的**最小 API**（建议 `/finance/candidates` 命名，仅供实现参考，先审计现有 API 后定稿）。
- 服务端持久化、幂等键、源数据指纹、revision、删除 / 纠错与只读查询必须在事务范围内验证。禁止以客户端传来的 owner/char 任意访问其他数据。
- 账本原则上属于**用户**而不是一个角色；角色通过得到授权的工具读取，不能把流水写在 `character_self_db` 当唯一事实来源。角色记忆中不得重复存整套账单。
- 生产接入期间，后端配置负责能力 `enabled/effective` 的真值，客户端只持有用户设备端收集开关；服务端关闭时本机保留未发送候选，不得假报同步成功。
- 新 token 权限按最小范围设计，不能要求手机使用 admin 凭证。跨仓协议、错误码、回执数据、恢复与兼容规范需补文档。
- 如果本轮后端工作量较大，可优先完整实现 `candidate` 收集 + 本地 UI + 假服务端合同测试；**不能**在没有持久后端时把 UI 标成已成功云端记账。最终 MVP 验收仍须真正的端到端写入与查询。

## 4. Flutter 页面与操作体验

目标是**把自动记账加入现有手机应用**，不是照搬 SmartLedger / Compose 的主题。原则是清晰、和现有 UI 一致、少点击。

### 页面建议（可沿已有生活记录入口扩展）

1. 生活记录中新增「自动记账」入口或子页：状态、今日候选数、待确认数、本月已确认支出；统计必须基于服务端真实记录或明确标注仅本机。
2. 设置项：自动记账开关（默认关）、通知读取授权状态、渠道范围、同步状态、暂停 / 恢复、删除待确认项、导入 / 手记入口。
3. 待确认列表：金额、渠道、商户、方向、时间、为什么待确认，支持确认、修正、忽略；不确定候选不能悄悄计入月度支出。
4. 流水列表：按日分组，支持按月 / 分类筛选、点击查看来源与修改历史，区分已同步、待同步、失败和被合并。
5. 失败和缺失要可读：未授权、某支付 App 无通知、受 ROM 限制、断网、401/403、后端未启用、源通知不完整，不使用通用“出错了”覆盖。

**复用要求：** 优先抽取 `life_records_widgets.dart` 及现有设置组件的卡片 / 列表 / 主题色 / 字号 / 页面路由，避免再造一套冲突的视觉规范。控制器与网络服务分离；原生监听服务不直接驱动 Flutter widget；不在聊天页面每次消费后强行插入角色消息。

## 5. 和角色的配合边界

- 角色可以在用户允许时查询消费记录、纠正误记、按分类汇总，但财务事实来自新持久账本，而不是角色自己的文本推断。
- `inventory_log` 继续保留。支付通知通常只能证明“某金额支付成功”，**不能证明买了什么、买了多少**，因此不能自动减少泡面数量或推断餐数。
- 后续可用明确的交易 ID 将购物小票、生活记录 `record_id` 与库存项目关联；本单仅预留关联字段，避免一次性扩成库存、会计、OCR 三合一系统。
- 角色不应擅自代表用户执行投资、转账或消费；此单只是**记录已发生的支付信息**。

## 6. 测试矩阵（必须）

| 场景 | 预期结果 |
|---|---|
| 通知权限未给 / 被撤销 | 不监听、不上传；UI 真实提示并可跳设置恢复 |
| 微信支付 16 元成功通知 | 生成 16.00 CNY 支出候选，证据字段完整 |
| 支付宝促销文案出现“20元” | 不生成消费候选 |
| 微信支付 + 银行通知同笔支付 | 足够证据时关联为一笔；疑似时转待确认 |
| 两分钟内两次相同金额消费 | 不得错误合并 |
| 通知更新、折叠、重新连接 | 同事件精确去重，无重复流水 |
| 退款、红包、转账、信用卡还款 | 不得混作普通消费；不确定则待确认 |
| 金额被截断 / 商户缺失 | 不猜金额或来源；待确认，允许用户补全 |
| 网络断开、切换、后端 500/429 | 本地持久保存、可恢复退避，不丢单也不重复 |
| 客户端超时但服务端已提交 | 用同一个操作 ID 对账，不生成第二笔 |
| 多角色 / 切换账号 / 切换后端 | owner 权限与本机作用域隔离；不串账 |
| 后端禁用、token 过期 | 不伪造成功；UI 可诊断、可恢复 |
| 手工修改、撤销、删除 | 版本可追溯，统计正确，无重复新增 |
| 关闭权限后恢复、杀进程、重启 | 非承诺实时，但数据状态可恢复且不偷偷重新开启 |
| 320px 宽屏、大字、深浅主题 | 与既有组件对齐，可读，无溢出和低对比度 |
| 安装 Android 14 实机 | 权限操作与通知抓取真机验证；未跑则标 `NOT RUN` |

测试要求：Android 单测（解析 fixtures、事件合并、权限状态、持久队列）；Flutter controller/widget 测试；后端 API 幂等 / 授权 / 事务 / 查询测试；跨仓协议 fixture；`flutter analyze`、`flutter test`、Android debug APK 构建，及后端相关测试。不要仅凭 mock 测试称“微信真机自动记账已实现”。

## 7. 施工分段与提交

**330-A：固定 SmartLedger 的开源审计和克隆。** 在 `<外部参考目录>` 克隆并 checkout 已指定 SHA，验证选定仓库的许可证、核心解析与去重代码；不重新开展无目标选型。输出 `docs/mobile/auto-bookkeeping-open-source-review.md`：候选比较、license、commit SHA、真实可复用模块、风险及选择依据。先给出可移植/需改写的明确清单，再进入 330-B；不可直接跳过审计开写。

**330-B：原生收集与本地队列。** 通知监听、权限开关、规范化解析、精确去重、加密/最小化持久化、后台恢复；配套单测。禁止挤入现有主动消息通知服务。

**330-C：后端候选账本与 API。** 最小持久数据、幂等、校验、鉴权、冲突、观测、备份；跨仓合同文档与测试。

**330-D：Flutter 页面与联调。** 原有生活记录 / 设置组件复用；状态展示、待确认、修改 / 忽略、已确认流水、异常解释；回归 UI、网络和权限。

每阶段独立 commit、附测试证据，禁止新建分支。若两仓的工作区暂时不允许同时改动，先完成可独立验证部分，并把剩余依赖作为 `BLOCKED`，不假报端到端完成。

## 8. 最终交付清单与验收口径

交付：

- 开源审计报告（含许可证与具体 SHA）；如引入代码则附 `THIRD_PARTY_NOTICES.md`。
- 手机端 Android 原生监听 / Flutter UI / 测试与构建产物说明。
- 后端持久化 API、权限与幂等测试、跨仓接口文档。
- 可复现的支付通知匿名测试 fixtures 与误报 / 去重测试。
- 真实 Android 设备验收表，区分 `PASS`、`FAIL`、`NOT RUN`，不要把构建成功当真机成功。
- 数据目录、备份 / 恢复、卸载 / 换机注意事项、回滚操作。
- 变更文件列表、模块职责、commit SHA、尚未实现或受系统限制的功能。

**最终验收：** 用户在手机上主动授权通知监听之后，能在 PresenceKit Mobile 内看到真实识别出的待确认支付记录；确认后该交易在可靠存储中只出现一笔，断网重连与应用重启不会重复入账；用户可查看、修正、忽略并查询后端已保存的流水。未授权或后端不可用时，系统明确呈现真实状态，不采集额外隐私，不影响角色聊天和原有生活记录功能。

---

### 参考文档（Codex 施工时重新校验最新版本）

- https://github.com/huanghhcri/SmartLedger
- https://github.com/FridayKoi/WhereIsMyMoney
- https://github.com/Skybaby-T/AutoBook
- https://developer.android.com/reference/android/service/notification/NotificationListenerService
- https://source.android.com/docs/compatibility/15/android-15-cdd
- https://github.com/cicikat/PresenceKit-mobile
- https://github.com/cicikat/PresenceKit
