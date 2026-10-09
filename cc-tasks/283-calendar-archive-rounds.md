# 283 使用情况历史聊天轮数恢复

- [x] 只读溯源：统计计数始于 2026-09-12；canonical 日日志自 2026-04-15 起保留，旧日文件为 `.md.gz`，calendar 回退仅读 `.md`（admin/routers/chat_log.py）。初审 178 个日文件、17993 轮，非完整历史承诺。
- [x] 修复：admin/routers/chat_log.py calendar_stats 读取 canonical `.md.gz`，双格式复用 _merge_day_texts；不修改源文件、不猜测 token、不改变 coverage。
- [x] 验收：tests/test_conversation_stats.py、tests/test_chat_log_turn_id.py 共 20 passed（Python 3.12）；覆盖压缩归档、同日去重、损坏归档 503、角色隔离和未知用量。真实数据直接调用 calendar_stats：2026-04-15 至 10-10 共 18004 轮；月汇总 2667 / 5113 / 2146 / 1568 / 1592 / 2912 / 2006，首日 10 轮，totals_partial=true。数字是检查时快照，当前日可继续增长。
- [x] 文档：docs/conversation-calendar.md 说明压缩归档读取、重叠取 max、历史下限和源文件保留；三仓总账补充读取格式。
- [x] 提交检查：git diff --check 通过，普通与 ignore-cr-at-eol diff stat 一致；仅提交本工单的代码、测试和文档。提交号见此文件 Git 历史。

部署验收：partial。真实数据函数级查询完成；运行中的 HTTP 服务与手机 UI 尚未 reload/实测，不把本地代码结果写成线上已生效。后端重启加载此提交后，现有 calendar API 自动显示，无需改写生产数据库。

三面闭环：复用现有 memory.read + state.read 观测端点；无新设置、权限、队列或持久状态。桌面/手机消费原 schema，不改客户端。历史回退仍仅 canonical 桶；未归属旧目录不自动认领。原发送、通知、ack、TTL 链无变化。

删除候选：无；本次不清理任何历史数据。
