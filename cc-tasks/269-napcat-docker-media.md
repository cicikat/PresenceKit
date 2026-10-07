# 269 — NapCat Docker 本地媒体传输

## 范围与验收

Windows 后端继续运行，NapCat 独立部署于 Linux Docker。现有图片和 AMR
发送传宿主文件路径，容器不能读取。改用 OneBot 已支持的 base64 内容，
不新增配置、设置入口或桌面/手机协议。部署配置、凭据和账号数据留在仓库外。

- [x] 图片发送改为携带文件内容。
- [x] AMR 转换成功后发送内容；转换失败保留 WAV fallback。
- [x] 回归覆盖私聊/群聊图片、AMR 成功和转换失败。
- [x] 同步 QQ 通道说明，核对换行和差异，独立提交。

验证：Python 3.12，`tests/test_qq_container_media.py` 与
`tests/test_sticker_broadcast.py` 共 12 项通过；私聊/群聊帧含原始文件内容，
转换成功/失败均不向 NapCat 暴露宿主路径，临时音频清理通过。
桌面/手机继续消费原有贴纸 payload，无跨端接口变化，不扩改其他仓库。

容器启动、WebUI 可达与真实账号登录/收发分别验收；单元测试不证明 QQ 到达。
旧 NapCat 在替代服务验证后清理，配置备份和新持久化数据保留。
本单不执行其他 brief 或 legacy 清理。
