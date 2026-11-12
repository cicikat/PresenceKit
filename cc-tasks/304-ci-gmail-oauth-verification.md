# 304 CI 回归与 Gmail OAuth 验收

- 原始失败见 [304 基线 Actions](https://github.com/cicikat/PresenceKit/actions/runs/43155270308)。早期修复覆盖 Python 3.10 超时、摄像头参数、管理面和工具契约、队列及 TTS 预览。本次复现并修复了剩余失败。
- 图片测试遗留回合附件，后续通道替身因参数不匹配拒收消息。共享 fixture 复位回合上下文和通道注册，桌面投递测试断言通过真实通道发出的 WebSocket payload。安全开关和启用前置条件均在测试中生效。
- 手机 WebSocket 测试等待真实 ASGI 处理器完成断开，验证旧会话被拒绝和连接表清理。音频统计使用确定的单调时钟。全局记忆路由通过注册合成条目实际验证作用域拒绝，避免空参数集生成 skip。
- [PR #2](https://github.com/cicikat/PresenceKit/pull/2) 包含完整修改清单和针对提交的双版本 Smoke、全量、串行、浏览器及离线评估。配套规则和报告见 [测试与合并约束](../docs/testing-and-merge.md)及 [306 验收](305-test-quality-ci-trust.md)。

## OAuth 管理接口

管理员可在管理面「邮件连接」配置客户端。三个接口复用管理员鉴权。

| 接口 | 契约 |
|---|---|
| `GET /settings/gmail/oauth/config` | 返回 `client_id`、`client_secret_set`、`redirect_uri`、`configured`、`source`、`credential_revision`。 |
| `PUT /settings/gmail/oauth/config` | 保存 Client ID、Secret 和回调 URI。Secret 留空沿用当前值。轮换保留邮箱和 Refresh Token，并使旧 Access Token 与短期授权会话失效。 |
| `POST /settings/gmail/oauth/config/validate` | 本地检查客户端配置与公开 HTTPS 回调格式。授权结果由实际 OAuth 流程确认。 |

有效配置优先读取私有 `runtime/auth/gmail/mailbox.json` 中的 `oauth_client`。没有该记录时回退到 `GOOGLE_OAUTH_CLIENT_ID`、`GOOGLE_OAUTH_CLIENT_SECRET` 和 `GOOGLE_OAUTH_REDIRECT_URI`。文件以原子写入更新。Linux 上目录权限为 `0700`，文件权限为 `0600`，保护机制为文件访问控制。

## 人工上线与回滚

1. 在部署机私下备份 Gmail 私有文件。部署后先读现有邮箱状态。
2. 如需配置或轮换，在 Google Cloud 和管理面填写同一完整公开 HTTPS 回调地址，固定路径为 `/settings/gmail/oauth/callback`。保存后用「测试连接」确认。客户端被禁用时先恢复或替换 Google Cloud 客户端。
3. 如需回滚，恢复上一版本和备份中的 OAuth 客户端配置，保留邮箱记录和投递台账。代码已进主线时通过 revert 回滚对应 PR。

本次浏览器和 API 验收使用合成凭据和隔离目录。真实 Google 授权、真实邮箱连接及 VPS 上线尚未执行。
