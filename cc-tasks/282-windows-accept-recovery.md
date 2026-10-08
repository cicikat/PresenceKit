# 282 Windows HTTP 监听因 WinError 64 停止

根因证据：支持环境 Python 3.12 的 IocpProactor.accept 将 AcceptEx 的 WinError 64 向上抛出，BaseProactorEventLoop._start_serving 的 OSError 分支关闭监听 socket。用户报错后本机8080连接失败。该路径不经过文档或工具调用；上次入口200仅证明检测时可用。

- [x] admin 生命周期内安装 Windows Proactor accept 恢复适配；仅对 WinError64重试，关闭失败连接而保留 listener，退避防忙循环，取消与其他错误不吞。
- [x] 保留 Proactor 的子进程能力；不改系统 Python、不切 selector、不过滤错误假装恢复。
- [x] Windows 实际 TCP 故障注入后下一连接可用；覆盖取消、正常关闭、非64错误。
- [x] 文档、相关测试与换行检查后独立提交。

无新配置或持久状态；恢复警告走既有运行告警观测。只影响共享后端 HTTP监听，不改变 mobile/desktop协议或UI。真实手机端恢复仍需用户验收。删除候选无，本单不清历史brief或用户数据。

验证：Python3.12 Windows真实IOCP socket故障注入、子进程运行及关闭/错误回归通过；手机入口/队列及LF/角色名相关共37 passed、1旧失败。失败为 test_desktop_chat_keeps_desktop_provenance 仍期待仅reply_to，当前既有入口已带trusted_user_text；本单未改该入口，不扩大修复。运行中的旧进程8080不再监听；需重新启动后端才能加载适配，不强杀旧进程或发送虚构用户消息。
