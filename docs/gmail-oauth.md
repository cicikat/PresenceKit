# Gmail 独立邮箱（303 / 304）

在管理后台的「邮件连接」页，选择角色后填入角色的 Google 邮箱，确认模型处理说明，再点击「连接 / 重新授权」。保持同一浏览器完成 Google 授权。回到管理面点击「刷新」，再用「测试连接」确认账号。测试只查询授权邮箱的身份。

## 部署

在 Google Cloud 启用 Gmail API，使用 Web 类型 OAuth 客户端。在其已获授权的重定向 URI 中填写本机部署的完整 HTTPS 地址 `https://<公开主机名>/settings/gmail/oauth/callback`。路径、协议、主机名和端口必须一致。

管理员可直接在「邮件连接 → Google OAuth 应用配置」填写 Client ID、Client Secret 和完整 Redirect URI。「验证格式」只检查本地格式，不写入文件，也不验证 Google 授权。「保存」原子写入私有配置并立即生效，成功后页面重新读取服务端；Secret 输入框清空，只显示是否已配置。已有 Secret 留空保留。页面显示当前有效来源，复制按钮复制服务端正在使用的回调地址。

优先级为私有文件中的完整 `oauth_client` 配置高于 `GOOGLE_OAUTH_CLIENT_ID`、`GOOGLE_OAUTH_CLIENT_SECRET`、`GOOGLE_OAUTH_REDIRECT_URI` 环境变量；三项按整组选择，不混用来源。首次网页保存会将有效配置写入私有文件，后续进程重启无需环境变量。旧的 systemd `EnvironmentFile` 安装仍兼容；只有没有私有 `oauth_client` 时才使用环境变量。配置不写入公开 YAML。回调地址必须是公开 HTTPS 地址，固定路径 `/settings/gmail/oauth/callback`，不含用户信息、查询参数、片段或非 443 端口；不会从 Host 或 X-Forwarded-Host 推导地址。

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

OAuth 客户端配置、刷新令牌和哈希投递台账复用服务 data 根目录的 `runtime/auth/gmail/mailbox.json`（以 `get_paths().auth_dir()` 为准）。Linux 目录权限为 0700，文件权限为 0600，原子替换失败保留旧文件。该文件是受权限保护的明文，不是加密存储，系统管理员仍可读取。Access Token 只驻留进程内存。受保护卷的状态快照会包含邮箱文件；若仍使用环境变量，另行保护 systemd 环境文件。管理面响应不返回 Secret 或 Refresh Token。

更换 Client ID、Secret 或回调地址会保留邮箱、刷新令牌、同步设置和投递账本，清除旧 Access Token 与未完成的授权流程，并标记「待验证」。保存后点击「测试连接」；仅刷新令牌和读取 profile，不发送邮件。测试成功才显示授权有效，不能把本地格式有效视为 Google 授权有效。「断开并撤销」清除本地邮箱令牌并请求 Google 撤销，保留 OAuth 应用配置与投递账本。若上游撤销未确认，请在 Google 账号的第三方连接页手动移除该应用，同时清理含旧凭据的备份。

## API 与错误状态

以下配置 API 沿用 `admin` scope，响应设置 `Cache-Control: no-store`；输入校验错误不会回显提交值。

| API | 契约 |
| --- | --- |
| `GET /settings/gmail/oauth/config` | 返回 `client_id`、`client_secret_set`、`redirect_uri`、`configured`、`source`、`credential_revision`，不含 Secret。 |
| `PUT /settings/gmail/oauth/config` | 接收 `client_id`、可选 `client_secret`、`redirect_uri`；Secret 空或省略保留旧值；返回保存后的脱敏配置。 |
| `POST /settings/gmail/oauth/config/validate` | 同样的输入，仅校验，返回 `valid: true`、`authorization_verified: false`。 |
| `GET /settings/gmail` | 保留旧字段，新增 `token_state`：`not_configured`、`waiting_authorization`、`pending_verification`、`valid`、`client_disabled` 或 `reauthorization_required`。 |

