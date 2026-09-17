# Owner Turn API v1

这是一份给硬件、脚本和本地适配器使用的调用合同。HTTP 的精确 schema 仍以运行中的
`/openapi.json` 为准；本页解释状态、幂等、副作用和凭证边界。

## 1. 接入和鉴权

服务地址使用部署方提供的 base URL，例如 `https://<presence-host>`。loopback、私网和
公网 TLS/WSS 的边界由部署配置决定；本接口不替调用方探测公网隧道或证书健康度。

请求必须带：

```text
Authorization: Bearer <owner-input-token>
```

在管理面 Token 页面或 `POST /auth/tokens` 创建独立调用凭证：

```json
{"label":"<caller-label>","profile":"owner-input"}
```

`label` 是服务端 caller identity。每个物理设备、脚本或适配器使用独立 label，便于单独
停用、轮换和审计。明文 token 只在创建/rotate 响应出现一次；立即放入 OS secret store、
设备安全区、部署平台 secret 或未跟踪的 `.env.local`。不要写入 Git、角色卡、Prompt、
URL query、日志或截图。desktop、mobile、device、panel、legacy admin 和普通 sensor
token 都不是本接口的集成凭证；sensor/device 事件也不能冒充用户消息。

调用方可用同一 token 调 `GET /auth/whoami` 做不泄密自检。只记录返回的 label/profile/scope
摘要，不打印 token 本身。

## 2. 请求合同

`POST /v1/owner/turns` 的 body 只允许以下字段：

| 字段 | 类型和限制 | 语义 |
|---|---|---|
| `client_turn_id` | 必填 string，1–128 字符，匹配 `[A-Za-z0-9._:-]+` | 调用方生成并持久化的 opaque 幂等键 |
| `message` | 必填 string，去首尾空白后非空，最多 12000 字符 | 一条真实 owner 表达 |
| `reply_to` | optional object 或 `null`；`text` 必填 string、最多 2000 字符；`ts` 必填 numeric | 被引用的上下文 |
| `upload_ids` | optional list，最多 8 个 opaque string | 当前预留；非空固定返回 `upload_id_not_available` |

body 禁止出现 `uid`、`char_id`、`source`、`origin`、`trust`、tool capability、token、
配置覆盖和任何本机路径。owner、active character、channel provenance、工具暴露面和写入
策略由服务端固定解析，调用方不能覆盖。固定会话 scope（`session_id` / `request_id` /
`capabilities.session_scope`）是另一份拟议合同，见
[session-scope-contract.md](session-scope-contract.md)；未落地前本接口仍冻 live active，
且不得把 `client_turn_id` 改名为 `request_id`。

这是有状态且有副作用的真实 owner turn：它复用现有 Reality pipeline、conversation gate、
统一 `turn_sink` 和已授权工具面，可能写入对话/记忆状态。HTTP body 不是第二份聊天历史，
调用方应以 canonical `turn_id` 去重并把最终回复视为统一 fanout 的同一轮结果。

成功响应（200）示例：

```json
{
  "reply": "<assistant-reply>",
  "emotion": "neutral",
  "turn_id": "<canonical-turn-id>",
  "msg_id": "<canonical-turn-id>",
  "critical_written": true
}
```

当前轮仍由本进程执行时，重复 POST 或 GET 会得到脱敏状态投影；POST 的 202 示例：

```json
{
  "status": "in_flight",
  "client_turn_id": "<client-turn-id>",
  "canonical_turn_id": null,
  "created_at": 0,
  "updated_at": 0,
  "error_code": null
}
```

`GET /v1/owner/turns/{client_turn_id}` 只允许当前 token caller 读取自己的 receipt，返回
`status`、`client_turn_id`、`canonical_turn_id`、`created_at`、`updated_at`、`error_code`。
receipt 不保存或返回请求正文、assistant reply、Prompt、工具参数/结果、request hash、token
或路径。

完成后的重复 POST 会重放同一 canonical `turn_id` 和 reply，不会产生第二次 LLM、工具或记忆
副作用。canonical reply 超过现有 retention 后返回：

```json
{"status":"completed_result_expired","client_turn_id":"<client-turn-id>","error_code":null}
```

进程在无法证明副作用是否已经发生的窗口中断时，receipt 会变成 terminal
`interrupted_unknown`，固定 `error_code` 为 `execution_outcome_unknown`。它不会自动重跑；
调用方停止自动重试并交给人工或上层状态机处置。

Owner-input 默认工具面只允许既定 `info`/`memory` 类别。最终是否调用工具仍由模型 preset、
角色卡和 tool-loop 合同决定；“能够调用 API”不等于“所有工具都可用”。

## 3. HTTP 状态和固定错误

