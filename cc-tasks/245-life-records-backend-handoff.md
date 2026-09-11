# 18 · 后端工单：生活记录（proposed，尚未实现）

后端接力文件：`Emerald-presence/cc-tasks/245-life-records-backend-handoff.md`（同内容快照）。后续后端施工状态以该仓文件为准。

依赖手机工单 17；本文件仅是后端交接单，不表示接口已经存在。后端正在施工，本轮不修改其代码。

## 目标

用户主动提供图片 → 持久化识别任务 → OCR/视觉模型 → 饮食、账单、购物车结构化记录 → 用户校正 → 角色按年月日查询。复用后端已有图像识别配置，正式记录和原图在电脑；手机仅缓存与 outbox。禁止将图片内文字作为系统指令执行；不自动支付、不自动写长期记忆。

## 待办（后端施工者逐条打勾）

- [ ] B1：确认 v1 契约，提供 capability 与 mobile scope，迁移/回滚方案。
- [ ] B2：正式记录、原图、识别任务、操作幂等表与 revision；删除墓碑及检索清理。
- [ ] B3：异步 OCR/视觉抽取；保留原始证据、置信度与不确定值，用户修正优先。
- [ ] B4：记录 CRUD、分页日期/分类/关键词查询、冲突处理与设备队列观测端点同单提供。
- [ ] B5：管理面配置 enabled、角色可读、原图保留策略、模型 effective state、任务失败与审计。
- [ ] B6：角色只读工具按 owner/日期/分类检索，饮食估算标注来源；金额精度与币种不混算。
- [ ] B7：桌面设置/只读观测评估并同步三仓总账，标记 current/open/roadmap/observe。
- [ ] B8：手机联调和网络失败矩阵，确认不影响 chat/poll/ack、通知与购物辅助。

## Proposed HTTP v1

所有端点 Bearer，复用可信 origin 和关闭重定向。owner 由 token 校验，不能仅信请求参数。新增专用 mobile 可用 scope（由后端确定并写 scope 表），不要求手机持有 admin。401/403 停止当前凭证重试，429/5xx 有界退避，404/501 视为未接入。

- `GET /life-records/capabilities?owner_id=...` → `{schema_version:1, enabled:true, recognition_available:true, background_sync:true}`。能力不存在或关闭时不发送图片；本机不复制 effective state 判断规则。
- `POST /life-records/sync`：JSON `{owner_id, operation_id, record_id, action:"upsert"|"delete", base_revision, record?, image_base64?, image_mime?}`。单图 ≤ 10 MiB；图片只在新建时发送。record 为下述公共结构。同一 operation_id 重试必须返回原结果，不能重复识别/入账。返回 `{operation_id, record_id, revision, record?, deleted?}`；ack 必须匹配关联键且 revision 正整数，删除必须 `deleted:true`。
- `GET /life-records?owner_id=...&category=...&from=YYYY-MM-DD&to=YYYY-MM-DD&q=...&cursor=...&limit=50` → `{records:[...], next_cursor:null|string}`；日期按 record.occurred_on，稳定排序与快照分页。记录需含 id/revision。
- `GET /life-records/{id}?owner_id=...` → `{record:{...}}`，用于异步识别状态更新与冲突读取。
- `GET /life-records/observability?owner_id=...` → 脱敏任务数、pending/failed、最近 ack 时间、识别路由/effective state、保留策略；无正文/原图/token。管理面用 admin 聚合，mobile 仅能看自己。

记录公共结构：`{id, revision, category:"diet"|"bill"|"cart", occurred_on:"YYYY-MM-DD", captured_at:<UTC ISO8601>, title, note, items:[{name, quantity, unit, amount, currency}], recognition_status:"pending"|"processing"|"ready"|"failed", updated_at}`。quantity/amount 是十进制字符串，可为空；currency 使用明确币种字符串；不凭照片捏造热量、价格、份量。occurred_on 是用户当地日期，可校正，captured_at 不随校正改变。空 category 输入需要后端自动分类的扩展可后续协商，手机一期用户选择类别。

补充字段：`user_edited_fields` 为用户已校正/明确输入的顶层字段名数组（title/note/category/occurred_on/items），识别不得覆盖；`image_mime` 为源图 MIME。amount 是该条明细金额，不自行推导单价/总额。额外 items 字段客户端必须保留。列表可带 `{id,revision,deleted:true}` 墓碑，手机按 revision 清除无待同步修改的旧缓存；409 的 current_record 也可为墓碑。

capability 的 `background_sync` 必须明确给出；false 时系统后台任务不发送图片，前台仍可手动同步。每次同步只发送一个操作，前台 30 秒续传；原生 JobScheduler 在任意网络可用时按系统退避继续，非即时传输保证。原图及 outbox 存于 noBackupFilesDir，单图 10 MiB、全机源图 100 MiB、待办最多 200 项；记录/操作事务写入，原图 fsync。最后一次操作收到匹配 ack 后清理手机源图。长期原图读取接口是后续扩展，本期手机校正正式结构化记录，不能宣称已支持上传后原图的跨端重取。

409 返回 `{detail, current_record:{...}}`；手机保留本地草稿并提示冲突，重新拉取后由用户决定，禁止静默覆盖。后台识别只能更新未被用户锁定字段；revision 增加。识别 ready 是可校正提取结果，不等于角色已确认真实发生。

## 同步与安全约束

客户端 operation_id/record_id 在首次落盘时生成，重试原样复用。超时可能已落库，因此必须先查询幂等表。删除用同样幂等机制；新建在途期间的删除必须排在新建 ack 后。服务端幂等保留期至少覆盖客户端 outbox 最大存活期；一期用户未同步素材不自动过期，因此去重记录随记录/删除墓碑保留，不能短 TTL。容量超限显式拒绝，不丢弃旧队列。

每个队列绑定精确 backend origin + owner；切换后不得把旧图片传到新节点。凭证不进 outbox。任务系统独立于 mobile 消息队列，不消耗 mobile ack_seq；完成状态由查询获取，不新增通知行为。

## 三面闭环和验收

管理面：enabled 默认 false；识别配置缺失显示 effective reason；角色读取开关独立，审计只记 ID/动作/时间/结果。桌面：先只读观测，不造第二个配置真值；后续 UI 列 roadmap。手机：能力未上线时可本地采集并明确待接入，不显示识别成功；权限拒绝/磁盘满/传输错误不丢原图。

测试：原图上传后断线重试、ack 丢失、重启、多个设备 revision 冲突、识别与校正并发、重复删除、token 轮换/吊销、跨 owner 越权、分页完整性、日期跨时区、金额小数、恶意图片指令、原图过大、删除后角色查询、既有聊天通知回归。

淘宝直接导入列 roadmap：需官方 API 能力/授权资质核实与用户授权，不能依赖抓取登录 cookie 或把辅助点击当作购物车数据接口。一期用户上传截图。
