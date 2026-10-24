# 2026-10-24 手机消息投递与代理故障联合审计

范围：只读检查运行代码、隔离故障注入、登记方案；不改运行行为、不部署。后端基线dfba6bc，手机基线1b42f36。完整手机报告位于手机仓 `docs/mobile/message-delivery-audit-2026-10-24.md`（工单41）。

## 已确认的后端问题

- **P1 / open：ACK落盘失败仍成功**。`channels/mobile.py:154-164` 忽略safe_write_json的False；`admin/routers/mobile.py:134-135` 返回ok=true。注入写盘False，ack返回remaining=0，重新poll仍有旧item，携带已确认seq的after则空。它导致确认语义失真/后续重投，不等于已证明当前手机前台卡住由此引起。发送入队失败已在678bd73修复，此处是另一条路径，不能写作已解决。
- **P2 / current limitation：一个共享消费者**。ACK删除全局seq前缀，无device_id游标。双手机实验确认一端ack后另一端取不到；桌面不消费mobile队列，不存在「桌面ack抢走手机消息」。现有单手机合同保持；多设备独立补收需单独设计。
- **P2 / open：串行fanout拖延**。`core/turn_sink.py:173-201` 顺序await；mobile不活跃而作为durable mirror补入末尾时，可被前面的网络发送拖延。slow-channel受控事件实验确认。实际注册顺序见main.py:960-982，mobile活跃时通常在QQ前，不声称所有手机消息都被QQ阻塞。desktop.send带behavior时还可能等5秒动作ack。
- **P2 / observe：桌面旧连接ACK/pong归属**。`channels/desktop_ws.py:259-321` 替换连接后，接收旧帧未校验来源，已有10月18日登记；本轮仍未确定性复现，不改成多端广播。

## 与手机现象的关联

用户补充：前台聊天页面也卡，开V2Ray类代理、服务器地址时明显。手机正常前台onResume会停止原生ntfy服务，因此后台15分钟补偿不是持续前台现象的首要解释。

手机BackendClient在getUrl/postUrl阶段没有总期限，8秒connectionTimeout不覆盖本机SDK后续HTTPS代理CONNECT等待。真实本地假代理接受CONNECT却不响应，16,011ms后仍在途；同一个ChatController第二次收件复用原任务（pollingMobile=true），15秒poll响应timeout尚未启动。此实验显式findProxy，不证明V2Ray的VPN/TUN路径相同。需要实际手机路由/分阶段时序核实；禁止写成「已确认V2Ray导致」。

后端 `admin/routers/chat.py:314` 仅desktop provenance/live origin进入stream；手机等待完整模型输出，首字出现天然更晚。`channels/mobile.py:127-152` 已有condition长轮询，enqueue通知能即时唤醒，无须先增加WS才能减少整条消息延迟。手机前台仍每5秒Timer、一页20条；可先独立收件循环、有界排空和网络取消。

后台独立缺口：signal触发pollOnce失败，返回值被调用侧忽略、pending信号已清；SSE保持connected无新signal时可能等15分钟补偿（受系统调度影响，非硬上限）。原生consume是删除式交接，页面应用前崩溃/旧代丢弃仍靠history恢复。详见手机报告B1/B2；不将历史修复的seen/pending误用、30分钟通知冷却重复列为未修。

## WebSocket评估与依赖

1. **R0（优先）**：手机统一传输期限、真实取消、网络epoch/恢复、分阶段可观测性。DIRECT只能禁应用HTTP代理，不能直接绕VPN/TUN；不擅自更改用户VPN选择。
2. **R1（R0之后）**：连续HTTP长轮询、history并行对账、signal回源失败重试、完整本机inbox/claim-ack。后端ACK失败传播可与R0并行做最小修复；持久队列入队应独立于其他通道的网络完成。
3. **R2（依赖R1）**：独立 `/ws/mobile` 或明确client/device身份的订阅协议，HTTP保留发送/附件/历史与补收，WS负责实时事件。禁止复用单例 `/ws/desktop`，该路由会关闭旧桌面连接，桌面被replace还会停止重连；mobile profile也无ws.desktop。需定义新增鉴权与token兼容，不能通过发admin权限绕过。
4. **R3（可选）**：多设备独立游标，非本次单手机卡住的前置。

## 三面闭环

- 管理面已有 `/settings/clients` mobile effective_state仅反映活跃TTL，不证明UI收到；`/settings/relay`是配置而非投递健康。后续补无正文的队列数量/最老年龄/保留淘汰、ack持久化失败、relay发布/回源结果和逐通道耗时；新增运营transport策略必须热更新入口/默认值/effective state，新增持久inbox/trace同单提供观测。
- 桌面本轮只读，无运行改动；后续保持v0.1与desktop动作权限边界，手机订阅单独归属。手机能力页只读，策略和权限放设置，通知授权/动作确认保留。
- 链路：request_id/session冻结 → 生成与工具 → turn sink/持久mobile queue → signal或WS/HTTP补收 → 本机inbox → UI/通知。保留turn_id/msg_id关联、seq顺序、ack、TTL、角色隔离、旧代失效、fallback，未知结果不自动重跑。

## 验证记录

Python3.12.13，公开测试配置、隔离DataPaths，无真实服务/凭据：

- `tests/test_mobile_queue_ack.py`、`tests/test_relay_publisher.py`、`tests/test_turn_sink.py`：49项。
- 临时 `_audit_delivery_probe_test.py` 四项：ACK写盘False仍成功；双消费者共享前缀；wait=5在入队后500ms内唤醒；slow-channel推迟离线mobile mirror。
- 合计 **53 passed in 14.25s**。日志 `.tmp/mobile-delivery-audit-2026-10-24.log`；探针副本保存在手机 `build/review/mobile-delivery-audit/backend-probes.py`，临时测试已移除，不把证明当前缺陷的断言加入正式修复回归。
- 手机现有定向78 passed、真实CONNECT代理/前台实验1 passed；它们均非Android真机。`adb devices -l`为空；线上部署SHA/手机版本/V2Ray实际模式/真实延迟/Doze/OEM未验收。

本轮仅审计文档，无跨端运行行为影响，无新增配置、状态库或API。验收下一阶段需覆盖代理切换、打开连接/响应头/正文卡住、ack落盘失败、页积压、signal回源失败、consume后崩溃、桌面并存及后台恢复。