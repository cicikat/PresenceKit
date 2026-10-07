# 个人微信 reference transport（268）

实现状态：代码与本地 fixture 链路 current；真实已登录 WeChatPadPro 与微信收发 observe。
PresenceKit 不附带第三方仓库或可执行文件，不自动安装、扫码或创建账号。

## 边界与协议来源

抽象 `integrations/wechat_transport.py` 只定义 `TransportMessage`、`DeliveryResult` 与
`WechatTransport.receive/send_text/close`。`integrations/wechat/factory.py` 选择实现；其余
共享业务代码不依赖 WeChatPadPro 字段或 API。未来 bridge 版本差异应新增或修改实现，不改
Pipeline、memory 或 turn_sink 的协议解释逻辑。

首个 reference profile 固定为公开 849 API family：
[Swagger](https://github.com/naiveclub/wechatpadpro/blob/1920f14a9f25eef0e8eac696e1160c522ca6dba/static/swagger/swagger.json)，
commit `1920f14a9f25eef0e8eac696e1160c522ca6dba`。
公开主项目 [WeChatPadPro/WeChatPadPro](https://github.com/WeChatPadPro/WeChatPadPro)
存在不同版本与分发方式，因此不宣称与全部发行版兼容。

实现内使用 WS `/ws/GetSyncMsg` 与 REST `/message/SendTextMessage`，账号 key 作为第三方
接口 query 参数；正文为 MsgItem 列表。发送成功只在有单条 Ret=0 明确确认时标记 accepted；
HTTP 200 或泛化成功封套不足以证明投递，未知响应标为 unknown。accepted 仅表示 bridge
协议确认，不代表微信用户已读。

WS 支持 Data.AddMsgs 批次与单条 sync 记录，字符串字段可为 protobuf string 对象；无法识别
的状态/联系人消息忽略。公开 Swagger 未定义完整同步响应 schema，实际部署必须核对该版
收到的 sync 封套及逐消息发送确认。若不一致，只修改 reference decoder/confirmation mapper
并补 fixture；不可在 shared ingress 中兼容字段。不记录带 key 的 URL 或第三方异常正文。

## 部署连接

1. 在仓库外指定目录部署所选 bridge，按其文档完成账号登录与普通账号 key 获取。
2. 给启动 PresenceKit 的进程设置环境变量 `WECHAT_TRANSPORT_TOKEN`。不要把 key 放在 URL、
   tracked 文档、日志或截图中；凭据不通过管理面回读。环境变量变更需要在进程启动前设置。
3. 管理面「高级运行配置与实验 → 个人微信」设置 REST 根地址（可含版本前缀）、登录账号 ID、
   绑定 owner 的微信发送者 ID，然后启用通道。UID 使用现有 scheduler.owner_id，不能输入
   一个新的微信 UID 作为记忆身份。主动下行默认保持关闭。
4. 检查 effective_state、credential_configured、连接状态与发送结果计数。缺少绑定/连接凭据
   显示阻塞原因；启用后未连上显示 connecting，连接错误使用稳定脱敏码。配置变更关闭旧连接
   并拒绝旧 generation 的排队消息/回复；不会把失败回复改送 QQ。

REST 与 WS 都使用 `aiohttp trust_env=False`，不走系统代理；REST 不跟随重定向。
入站、出站均遵守 recovery no_outbound。首期没有补发队列、登录 UI、媒体或群聊。

## 验收边界

本地 aiohttp fixture 实际建立 WS 并发送 sync 消息，真实 HTTP 请求验证文本发送协议；另有
绑定拒绝、去重、旧 generation、unknown 不重发、no_outbound、协议字段隔离与管理面权限
测试。它们不证明已登录的 bridge 版本兼容、真实账号稳定性或真实微信到达。

上线验收需：绑定 owner 两轮私聊延续同 UID 记忆；QQ 同时收发不误投；重复事件只一次生成；
断连、热关闭与重新连接有准确观测；开启主动下行后只发送到绑定 owner。无真实连接材料时
保留 not-run，不能以模拟成功代替。
