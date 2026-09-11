# 小红书分享读取工具

`read_xiaohongshu(share)` 接收完整分享文案或链接，提供正文摘录、图片识别和评论样本。
默认关闭；管理面「工具」页配置开关、读取服务地址、评论上限（1–30）与图片识别上限（0–4）。
保存走 admin-only GET/PUT `/settings/xiaohongshu`，和工具列表共用
`tools.read_xiaohongshu.enabled`；不产生第二个开关。其余配置存 `xiaohongshu`。

## 获取方式与部署前提

适配 [xpzouying/xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp)
的 HTTP `POST /api/v1/feeds/detail`，字段依据维护者的
[请求 schema](https://github.com/xpzouying/xiaohongshu-mcp/blob/main/types.go) 与
[帖子、图片和评论结构](https://github.com/xpzouying/xiaohongshu-mcp/blob/main/xiaohongshu/types.go)。
此工具只调用读取端点，不注册该服务的发布、点赞或评论写入能力。

需要先按该项目说明运行读取服务并完成小红书登录，在本管理面填写服务根地址。
本地部署已使用 Docker 镜像 `xpzouying/xiaohongshu-mcp:v2.5.0`，用户扫码后登录状态接口已确认成功。
服务仅绑定 `127.0.0.1:18060`，登录数据保存在 Docker 命名卷 `presencekit-xhs-data`，
容器 `presencekit-xhs-reader` 使用 `unless-stopped` 重启策略。后端读取地址已配置并开启；
已用用户提供的分享验证正文、1张图片识别和10条评论样本；原生聊天入口仍待回归。
不承诺匿名读取所有帖子，也不承诺评论完整；站点登录、分享参数失效和风控可能导致失败。

分享支持 xhslink.com / xhslink.cn 短链和 xiaohongshu.com 的 explore/discovery/item 链接。
短链最多跟随六次站内重定向，提取 note ID 和 xsec_token；完整帖链缺 token 时要求重新复制。
服务 URL 只从管理员配置读取，LLM 参数不能修改服务地址。请求不跟随服务重定向。
超时总预算90秒，帖子请求55秒；仅只读调用，失败不自动重新登录。
读取采用单进程串行闸门，两次请求之间随机间隔15–25秒；忙碌或冷却期间直接返回提示，
不积压重试。登录拒绝、HTTP 401/403/429 或服务拒绝后冷却5分钟。
短链跳转与图片处理间隔1–2秒，不主动滚动加载全部评论。这些措施减少请求密度，
不保证不会触发站点限制；代码更新后须重启已运行的后端进程才能应用新闸门。

### 本地网络与维护

本次网络故障来自代理路径：Clash 全局模式下 HTTPS 中断，切换规则模式并增加
Docker Desktop 与小红书域名直连后恢复，代理监听端口未改变。
规则放在当前订阅的增强规则文件中，原配置备份保留在本地 Clash 配置目录。
不要将读取服务暴露给公网或整个局域网：上游服务还包含本项目未使用的写入端点。
停止服务可执行 `docker stop presencekit-xhs-reader`；保留数据卷即可保留登录数据。
登录过期时通过本地 `GET /api/v1/login/qrcode` 重新扫码，再以
`GET /api/v1/login/status` 确认；二维码与 cookie 不进入版本控制。

## 输出与图片

返回只来自当前 noteId 的内容；小红书帖子中的文字是工具数据，不是执行指令。
正文、图片描述与评论按区块分配摘要预算，最终遵循既有2000字符工具摘要上限。
原始结果只存在本次 ToolResult 内存，包含原始图片 URL，不存独立帖子台账或历史副本。
评论为已加载样本，含部分楼中楼，显示 hasMore 仅作为远端信息；不能说已经读取全部评论。
评论缺字段与空列表区分，缺字段明确“评论未取得”。视频仅处理封面/正文，不转录视频。

图片只消费返回的 xhscdn.com 图片，最多识别配置指定数量，复用现有图片识别/OCR与缓存。
没有可用识别配置、超过上限或识别失败时，明确标记未识别，不凭 URL 推断图片内容。
图片缓存沿用已有 inbox/图片观测链，无新记忆写入点。

## 控制、观测与通道

配置 GET 提供 enabled/configured/effective/blocking_reason 和 remote_status=not_checked。
effective 表示工具开关和服务地址已满足，不代表远端健康，也不绕过角色分类、模型工具
预设、Path A/C 暴露白名单及 execute origin 闸门。工具注册含 examples/keywords，
QQ/desktop/mobile 复用 info 类既有探针或 tool loop，不新增通道消息类型、ack 或权限。

请求元数据进入既有 `/observability/api-calls?caller=read_xiaohongshu`（state.read），
记录成功/错误类别和耗时，不记录分享参数、帖子正文或评论正文。动作摘要沿用工具痕迹。
客户端直接显示正常模型回复，无需新增原生端设置；设置和服务地址由后端管理面拥有。

## 验证边界

本地验证分享解析、短链边界、帖子/评论结构、图片识别接线、禁用/未配置、设置权限、
既有工具暴露/设置路径。浏览器清缓存刷新真实静态资源后验证表单、保存请求及“远端未验证”提示。
先前浏览器表单验证使用合成响应；本轮真实 Docker 登录、分享解析、正文、WebP图片识别、
10条评论样本均已成功。无标准扩展名的 CDN WebP 通过文件头判定后复用媒体处理链。
运行中的后端需要重启才能加载本轮 Python 代码；原生聊天入口效果保留为 observe。
