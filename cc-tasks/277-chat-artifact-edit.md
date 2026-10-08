# 277 工件追加与局部修改

范围：扩展已注册的 Path C `update_artifact`，保留旧调用默认整份覆盖。

- [x] 核查注册、dispatcher 范围注入、群聊拒绝及现有读写合同。
- [x] 增加 append / edit 模式；在同一 scope 锁内读取、校验、修改。
- [x] 保留同一 ID、上一版、revision、updated payload、字符上限及 SHA 冲突检查。
- [x] 验证追加不丢未读尾部、唯一片段替换/删除、拒绝歧义及超限、并发不丢追加。
- [x] 运行相关回归与 portable schema 检查，核对差异和换行，独立提交。

验证：Python 3.12，`tests/test_chat_artifacts.py`、`tests/test_tool_schema_portable.py`、
`tests/test_line_endings.py` 共 36 passed；追加和局部修改经 `execute_structured` 验证。
仅本单四文件进入提交，保留其他并行修改。真实角色会话工具可见性未实测。

验收边界：后端工具 schema 与 dispatcher 执行、沙盒文件和下载回归。
复用既有 `/observability/chat-artifacts`，不新增配置或持久状态种类。
desktop/mobile 继续消费既有工件 ID 和下载链接，无协议变更；真实模型会话与客户端实测不在自动测试证据内。
删除 brief 候选：无。本次不涉及迁移或退役能力。
