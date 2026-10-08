# 腾讯微信 transport（270）

后端使用 `integrations/wechat/openclaw_weixin.py` 连接本机独立桥接。
协议来自 [Tencent/openclaw-weixin](https://github.com/Tencent/openclaw-weixin)
官方 `src/api/api.ts`，初始固定提交
`24de5c9eb0dd5e595d7e2d090ed8a3f82870d42c`（2.4.9）。
[协议说明](https://github.com/Tencent/openclaw-weixin/blob/24de5c9eb0dd5e595d7e2d090ed8a3f82870d42c/docs/protocol_zh_CN.md)
为 wire 依据。已取消的 PadPro 实现和 fixture 随替换删除。

## 所有权与隔离

`integrations/wechat/openclaw_bridge/` 是独立 Node 宿主，构建复用官方 API
模块，只替换宿主配置/日志 hook；不运行 OpenClaw Gateway/Agent。
角色、记忆和回复仍归 PresenceKit shared ingress、canonical owner 锁、
pipeline 与 turn sink。桥接不调用模型，也不自行生成回复。

本机 `GET /events` 返回规范化事件，`POST /send` 返回
accepted/rejected/unknown，使用 Bearer 本机密钥。微信 bot/context token
仅归桥接，不回传后端。HTTP 200 不是投递证明：sendmessage 必须明确
ret=0 且没有非零 errcode；缺确认/异常为 unknown，不自动重发。

只接受扫码 owner 与 bot account 都匹配、五分钟内的已完成单项文本私聊。
过滤陌生人、媒体、群聊与自发回显；入站队列上限64。仅缓存该 owner 的
最近回复 context，24小时失效，无有效 context 时拒绝主动下行。
桥接队列、游标、context 都是瞬态，不保证跨重启 exactly-once 或补发。

## Docker 与登录

从固定提交检出官方源码，在仓库外安装 esbuild、qrcode。
运行 `build.mjs <官方源码目录> <构建输出目录>`；将 bridge.mjs、Dockerfile、
生成 bundle/package.json 和依赖放入构建目录。Node24 镜像运行服务，
宿主端口仅映射127.0.0.1，仓库外登录数据挂载到 `/state`。
本机密钥通过 BRIDGE_TOKEN 提供，不需要 MySQL/Redis 或第三方授权码。

打开桥接首页，填本机密钥，获取二维码，用微信扫码确认。
支持过期刷新、手机验证码和凭据原子持久化；重启加载凭据。
二维码 redirect 与确认 baseurl 仅允许 HTTPS `*.weixin.qq.com` 根地址。
受保护的 `GET /status` 提供登录/连接状态、绑定、队列与计数，
不返回 bot/context token 或正文。上游 -14 会停止轮询，显示 login_expired。

## 后端管理面

后端进程环境 `WECHAT_TRANSPORT_TOKEN` 是本机桥接密钥，不是微信 bot token。
环境变更需重启后端；管理面不回读密钥。
「高级运行配置与实验 → 个人微信」使用 transport=openclaw_weixin；
base_url 填本机桥接地址，account_id、owner_sender_id 来自扫码确认。
canonical UID 仍使用 scheduler.owner_id，通道/主动下行默认关闭。
开关与绑定热更新。no_outbound 在 transport receive/send 入口阻断。
停止后端通道不再消费事件或发送，独立桥接的上游轮询仍由 Docker 管理；
需一并停止轮询时停止桥接容器。

`GET /observability/wechat`（state.read）继续提供后端脱敏计数和连接状态。
扫码凭据的排障使用受本机 Bearer 保护的桥接 /status。
桌面/手机协议没有变化，不需要安装新的聊天客户端。

## 验收

fixture 覆盖 Bearer、owner/media/history 过滤、大整数消息ID、context 回复、
缺确认 unknown、二维码保存、恢复凭据、后端热关闭与 no_outbound。
真实扫码、容器重启、两轮微信私聊和 QQ 并行收发分项验收；
扫码成功或服务在线不能替代消息实际到达。