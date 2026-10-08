# 消息分发与客户端设置

管理面「功能与行为」→「功能开关总览」下方的「消息分发与客户端设置」集中 QQ、微信、电脑与手机。客户端按需启用，详情使用折叠卡片。微信连接从高级运行配置迁入；手机后台唤醒从网络配置迁入。

`GET/PUT /settings/clients` 要求 admin scope。`client_channels.desktop/mobile` 缺省均为 true，热更新：关闭阻止对应 `/desktop/*`、`/mobile/*` 和桌面 WS 新连接，同时停止广播、桌面已有 WS 推送及手机 durable mirror。保留原队列；重新开启后未过期消息按原 ack/TTL 规则消费。已开始的回合不撤销；这不是撤销 token 或关闭所有共享工具接口，若需要撤销设备全部访问须停用它的 token。

QQ enabled 复用 feature-flags，host/port 由本页配置，均需要重启后端；standalone_mode 仍阻止 QQ 启动。微信 enabled、连接和 owner 绑定复用 `/settings/wechat`，桥密钥取自环境变量。页面显示配置及连接状态，不将等待连接写成投递验收成功。

## 首次部署与协议兼容

1. 复制 config.example.yaml 为 ignored 的 config.yaml，配置模型及 owner。
2. 按 setup/auth 指南生成本地管理凭据和客户端 token；只开启实际使用的通道。
3. QQ 配置 OneBot WS 桥实际 host/port；微信按 [桥部署说明](wechat-reference-transport.md) 登录并设置桥 URL、账号、owner 身份及环境密钥。
4. 桌面直接连接后端 HTTP/WS，手机直接连接 HTTP；在客户端填写可达的后端地址和对应 token。手机不能用回环地址访问电脑。
5. 后端监听 host/port 由 config.yaml 的 admin 块决定，修改后重启。手机后台唤醒中继可选，App 与后端填写相同地址/topic/凭据。

桥安装目录不属于后端连接合同；可放任意目录或其他服务器。同协议桥可配置连接地址，跨协议需要独立适配器，不能只选设备自动兼容。当前 QQ adapter 使用无路径的 `ws://host:port`，不提供通用 URL/TLS/桥认证字段；需要这些连接形式时应另扩展合同，不应声称任意 OneBot 部署均可连接。

本地名为 presencekit-bridges 的目录用于部署 QQ/微信服务及保存登录状态，不随后端仓库自动安装。Emerald-bridge 是 insideworld 游戏 director 和只读 MCP sidecar，不是 QQ/微信/桌面/手机分发桥；普通陪伴部署无需安装它。两者均不是后端代码固定安装地址。

本单只改后端管理与通道开关，不改变客户端协议字段或设置界面。真实 QQ/微信登录投递和真实设备验收独立于隔离浏览器检查。
