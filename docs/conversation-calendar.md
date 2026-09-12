# 对话日历统计

`GET /chat-log/stats/calendar`，要求 `memory.read` 与 `state.read`（admin 兼容）。
所有者从后端 scheduler.owner_id 读取；char_id 可选，省略使用活跃角色。

- `period=day|week|month|year`，`date=YYYY-MM-DD` 指定所在周期，默认当月；周从周一开始。
- 或传 `start`、`end`，含首尾，最多 366 天；非法日期、倒置或超长区间返回 422。
- `days` 每自然日一项：chat_rounds、tool_calls、image_views、input_tokens、output_tokens、
  total_tokens、model_calls、usage_missing_calls、coverage、chat_rounds_source。
- 日期使用服务器本地时区，不按客户端时区重新分桶；`tracking_since` 是首次启用时间。
- coverage 为 complete / partial / unavailable / future。null 表示未知，不得画成零活动。
  totals 仅求已知值之和，totals_partial 提示缺失或未完成的覆盖。

轮数：现实 capture_turn 成功写入的一问一答，按 owner + character + turn_id 去重。
不计主动发言、工具旁白、Dream；历史读取保留的 reality 事件账本及聊天日志，明确标为
partial，不推测已删除历史。工具次数：此 owner/character 通过闸门后的 dispatcher 执行尝试，
含失败，不计权限拒绝、待确认或已读缓存跳过；不代表远端动作成功。

Token 按调用开始时冻结的 owner + character 归属，切换角色不串桶。覆盖公共 LLM 协议出口
（Chat Completions / Responses / Anthropic）、独立上传视觉
与 OCR 出口的用量，不仅是聊天生成，可能含后台和 Dream 调用。独立手机自动化等绕开这些出口
的 transport 尚未覆盖；因此不称账单总额。图片次数是这些出口提交的图片张次，重读重复计，
上传缓存命中不计，不代表人类打开图片。全部指标按角色查询；无法确定 owner/character 的调用不计入任何角色。
Chat Completions 流式请求 include_usage；只记录服务商实际返回的 usage，缺失计入
usage_missing_calls，不估算 token。管理面合成图片诊断不计入活动。
Anthropic cache read/create 计入输入，OpenAI cached_tokens 不重复加算。

持久化：sandbox DataPaths.conversation_stats_db，SQLite 元数据计数，无正文、参数、图片、
密钥或地址，长期保留；调用 ID/turn ID 幂等，写入失败 fail-open，仅记录错误类型。
查询在线程执行，不创建数据库，不调用模型；数据库损坏返回 503，不伪装空数据。

三面闭环：新增只读接口即该存储的观测入口；不增加设置开关。桌面/手机既有 profile
具备读取 scopes，历史 /chat-log/dates 与 /chat-log/{date} 保持原协议。
本任务仅提供前端热力图接口，原生热力图及点击详情 UI 为 roadmap；无需修改 WS、IPC、
通知、queue、ack 或 TTL。真实服务商 usage 覆盖与客户端接入列 observe。