Google 错误仅显示固定分类码，不显示其原始响应正文。`disabled_client` 表示客户端被停用：在 Google Cloud 恢复客户端或更换网页配置，反复重新授权不能解决。`invalid_client` / `unauthorized_client` 先核对应用及密钥；`redirect_uri_mismatch` 核对页面复制值与 Google Cloud 登记值；`invalid_grant` 检查刷新授权是否失效并按需重连；`access_denied` 表示授权被拒绝。已识别的客户端/授权错误持久化，重启后仍可诊断；有效凭据版本和角色绑定隔离旧错误。

## 上线与回滚

部署前将现有私有邮箱文件及使用中的环境文件备份到受保护位置，禁止加入 Git、公开构建产物或工单。部署后读取原 Gmail 状态，在网页保存或迁移配置，核对来源和回调地址，再执行不发邮件的连接测试。真实 Google 授权和邮件收发须由账号管理员现场验收。

回滚前停止服务并备份当前私有文件；恢复上一代码版本以及需要的旧 `oauth_client` 配置。保留当前邮箱和投递账本，避免恢复整份过旧文件丢失后来写入的去重记录。若必须恢复完整备份，先核查 Gmail 已发送邮件，并恢复文件原所有者及 0600/0700 权限。环境变量回退需要删除私有记录的 `oauth_client` 字段，不能删除整个 mailbox 文件。

## 邮件处理

授权请求只包含 `gmail.readonly` 与 `gmail.send`。连接页显示实际授予的范围。角色在私聊 Tool Loop 或自主执行链中可列出、搜索、阅读、发送、回复和列出标签。若部署显式配置了 Tool Loop 类别，请在现有类别列表加上 `gmail`。模型预设和角色权限仍可收窄工具权限。缺少发送权限时重新授权后才可发信。

私聊首轮通过 `load_tools_gmail` 发现入口告知角色可用工具与用途；加载后返回完整工具定义和参数说明。
角色可先列出或搜索摘要，使用返回的 `message_id` 读取或回复；发送和回复使用同一 `request_id` 对同一投递去重。
授权成功或注册表存在工具不等于模型已收到工具，排障时还应核对最终发现入口、角色类别、模型预设和函数调用模式。

同步默认 300 秒，依赖现有调度器，可热更新为 60–3600 秒。连接或重新启用时建立当前时间基线，之后逐页检查收件箱。自主能力启用时只排入新邮件事实。收件箱历史可按需搜索。管理页显示有效同步状态、上次成功时间和退避错误。正文仅在角色读取时进入当轮工具上下文，现有模型路由可能把内容提交给外部模型服务处理。请使用适用于 Gmail 数据、符合 Limited Use 且不把这些数据用于通用训练的模型服务。角色可按授权主动保存必要摘要。管理面显示邮箱状态与脱敏投递记录。含邮箱结果的模型请求和推理不写入本地模型调试归档。

展开「邮件通道」，用「角色写信发信后端」切换 SMTP 或 Gmail API。Gmail API 使用角色邮箱作为发件地址，继续沿用写信和梦境明信片的目标地址、标题前缀及触发设置。旧安装默认 SMTP。Gmail 发送前保存持久投递标记。出现 `outcome_unknown` 时，在 Gmail 已发送邮件以 `rfc822msgid:` 加管理页显示的 Message-ID 核查。周信和明信片暂停对应条目的自动重试。确需补发时，先在 Gmail 中人工确认原投递状态。重复提交相同请求可能命中已发送记录。

## 授权持续性

Google 的 External / Testing 对本工单所用 scope 发出的刷新令牌通常七天到期。测试期间保留该账号在 Test users 并按需重连。长期运行时，在自己的 Cloud 项目核实个人使用豁免或完成适用的 Production 发布、scope 验证和安全评估。若 Google 要求验证授权域名，使用自己能验证所有权的 HTTPS 域名，并同步更新当前有效 OAuth 配置和 Google Cloud 中的重定向 URI。

- [Google OAuth 令牌到期条件](https://developers.google.com/identity/protocols/oauth2#expiration)
- [Gmail scope 分类](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [受限 scope 验证](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)
- [Google Workspace 用户数据与 Limited Use](https://developers.google.com/workspace/workspace-api-user-data-developer-policy)
