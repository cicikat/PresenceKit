# 前端工单：按聊天消息展开模型返回思考

后端准备完成，前端未施工。本单依赖后端 243 第 2 项；桌面和手机可分别实现。

## 契约

`GET /chat/turns/{turn_id}/reasoning`，Bearer，要求 `memory.read`（标准 desktop/mobile
profile 已包含；只有 chat scope 的 owner-input token 无权读取）。不需要 admin token。
turn_id 使用 `/desktop/chat`、`/mobile/chat` 成功响应的 canonical `turn_id`，
不能用流式 msg_id、本地气泡 ID、时间或最近一条归档推测。沿用现有请求桥接层。

```json
{"turn_id":"example-turn","available":true,"entries":[
  {"seq":1,"call_id":"example-call","created_at":1789000000.0,
   "preset":"example-preset","model":"example-model","protocol":"chat_completions",
   "status":"completed","reasoning_chars":4,
   "parts":[{"source":"reasoning_content","text":"示例思考"}]}]}
```

entries 按调用顺序排列，多步工具循环/重试可能有多条；不要覆盖成最后一条。
status 为 completed/interrupted，completed 仅表示协议消费完成，不代表未触及 token 上限。
source 是来源标签，正文只作文本渲染，禁止插入 HTML。不对思考执行工具或回灌聊天。

## UI 与降级

- [ ] assistant 气泡保存 canonical turn_id，提供默认收起的“模型返回思考”。
- [ ] 点击后懒加载，显示模型、各次调用、思考文本、中断状态；请求失败可重试。
- [ ] `available:false,entries:[]` 显示“本回合没有可用的模型思考记录”，不能解释成模型没有思考。
- [ ] 旧后端 404 隐藏入口或提示版本不支持；401 提示连接失效，403 提示权限不足，503 提示稍后重试。
- [ ] 普通 HTTP 完成后可立即查询；只收到 WS 广播的另一端可能比关联落盘更早收到消息，空列表允许稍后重试。
- [ ] 切换会话/角色时取消或忽略迟到结果，按 turn_id 缓存，不能串到其他气泡。
- [ ] 不修改 thinking.enabled；它控制生成策略，与展示偏好、默认归档行为独立。

## 范围与验收

当前关联桌面/手机 Reality owner 对话，包括主生成和相关工具循环调用，排除发送后的
情绪检测等后台调用。历史归档无回合关联，不回填猜测；QQ、主动消息、Dream、Stage
暂未接关联，保留 roadmap。现有 admin-only `/observability/llm-reasoning` 列表/详情
继续用于管理员全局归档查看，不应在客户端拿 admin token 调用。

- [ ] 非流式、流式完成、多步调用、中断/重试、无思考、旧消息、权限不足、切换角色。
- [ ] 不改变 HTTP/WS 正文、mobile poll/ack、TTL、relay 和通知行为。
- [ ] 同步客户端设置审计/协议文档，真实打开页面与真机验证。

后端回归：39 项通过（归档协议、并发关联、后台隔离、权限、mobile/owner 原路径）。
