# Gmail 独立邮箱（303）

在管理后台的「邮件连接」页，选择角色后填入角色的 Google 邮箱，确认模型处理说明，再点击「连接 / 重新授权」。保持同一浏览器完成 Google 授权。回到管理面点击「刷新」，再用「测试连接」确认账号。测试只查询授权邮箱的身份。

## 部署

在 Google Cloud 启用 Gmail API，使用 Web 类型 OAuth 客户端。在其已获授权的重定向 URI 中填写本机部署的完整 HTTPS 地址 `https://<公开主机名>/settings/gmail/oauth/callback`。路径、协议、主机名和端口必须一致。

Linux systemd 安装可用 `sudo install -m 600 -o root -g root /dev/null /etc/presencekit/gmail-oauth.env` 首次创建凭据文件。先创建目录 `/etc/presencekit`，已有文件直接用 `sudoedit` 编辑。变量为 `GOOGLE_OAUTH_CLIENT_ID`、`GOOGLE_OAUTH_CLIENT_SECRET`、`GOOGLE_OAUTH_REDIRECT_URI`。在 PresenceKit 的 systemd drop-in 的 `[Service]` 下加 `EnvironmentFile=-/etc/presencekit/gmail-oauth.env`，再执行 `sudo systemctl daemon-reload` 和 `sudo systemctl restart presencekit`。在后台填写预期邮箱地址，Google 授权成功后由程序获取 refresh token。凭据只通过服务器配置文件填写。

在现有 HTTPS server block 中加入下面两个精确路由。它们只接受浏览器一次性交接和回调，管理面和其它设置 API 继续走私有入口。保持无查询日志、无代理重试，确保其它入口的日志同样隐藏 OAuth code、state、ticket。若有外层 CDN，也要关闭这两个 URL 的查询参数日志和缓存。

```nginx
location = /settings/gmail/oauth/authorize {
    limit_except GET { deny all; }
    access_log off;
    error_log /dev/null crit;
    proxy_set_header Host $host;
    proxy_next_upstream off;
    proxy_pass http://127.0.0.1:8080;
}
location = /settings/gmail/oauth/callback {
    limit_except GET { deny all; }
    access_log off;
    error_log /dev/null crit;
    proxy_set_header Host $host;
    proxy_next_upstream off;
    proxy_pass http://127.0.0.1:8080;
}
```

OAuth 令牌和哈希投递台账存放在服务 data 根目录的 `runtime/auth/gmail/mailbox.json`（以 `get_paths().auth_dir()` 为准）。Linux 目录权限为 0700，文件权限为 0600。系统管理员仍可读取文件。Access Token 只驻留进程内存。受保护卷的状态快照会包含数据目录下的邮箱文件。请在加密或受保护的存储上另外备份 systemd 环境文件。恢复前停止服务并恢复原所有者和权限。恢复旧备份可能失去后来写入的发送去重记录，恢复前应核查 Gmail 已发送邮件。管理面「断开并撤销」先清除本地令牌，再请求 Google 撤销。若上游撤销未确认，请在 Google 账号的第三方连接页手动移除该应用，同时清理含有旧凭据的备份。轮换客户端密钥时更新环境文件并重启，出现重新授权提示时从后台再次连接。

## 邮件处理

授权请求只包含 `gmail.readonly` 与 `gmail.send`。连接页显示实际授予的范围。角色在私聊 Tool Loop 或自主执行链中可列出、搜索、阅读、发送、回复和列出标签。若部署显式配置了 Tool Loop 类别，请在现有类别列表加上 `gmail`。模型预设和角色权限仍可收窄工具权限。缺少发送权限时重新授权后才可发信。

同步默认 300 秒，依赖现有调度器，可热更新为 60–3600 秒。连接或重新启用时建立当前时间基线，之后逐页检查收件箱。自主能力启用时只排入新邮件事实。收件箱历史可按需搜索。管理页显示有效同步状态、上次成功时间和退避错误。正文仅在角色读取时进入当轮工具上下文，现有模型路由可能把内容提交给外部模型服务处理。请使用适用于 Gmail 数据、符合 Limited Use 且不把这些数据用于通用训练的模型服务。角色可按授权主动保存必要摘要。管理面显示邮箱状态与脱敏投递记录。含邮箱结果的模型请求和推理不写入本地模型调试归档。

展开「邮件通道」，用「角色写信发信后端」切换 SMTP 或 Gmail API。Gmail API 使用角色邮箱作为发件地址，继续沿用写信和梦境明信片的目标地址、标题前缀及触发设置。旧安装默认 SMTP。Gmail 发送前保存持久投递标记。出现 `outcome_unknown` 时，在 Gmail 已发送邮件以 `rfc822msgid:` 加管理页显示的 Message-ID 核查。周信和明信片暂停对应条目的自动重试。确需补发时，先在 Gmail 中人工确认原投递状态。重复提交相同请求可能命中已发送记录。

## 授权持续性

Google 的 External / Testing 对本工单所用 scope 发出的刷新令牌通常七天到期。测试期间保留该账号在 Test users 并按需重连。长期运行时，在自己的 Cloud 项目核实个人使用豁免或完成适用的 Production 发布、scope 验证和安全评估。若 Google 要求验证授权域名，使用自己能验证所有权的 HTTPS 域名，并同步更新 systemd 环境和 Google Cloud 中的重定向 URI。

- [Google OAuth 令牌到期条件](https://developers.google.com/identity/protocols/oauth2#expiration)
- [Gmail scope 分类](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [受限 scope 验证](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)
- [Google Workspace 用户数据与 Limited Use](https://developers.google.com/workspace/workspace-api-user-data-developer-policy)
