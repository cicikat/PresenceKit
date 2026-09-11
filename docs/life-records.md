# 生活记录 v1

管理面「服务配置 → 生活记录」维护 enabled（默认 false）、角色只读、手机后台同步和识别后保留原图。图片识别复用模型路由现有 OCR/vision 设置；配置就绪不代表真实中转已经验证。缺失识别配置时任务保持 pending。失败原因和按设备聚合回执位于同页，可按记录 ID 重试失败任务。

## HTTP 与身份

`life_records` 是专用 scope，标准 mobile profile 包含它；admin 隐含全部权限。token 校验在路由逻辑之前，owner_id 必须匹配配置的单 owner，不接受客户端自行选择其他 owner。旧 mobile profile token 随服务重启展开新增 scope；显式 scope 列表需管理员按需补发，不要求手机持有 admin。

- GET `/life-records/capabilities?owner_id=...`：schema_version、enabled、recognition_available、background_sync、effective、blocking_reason、recognition_route。
- POST `/life-records/sync`：沿用工单 245 的 JSON；成功回传匹配 operation_id、record_id 和正整数 revision；删除额外 `deleted:true`。409 在顶层回传 `current_record`。
- GET `/life-records`：owner_id、category、from/to（本地日期）、q、cursor、limit；返回 records、next_cursor。游标绑定 owner/过滤条件和快照，按 ID 稳定排序。响应预算约 1MB。
- GET `/life-records/{id}?owner_id=...`：record（也可为删除墓碑）。
- GET `/life-records/observability?owner_id=...`：无正文/图片/密钥的任务、失败类型、设备回执和操作审计。
- GET/PUT `/settings/life-records`、POST `/settings/life-records/{id}/retry`：admin-only 管理面入口。

单图最大 10MiB，JPEG/PNG/WebP，实际图片格式须与 MIME 一致、像素数有界。字段与十进制字符串校验，amount 不转浮点、不混算币种；items 的扩展字段保留。captured_at 规范到 UTC，校正时保持原始拍摄时间。客户端不能设置 deleted、revision、recognition_status 等服务端字段。

## 存储、恢复与删除

`get_paths().life_records_db()` 的 SQLite user_version=1 保存记录版本、原图 BLOB、任务、幂等回执和元数据审计。图像与识别任务同事务提交；没有先返回成功再保存图片的窗口。operation_id 按 owner 去重，原请求重试返回原回执；同 ID 换内容返回 409。去重不设短 TTL，达到十万操作/1GiB 原图上限显式拒绝。

后台 worker 由 main 启动并在退出时取消；单次识别 120 秒上限，processing 租约 180 秒，到期可恢复。识别成功或失败增加 revision；在提交时读取最新用户校正字段，绝不覆盖已锁定字段。图中文字是低信任数据，不作为系统指令，无工具调用。结果未获用户确认，置信度未知记 null，未知份量/热量/金额不自动估算。

删除生成墓碑、清除原图和任务并屏蔽历史版本检索；运行中模型结果无法复活它。幂等回执仍保留原 ack（含当时记录快照），不等同物理擦除所有证据；后续删除与重试始终按 revision 处理。普通查询和角色工具不会读到这些旧回执。保留策略关闭后，仅在识别成功时清原图；失败原图保留以便修复配置后重试。

首次使用自动建表，无历史数据迁移。升级/回滚前停后端，对该 SQLite 做离线备份；回滚先关闭 life_records，再恢复旧代码（保留数据库备份），不降级写入未知 schema。此模块不共享 mobile ack_seq、聊天锁、通知、支付或记忆固化队列。

## 角色与跨端

`read_life_records` 只读 owner 记录，日期/分类/关键词有界查询；需要全局角色可读开关和原工具暴露/角色权限。群聊禁止使用。只返回用户记录和可校正提取证据，不自动写长期记忆，不把图片中广告或指令变成行为。

桌面通过管理面做配置与观测，不增加第二个设置真值；原生记录列表列 roadmap。手机沿用已有 life_records 独立 outbox/JobScheduler，原聊天/poll/ack/中继不变。管理面已使用实际隔离后端接口清缓存实测。真实手机拍照、Doze/强停/重启、真实模型图片识别仍为 observe；上传后跨端重取原图与淘宝直连导入列 roadmap。
