# IME 三小时草稿接收（v2）

状态：后端接收与观测已实现；未开启真实上报，未接入 AI 用途。
协议核对来源为相邻 IME 仓库 `docs/draft_sync_v2.md` 与
`app/src/main/java/com/chacha/jadeime/data/DraftSyncRepository.kt`。
早期“交接文档ime_draft_sync_api.md”已经过期，不按旧游标协议实现。

## 接入

默认 `ime_ingest.enabled: false`。管理面「功能总开关」中的“IME 草稿接收（仅存储）”
使用既有 `GET/PUT /settings/feature-flags`；配置键为 `flags.ime_ingest`，热生效。
本次不修改实际配置，也不创建 token、不设置手机 URL、不运行真实传输。

准备运行时为手机分配独立 sensor profile token，其 label 是 device_id；不要与其他
设备共用 label。重装/清除手机数据后使用新的 label，token 轮换保留 label 时保留设备身份。
手机填写完整 HTTPS URL，路径为 `/v1/ime/drafts`。当前手机还要求系统信任证书和私有
网络目标，公网/CGNAT 地址不受支持；后端仍复用既有 HTTP 服务和部署层 TLS。

POST Bearer 需要 sensor.write；一次接收 JSON 数组，最多 2000 条、4 MiB。
每条字段为 id、created_at、updated_at、revision、app_package、source、content，
时间为 Unix 毫秒，source 接受 keyboard/voice/mixed。content 是完整版本，保留空白。
单条最多 100 万字符；拒绝额外字段、非法枚举、负数/非整数 ID、倒序时间和明显未来时间。

按 `(token.label, id)` 事务 upsert，只有更大 revision 替换整条；相同/更小版本忽略。
整批成功提交才返回 204。422/413/503 不能推进手机游标；禁用返回 503；鉴权沿用 401/403。
同批乱序版本不会回退。过期记录直接忽略后确认，避免手机反复发送已过期数据。

## 存储与观测

`get_paths().ime_drafts_db()` 定位独立 SQLite；进程锁配合数据库事务保护整批原子性。
三小时从 updated_at 计算，读取永远过滤过期记录；下一次接收清理过期行。
没有独立定时清理进程，闲置数据库物理行可能保留到下次接收，不承诺准点擦除。
不保存历史版本、不复制到 memory、prompt、event_log 或任务队列。

admin-only `GET /observability/ime-drafts?device_id=&limit=50&before=` 返回
enabled/effective/blocking_reason、mode=receive_only、retention_hours=3、entries、next_before。
entries 含 seq、device_id 和全部原始字段；before 是 seq 游标，动态 inbox 的同 ID 更新
不新增 seq，查看最新修订应刷新第一页。禁用后仍能查看未到期已收记录。
观测是只读，不创建数据库；异常不回显 content。后续用途按真实字段另开工单。

## 闭环边界

现有 sensor/push、owner chat、desktop/mobile WS、通知、relay 不消费此 inbox。
管理面复用动态开关列表；原生桌面/手机客户端不需新增消费设置。
IME 自己已有开关、URL 与配对密钥 UI，未跨仓改动。真实 HTTPS 配对、输入法后台存活、
端到端断网重传仍为 observe；不把接口测试说成已接收真实手机数据。


## IME Tailscale deployment (2026-09-11)

current: Admin Observation home now displays IME reception, device filtering, pagination and collapsed escaped draft content. Independent ime-main sensor token is in ignored secrets.local.yaml (ime_pairing). Receive-only ingress is enabled. Local and Tailscale HTTPS empty batches returned 204. The updated IME permits 100.64.0.0/10; older APKs require an update.

Validation: 10 backend tests passed; Chromium cache-cleared UI verified records, escaped/collapsed content, empty/error states using synthetic API data. IME unit tests and Debug build passed with Android Studio JBR.

observe: Phone installation, pairing, actual drafts and offline retry still require device validation. Desktop/mobile main apps do not consume this inbox; no WS/ack or memory changes.
