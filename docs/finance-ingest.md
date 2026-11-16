# 330 财务候选账本 v1

财务真值是用户账本，不属于角色。`inventory_log`、LifeRecords、聊天与 ACK 完全独立。没有自动库存、自动付款、短信/无障碍支付采集或长期记忆复制。

## 身份与控制

「功能开关总览 → 功能总开关 → 自动记账」可直接热切换并搜索定位；其「细分设置」跳到原「消息分发与客户端设置」详细卡片。总览与详情共用同一配置字段，不产生第二个开关。角色读取仍需在详情单独授权。

`finance_ingest` 专用 scope 纳入标准 mobile profile；显式 scopes 的旧 token 需补发，不能升级为 admin。所有路由从配置的单 owner 推导归属，不接受客户端 owner/char。不同角色共享同一用户账本，角色读取另受默认关闭的 character_readable 和统一工具权限约束，群聊/梦境不可读。

后端 `finance_ingest.enabled`、`character_readable` 均默认 false。管理面「服务配置 → 自动记账」经 GET/PUT `/settings/finance`（admin）热更新并显示 effective/counts。关闭即停止同步/读取，数据保留。手机仅维护 origin+owner 隔离的收集开关和系统授权；新通知先落盘，后端拒绝不能标为云端已存。

## HTTP 合同

- GET `/finance/capabilities`：schema_version=1、enabled/effective、blocking_reason、character_readable。
- POST `/finance/candidates/sync`：`operation_id/candidate_id/base_revision/record`，均为持久操作；金额 amount_minor 是严格正整数分，CNY 或未知 null，禁止浮点。候选可缺金额，confirmed 必须有金额/币种/已知方向。严格字段白名单，禁止通知原文。
- 成功回执：operation_id/candidate_id/revision/record，revision=base+1。超时重发同一载荷/同一 ID 返回最初回执；同 ID 换内容返回 409。不能因超时创建另一笔。
- GET `/finance/candidates?cursor=&limit=50`：按 candidate_id 字典序分页，最多200；返回 records/next_cursor。包括墓碑。并发新增可能在游标前，下次从空游标刷新可补读；无快照承诺。
- GET `/finance/candidates/{id}`：record 和最近100个历史版本。
- 409 `finance_conflict/current_record`：revision、事件指纹或操作冲突；手机保留本机修改，显式采用服务端版本后再编辑。
- 401 无效 token、403 缺 scope、422 字段错误、503 `finance_disabled`（capabilities 则 effective=false）。500/429/断网保留队列退避；未知回执只能保持待同步，不自动视作入账。
- GET `/observability/finance`（state.read）：各状态数量和已提交操作数，不含商户、金额或通知文本。settings 复用此统计。

Record：candidate_id、source_app（精确白名单或manual）、source_event_key/fingerprint（SHA256）、occurred_at/observed_at（Unix毫秒）、timezone_offset_minutes（原通知当地偏移）、direction（expense/income/refund/transfer/unknown）、amount_minor、currency、merchant/category、confidence/reason_codes、status（candidate/confirmed/ignored/duplicate/deleted）、linked_record_id（未来显式关联预留）、linked_transaction_id/revision（服务端真值）。商户/分类上限120/80字。通知来源/原时间/指纹在创建后不可改。手工校正金额、方向、商户、分类、状态增加版本，transaction ID 保持不变。

仅 confirmed 计入正式流水。ignored/deleted 是可追溯撤回，不物理擦除历史/幂等回执。退款独立于收入，转账/还款不计消费。跨渠道只凭金额/时间无法证明同笔，保留候选和 possible_duplicate 提示，人工忽略重复项。相同通知事件更新不重复新增；聚合通知和旧通知回放不做猜测补账。

## 存储、备份与恢复

`get_paths().finance_db()` → `data/runtime/finance/ledger.sqlite3`。SQLite candidates/operations/versions 同一 BEGIN IMMEDIATE 事务，返回成功前持久提交；无外部模型或网络副作用。v1 首次建表，不迁移既有库存/生活记录。路径注册 canonical/reality/global，owner 是行级键；已纳入 backup-state 的 data protection root，无新增备份实现。

需要备份时按现有 backup-state create/verify/coverage 离线流程，不默认执行生产备份。恢复同一快照后重发 outbox，revision 冲突须人工处理，不能盲目覆盖。回滚前在管理面关闭，保留 SQLite，回退本单程序提交；旧版本不写新库。卸载手机会删除本机未同步候选；换机先完成同步。服务端已同步记录可分页恢复，客户端仅显示最多1000条缓存，统计明确限定本机缓存。

## 验证边界

定向 HTTP、SQLite/Robolectric 和 Flutter 自动化是合成验收；Android14 真实支付通知、OEM、后台/杀进程/重启、真实角色模型、生产服务均须单独实测。Dev APK 不等同正式发行或端到端真机通过。离线生产数据未用于测试。

330-C：Python3.12 定向 finance/schema/data-registry/dependency/backup-state 共327项通过（`.tmp/finance-backend-tests.log`）。实际隔离管理服务经浏览器登录，在新版「消息分发与客户端设置 → 服务配置总览」确认默认关闭、开启保存、刷新持久、再次关闭的真实回包和可见状态；使用匿名空账本，不调用支付/模型。管理面 fragment/core/settings-center 缓存版本已更新。