| 状态 | 含义 |
|---:|---|
| 200 | 本次执行完成，或同 payload 的 retained completed replay |
| 202 | 同 caller + ID 的当前进程任务仍在执行；不要生成新 ID |
| 401 | Bearer token 缺失、无效、过期或已停用 |
| 403 | token 没有 `chat`，或不是 `owner-input` profile |
| 404 | caller-owned status 中找不到该 ID |
| 409 | 同 ID 的正文/引用/上传引用冲突，或 `upload_id_not_available` |
| 410 | `completed_result_expired`：副作用已发生，回复正文已过 retention |
| 422 | body 类型、字段名、ID、message、reply_to 或 upload_ids 校验失败 |
| 429 | 鉴权失败限速器暂时阻断请求 |
| 502 | 上游模型返回格式不符合当前 owner-turn 合同 |
| 503 | 服务未就绪、执行失败，或 `execution_outcome_unknown` 中断状态 |

错误 body 的 `detail` 只含固定错误码或通用提示，不含 token、正文、Prompt 或本机路径。

## 4. 幂等、超时和重试

1. 收到新的真实 owner 表达时生成一个稳定 opaque ID（推荐 UUID）。
2. 把 ID 和规范化请求写入调用方 durable outbox，直到得到 terminal 状态。
3. 调用 POST；客户端 connect/read timeout 或网络中断不代表后端失败。
4. 收到 202、超时或断线后，使用**同一 token、同一 ID、同一 payload**重试 POST，或 GET 查询。
5. 同 ID 不得修改 message、reply_to 或 upload_ids；冲突固定 409，也不得自动换 ID 绕过冲突。
6. 只有新的用户意图才生成新 ID。代理、队列和固件不能因为 timeout 自动制造新回合。
7. `completed_result_expired` 表示副作用已经发生，不能重新执行。
8. `execution_outcome_unknown` 无法证明副作用是否发生，必须停止自动重试并人工处置。

## 5. 最小调用示例

以下示例从环境变量读取凭证，示例 token、用户、角色和路径均为占位符。

### curl

```bash
curl --fail-with-body --connect-timeout 5 --max-time 45 \
  -H "Authorization: Bearer ${PRESENCE_OWNER_TOKEN}" \
  -H "Content-Type: application/json" \
  "${PRESENCE_BASE_URL}/v1/owner/turns" \
  --data '{"client_turn_id":"<uuid>","message":"<owner-message>","reply_to":null,"upload_ids":[]}'
```

### PowerShell

```powershell
$headers = @{ Authorization = "Bearer $env:PRESENCE_OWNER_TOKEN" }
$body = @{ client_turn_id = '<uuid>'; message = '<owner-message>'; reply_to = $null; upload_ids = @() } |
  ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri "$env:PRESENCE_BASE_URL/v1/owner/turns" `
  -Headers $headers -ContentType 'application/json' -Body $body -TimeoutSec 45
```

### Python

```python
import os
import uuid
import httpx

payload = {
    "client_turn_id": str(uuid.uuid4()),
    "message": "<owner-message>",
    "reply_to": None,
    "upload_ids": [],
}
response = httpx.post(
    f"{os.environ['PRESENCE_BASE_URL']}/v1/owner/turns",
    headers={"Authorization": f"Bearer {os.environ['PRESENCE_OWNER_TOKEN']}"},
    json=payload,
    timeout=httpx.Timeout(connect=5.0, read=45.0, write=5.0, pool=5.0),
)
response.raise_for_status()
print(response.json())
```

### TypeScript

```typescript
const clientTurnId = crypto.randomUUID();
const response = await fetch(`${process.env.PRESENCE_BASE_URL}/v1/owner/turns`, {
  method: "POST",
  headers: {
    Authorization: `Bearer ${process.env.PRESENCE_OWNER_TOKEN}`,
    "Content-Type": "application/json",
  },
  signal: AbortSignal.timeout(45_000),
  body: JSON.stringify({
    client_turn_id: clientTurnId,
    message: "<owner-message>",
    reply_to: null,
    upload_ids: [],
  }),
});
```

## 6. 版本兼容和接入清单

`/v1` 允许新增 optional request/response 字段和新增固定错误码；调用方必须忽略未知 response
字段。删除/改名字段、改变类型、幂等键或副作用语义属于 breaking change，须新增版本路径或
迁移期。OpenAPI、本文档和 `tests/protocol_fixtures/v1/` 必须互相校验；发现冲突时以运行代码
为发布真值并登记 drift。

接入顺序：

1. 管理面创建唯一 label，profile 选 `owner-input`。
2. 立即复制一次性明文 `emt_<redacted>` 到目标项目的 secret store；关闭页面后只能 rotate。
3. 配置 `PRESENCE_BASE_URL` 与 `PRESENCE_OWNER_TOKEN`（只是推荐环境变量名）。
4. 用 `/auth/whoami` 确认 label/profile/scope，绝不打印完整 token。
5. 先实现 durable outbox、202/timeout 查询和固定错误码处理，再接真实硬件输入。
6. 麦克风/按钮输入做本地去抖与真实表达判定；普通传感器事实继续走 sensor/device 通道。
7. token 泄露或设备丢失时立即 disable/rotate；不同调用方不要共用 token。

不要把 token 放进 tracked `config.yaml`、代码常量、角色卡或 URL。owner-input 媒体上传尚未
实现，`upload_ids` 非空必须 fail loud。
