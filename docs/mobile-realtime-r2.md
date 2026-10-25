# 手机前台实时分发 R2

2026-10-25：本地实现为 **final_only 安全降级**。未部署、未发布；真实设备、代理及首可见正文 p50/p95 对比仍 observe。没有开启模型原始 token 流，不能声称已降低模型首字等待时间。

## 单次执行与范围

手机仍 POST `/mobile/chat` 或 `/upload/ingest`，沿既有 owner/session/request、conversation gate、Pipeline、工具、turn sink 执行。订阅、重连、分页和读历史均不触发生成或工具。桌面 `/ws/desktop`、设备 WS 与手机连接槽独立。

只有 turn sink 的 Reality 主聊天、非群聊、`fanout=all|broadcast`、`durable_mobile_mirror=true`、critical_written=true 且存在未墓碑化 assistant evidence 的消息可以投影。channel allowlist 为 mobile/desktop/qq/scheduler/sensor/watch；user_chat 另限 mobile/desktop/qq。活动私有输出、Dream、未绑定角色和无可靠持久证据的内容不进入手机 WS。

## 内容与历史

最终展示字段在现有 scope 的 event_store SQLite 内保存到 `mobile_finals` 表；这只是展示投影，不产生新记忆事件、不进入检索/prompt，不是第二个队列或 R3 设备游标。内容来自已过滤完整输出；拒绝 tool/think/analysis/system/draft 等内部帧和未闭合内部标签，不向手机转发桌面 delta。存储和发送都是成功持久化之后的同一条结果；投影失败时不发 WS final，HTTP/队列照旧继续，观测记录失败。

`GET /mobile/history`（memory.read + X-Presence-Session）返回 `{messages,next_before}`，每页最多50；`before`/`history_index` 只用于该授权历史分页，无投递/ACK语义。按插入索引降序，可跨日，不截断正文；包括 id/msg_id/turn_id/request_id、owner/char/domain、content/display_text、artifacts、timestamp、critical_written，无 seq。

原 `/chat-log/dates` 与 `/{date}` 保持按日懒加载并补上投影中的稳定身份及展示字段。Flutter 首次启动仅补最新一页；旧日详情由原历史分页按需读取；同一前台会话断线后补齐内存历史边界之后的全部页。后端重启和其他消费者 ACK 清空队列不会删除展示投影。原 assistant evidence 墓碑/删除会通过 SQLite trigger 删除投影；读取也 join 未墓碑化原事件。不承诺恢复被清理的源或失效媒体；下载仍走现有鉴权端点并可能返回不可用。

已知覆盖边界：上线前没有投影的旧记录保留原历史降级；无稳定父 turn 的独立贴纸/behavior 不能凭正文猜关联，继续沿既有队列/inbox。输入附件沿原 media_refs/sha256 历史合同；本单补齐正文与 artifacts 展示，不伪造缺失资源。业务记忆与展示投影不互相成为写入来源。

## WS 合同与身份

`/ws/mobile` 只接受 Bearer header、`X-Presence-Session` 和随机 `X-Presence-Client`。权限为原 chat scope，不授予 ws.desktop/admin。client_type 固定为 mobile，owner/char/domain 来自服务端 grant；客户端无法自报更宽范围。每个帧写入前重新校验 token、grant、owner、角色可用性和开关。相同 token label + client instance 的新授权绑定立即使旧连接代际无资格；不同手机 instance 不互踢。

服务端先发 `ready`（mode=final_only/session_id/generation/char_id/domain/client_type）；后续 `final.message` 为持久展示信封，外层重复 generation/scope；`ping` 每20秒，客户端仅回 `{type:pong,generation}`。45秒无响应关闭，写入预算3秒；最多16个连接、每个64条待写；满后断开改走 HTTP。没有 ACK、输入、执行或模型调用协议。客户端必须先接受 ready，再核对每一帧的连接代际和范围；旧帧不能写新 UI。

手机最终身份在持久化完成后绑定，当前 `msg_id=turn_id`；ready 不创建消息。final 与 HTTP、队列及历史以同一最终身份逐段合并。桌面自己的临时 stream msg_id 不进入手机身份空间。未来若增加安全增量流，早期只能用 request_id+stream_id 临时关联，持久化后显式绑定 turn_id/msg_id，不能用 chunk 序号代替消息身份。

## 生命周期与失败收尾

Flutter 独立 MobileRealtimeController 管理资源，AppShell 复用已有 pause/resume/reset。后台关闭 WS、取消升级/HTTP补读并失效本机代际；回到前台重新绑定当前角色、HTTP补收，再接收 WS。前台异常用带抖动2–34秒退避；旧服务端/鉴权失败至少60秒，关闭WS策略每30秒复查，后台无重连计时器。10秒升级预算会真实关闭 HttpClient，也关闭迟到成功的 socket，取消不影响发送请求。

生成失败、持久化失败和取消不发成功 final；本次没有半句气泡。HTTP 明确 critical_written=false 时转 execution_outcome_unknown，不展示成功正文；沿原 request_id/历史对账，绝不自动重发。WS断开不代表生成取消。既有 HTTP 期限后停止生成指示、保留原用户输入的失败/不确定状态；已收到持久 final 后，迟到 HTTP 失败不能降级它。未来的临时流必须同时具备 failed/cancelled/unknown 终态与本机期限，移除暂态正文，不留永久生成气泡。

## 控制面与观测

管理面「消息分发与客户端设置」→「手机前台实时连接」，GET/PUT `/settings/clients` 的 mobile_realtime 字段（admin），配置 `mobile_realtime.enabled` 默认 true，返回 enabled/effective/mode/default，热更新。手机通道总开关仍优先；关闭实时即回到 HTTP，不删除历史、不影响发送、附件、轮询、native inbox/通知。手机 GET `/mobile/realtime`（chat）发现 enabled/mode/protocol，旧服务端不支持则 HTTP 降级。

`GET /observability/mobile-realtime`（state.read）显示连接数/限制、持久投影失败、范围拒绝、队列满、已发送 final、历史页和 enqueue→send 累计耗时。`POST /mobile/realtime/metric`（chat）只接受枚举 kind + 0–600000 整数 value，拒绝额外字段，无正文、凭据或请求标识；内存每类最多200样本，观测返回样本数/p50/p95。指标包括 connect_ms、reply_apply_ms、recovered_messages、fallback；reply_apply_ms 是本机发送到 transcript 应用的耗时，**不是屏幕真实呈现或模型首 token 基准**，不能拿此数字替代工单42真机基线。连接和模型/生成耗时分别看原 owner_chat/timing 与本接口。

## 本地证据与遗留

后端相关173项通过（含新增R2、ACK/队列、mobile合同、session、chat-log、scoped auth、角色字面量和换行守卫），最后局部收尾R2的28项再次通过；手机相关124项通过（含R2的7项），分析零问题。证据用例为 tests/test_mobile_realtime.py 与手机 test/mobile_realtime_test.dart，涵盖真实本地WS、黑洞升级资源关闭、重复段落、持久化失败、迟到HTTP错误、旧代、撤权、跨日分页和队列ACK清空。

管理面使用实际 fragment/JS、隔离本机fixture，开/关和热更新回显已浏览器实测；不读写生产配置。实际手机、V2Ray、Wi-Fi/流量、Doze/进程被杀、真实模型及双端联合体验未验收。桌面协议/代码不变；后台 Android 原链路不变，不扩大此次原生验收范围。删除候选仅为将来安全增量流可替换 final_only 后的单一路径清理，本次不删除 legacy 传输或42的降级逻辑。
