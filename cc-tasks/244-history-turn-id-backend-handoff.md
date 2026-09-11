# 244 后端交付：历史回复 canonical turn_id

状态：后端修复与回归完成；真实桌面重启联调 `observe`。
来源：桌面仓同名交接工单（2026-09-11）。

- 历史解析只读取 assistant 回复末尾、紧邻分隔线的 emotion/intensity 元数据。
  支持旧无 speaker 格式；显式 speaker 必须为 assistant，重复 ID 字段不采用。
- user 元数据、正文引用和正文中的 turn_id 不提供回复关联；无 ID 的旧日志缺字段。
  不回写或迁移历史，不按时间、内容或 WS ID 推断。
- 保留日期、owner、角色桶和 memory.read，已有 assistant_display_text 查询使用同一 ID。
- 已核对 capture_turn → post_process_critical → turn sink → owner HTTP 响应，以及
  associate_owner_turn 按成功响应 ID 绑定思考归档的链路；无须修改写入链。
- 33 个相关测试通过：历史解析/HTTP/角色隔离/只读、持久化后归档关联、主动历史、
  turn sink 和思考读取。隔离夹具验证不代表真实 provider 或原生桌面实测。
- 桌面已消费 ChatLogEntry.turn_id 并按回合展示思考入口；手机历史模型尚未消费 ID，
  列为 roadmap。本单不修改客户端或管理面静态资源，无新增设置、状态或权限。
- `observe`：真实对话后重启桌面，检查一个回合仅一个入口、归档读取、旧无 ID 无入口、
  无归档空态及重试。尚未执行真实桌面重启联调。
