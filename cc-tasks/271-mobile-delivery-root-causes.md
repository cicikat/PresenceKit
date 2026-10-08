# 271 手机投递与重试根因闭环（联动手机工单 27）

用户已授权后端联动和取消手机固定通知冷却。保留其他工作区未提交文件。

- [x] A chat-log 使用 ledger visible_text 的纯文本和分段，保持 display_text 同源；验收：记忆全文与显示分段不再混用，旧无事件 fallback 不变。
- [x] B 失败回执区分可安全重试和未知副作用；验收：同 ID/同 payload 在未工具执行/落盘前失败可重跑，执行过工具/进入 sink 后仍 unknown，completed/inflight/conflict 不变。
- [x] C 三面文档和现有 session-scope 观测同步；无新增持久库/开关；桌面兼容。
- [x] D 定向 pytest 与差异检查通过（73 passed，定向文件见下）；仅本单文件和接口总账新增段落 staged，立即提交。
- [ ] E 真机/真实模型/Doze 联调 not-run，设备缺失不伪报。
证据：tests/test_chat_log_turn_id.py::test_history_plain_and_styled_copy_share_visible_paragraphs；tests/test_session_scope.py::test_chat_failure_before_side_effects_retries_same_id / test_chat_failure_after_side_effect_boundary_never_reexecutes / test_context_failure_stops_sibling_probe_before_retry；73 passed、1 deselected（既有 test_desktop_chat_keeps_desktop_provenance 缺 trusted_user_text mock，与本轮变更无关）。core/session_scope.py:74/237、core/tool_activity.py:51、core/turn_sink.py:263、admin/routers/chat.py:250、admin/routers/chat_log.py:376。
