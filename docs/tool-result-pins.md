# 工具结果临时驻留（F7）

真实成功工具结果返回 result_id；角色需要多轮复用时调用 `pin_tool_result(result_id, rounds)`，轮数1..10。只存既有 safe_summary，单项最多2000字、最多3项、总计6000字；不接受任意文本，不缓存 raw_data，禁止缓存 trace_result=false 的敏感结果及驻留工具自身输出。

缓存复用 context_continuity SQLite：最近30个候选加仍驻留项，硬过期最多24小时且不超过结果 expires_at。每次投影重查工具执行、角色曝光和 self-management 权限；到期、取消或撤权后不再注入。`unpin_tool_result` 取消，`list_tool_result_pins` 查剩余轮数。

`10.9_pinned_tool_results` 带 _layer/drop_priority=85，保留工具、获取时刻、原有效性、过期和剩余轮数；明确是不可信历史参考，驻留不代表仍然新鲜。与 recent_tool_results 的同工具同摘要去重。每次 owner prompt 构造用 ContextVar 冻结当轮已投影项；成功落盘后按 turn_id 幂等扣减。新标记不扣创建轮，工具推理步、失败重试和 scheduler tick 不扣；重标记修订不被旧投影扣轮。预算裁剪仍可能移除该层。

驻留轮沿既有外部资料隔离原则：事件标为 `tool_pin`，不进入自动 profile/中期固化/饮食抽取，原聊天证据仍保留供管理员查看；普通现实检索默认排除该来源。避免临时工具快照变成长期用户事实。此保守隔离针对整轮，与现有 web echo 行为一致。

现有 `/observability/context-continuity` 增加 pinned_results 元数据，无新运营开关、客户端设置或推送协议。观测不返回摘要原文。

回归覆盖真实快照、轮数、创建轮、重复提交、重标记、隔离、上限、撤权和硬过期。真实模型选轮数尚未验收。接续既有3条屏幕结果措辞测试在未修改模块的 HEAD 基线也失败，未顺手调整其断言。
