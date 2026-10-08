# 270 — 腾讯 openclaw-weixin Docker 通道

## 范围与验收

按用户要求停止 WeChatPadPro，使用腾讯 `Tencent/openclaw-weixin` 官方 API
模块完成扫码和消息收发，PresenceKit 继续拥有角色、记忆与回复生成。
独立桥接不运行 OpenClaw Agent，不需要 MySQL/Redis。

- [x] 移除旧微信桥容器、专用网络与三个专用镜像；旧文件删除受策略拦截，交付列明。
- [ ] 官方 API 模块按固定提交构建；只替换依赖宿主配置/日志的接口。
- [ ] Docker 桥接扫码、验证码、凭据持久化、有界入站和回复上下文。
- [ ] 后端新增独立 transport，复用 owner gate、turn sink、no_outbound 与热启停。
- [ ] 管理面明确 transport，提供原有开关、绑定与脱敏状态。
- [ ] 相关回归、真实扫码、状态与重启验证；真实收发缺口如实登记。
- [ ] 差异/换行检查后独立提交。

当前旧 PadPro 实现与 fixture 随本次替换删除，不保留已经取消功能的测试。
无桌面/手机协议变化，不扩大到其他仓库。登录数据、密钥、源码下载与
生成 bundle 存放仓库外；tracked 示例只用占位符。
