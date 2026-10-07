# 267 Mobile 聊天恢复与工具历史关联（联动 mobile 工单 26）

用户授权跨仓施工；不发布、不改生产数据，不修改桌面代码。
- [x] 确认 get_day 工具追加后仅 HH:mm 稳定排序，且遗漏 request_id 投影。
- [x] 工具执行在 owner 请求 ContextVar 内收集 event_id，完成后绑定已有 action_trace 的 turn_id/request_id；复用现有观测，不新增台账。
- [x] chat-log 使用 ledger occurred_at 排序，恢复已知 request_id；不猜 legacy 关联，兼容字段只增不删。
- [x] 隔离回归：同分钟排序、不同回合隔离、请求关联、工具 status 不变、既有历史媒体与鉴权。
- [x] 同步三仓总账与 known-issues，定向测试通过后 scoped commit。
- [ ] 真机/生产联合验收 not-run。

证据：Python 3.12 项目 venv，chat-log/tool activity/session scope/EventContext 5 个定向测试文件共 33 passed；基于已有 ingress 校验按 token+owner+char+domain 复用 request receipt，新增 same-scope rebind replay、different-char isolation 和 payload conflict 回归。已有 tool-traces 查询直接返回 display_activity 关联字段。无生产写入。

最终复验：33 passed；diff --check 通过，普通 diff stat 与 ignore-cr-at-eol stat 一致。真机 adb 列表为空，not-run。
