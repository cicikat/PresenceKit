# docs/model-presets.md — 多模型 Preset 系统

角色心声通用提示与四家原生思考接口边界核对见 [thinking-voice.md](thinking-voice.md)。
这次增加 prompt 文风引导，没有修改 provider 参数、路由或将思考前缀作为通用 API 能力。

## 概述

### API 返回思考的独立存档（2026-09-09）

模型协议出口默认保存实际返回的思考，不依赖 `thinking.enabled`，也不额外请求思考。
存储为 sandbox 隔离的 `runtime/observability/llm_reasoning.sqlite3`；没有自动过期或截断，
没有思考的调用不生成记录。此处只保存 API 已返回的文本，不获取未公开的内部过程。
Chat Completions 的 `reasoning_content`/字符串 `reasoning`、Anthropic 明文 `thinking`、
Responses 返回的 reasoning summary/content，以及正文 `<think>`/`<thinking>` 标签内容
均可保存；加密内容、签名、请求 prompt、API key 与整个 raw response 不保存。

非流式请求在归一化前采集，流式按 delta 采集，正常结束或中断时落盘；中断记录 status 为
`interrupted`，成功处理为 `completed`（不表示模型未触及 token 上限）。思考标签在可见正文
中仍被清理，包括拆分标签和未闭合标签，不进入记忆、工具 continuation 或消息广播。
每次 API 尝试有独立 `call_id`，多步工具循环与重试各存一条；尚不关联聊天 `turn_id`。
SQLite 写入使用工作线程与 250ms 锁等待，失败只记录错误类型，不让聊天失败。

读取为 admin-only，端点由 `admin/routers/observability.py` 提供：

- `GET /observability/llm-reasoning?limit=50&before=<seq>&model=<model>`：分页元数据，
  返回 `enabled: true`、`retention: indefinite`、`entries` 与 `next_before`，不含思考正文。
- `GET /observability/llm-reasoning/{call_id}`：读取该次调用的 `parts[{source,text}]`。
  缺失返回 404，数据库不可读返回 503；列表读取不会创建数据库。

当前没有管理面板查看页或客户端展开 UI，标准 desktop/mobile token 不具备此 admin 权限。
未来按聊天回合展示需先补关联键与适当的受限读取契约，不应给客户端增加 admin token。

### Uploaded image routing and OCR

The admin Model Routing page owns `GET/PUT /image-recognition` (admin scope).
`image_recognition.mode` selects `vision` (legacy default) or `ocr` for QQ images
and desktop/mobile image uploads. OCR is independent of `vision`,
`phone_control_vision`, and `use_computer_vision`; screen automation never inherits OCR.
The UI follows the model connection fields: provider, explicit protocol, model, address,
and write-only key. Provider selection does not lock or overwrite an edited address.

`glm_layout_parsing` posts `{model, file}` directly to the exact `endpoint_url`, then
reads `md_results`; `chat_completions` uses the independent OCR `base_url` plus
`/chat/completions` and parses message content. Model names never infer protocols.
Requests use the shared proxy, a 60-second timeout, no automatic retries or redirects,
and the existing API call ledger (`caller=image_ocr`). No provider body or image data
is written to the ledger. Multiple images retain their input order and individual results.
GIF input uses its first frame as PNG. PDF upload support is not introduced here.

The image cache keeps the original file hash and adds a recognition signature for mode,
protocol, provider, model, and address. Old unsigned entries are reprocessed on upload;
changing a key alone does not invalidate results. Explicit rereading bypasses cached text
and uses the current upload route. OCR-only mode extracts text, not general scene meaning.
Empty OCR text is marked explicitly; transport/schema failures are not cached.

`GET /image-recognition` provides configured/effective state and the resolved OCR request
address, without the key. Ready configuration is not evidence of a successful remote call.
`GET/PUT /vision-params` also suppress key echo and preserve the key on blank input.

RPG Dream uses the independent `rpg_kp` call category for neutral structured adjudication. It falls back to the active profile's chat preset when absent, with a bounded 30-second timeout and no SDK retry.

把"只能跑一个 DeepSeek"重构成"按任务分流的多模型 preset 系统"：
- 主对话可以走 Claude / DS / 本地；轻量调用（probe / summary / detect_emotion）可以指向便宜模型。
- 每个 preset 自带**生成参数默认适配**（provider 白名单过滤）和 **prompt 结构适配**（narrative / xml）。
- **完全向后兼容**：现有 `config.yaml` 的扁平 `llm:` 块一字不改也能跑。

---

## 关键文件

| 文件 | 职责 |
|---|---|
| `core/model_registry.py` | ModelClient 构建 + 缓存、路由解析、参数合并+白名单、向后兼容合成 |
| `core/llm_protocol.py` | Chat Completions / Responses / Anthropic Messages 请求转换、调用与统一结果归一化 |
| `core/prompt_style.py` | prompt_style 转换钩子（narrative / xml） |
| `core/llm_client.py` | 唯一 LLM 出口，调用 model_registry 路由，在 sanitize 前应用 prompt_style；可选记录高敏感调试快照 |
| `admin/routers/settings_llm.py` | HTTP 接口：`/model-presets`、`/model-presets/active-routing`、`/llm-params` |

---

## 配置 schema

新增顶层 `model_presets` 块。旧 `llm:` 与 `vision:` 块保留。

### 请求快照调试（默认关闭）

`llm_debug_requests` 不参与路由选择；它只在短时排查模型请求或工具 schema 时记录实际发送的
messages、tools 和生成参数。配置通过 MCP 管理页或 admin-only `GET/PUT /llm-debug-requests` 修改：

```yaml
llm_debug_requests:
  enabled: false
  keep_days: 1  # 1–7
```

快照包含 Prompt 内容，读取端点 `GET /observability/llm-debug-requests` 因而要求 `admin` scope，
不是普通 `state.read`。疑似 credential 字段及图片 data URL 会被遮蔽；这不是审计总账，调试结束应立即关闭。

```yaml
model_presets:
  active_routing: default        # 当前生效的路由方案名

  defaults:                      # 全局参数默认值，preset 未声明的从这里回退
    temperature: 1.0
    top_p: 0.9
    max_tokens: 4000
    frequency_penalty: 0.3
    presence_penalty: 0.4

  presets:
    deepseek-default:
      provider_kind: deepseek    # 决定参数白名单 + 默认 prompt_style
      api_protocol: chat_completions  # 省略时保持此旧行为
      base_url: https://api.deepseek.com
      api_key: sk-xxx
      model: deepseek-chat
      tool_call_mode: function_calling
      # prompt_style 省略 → 用 provider_kind 默认（deepseek→narrative）
      params:                    # 只写想覆盖的字段
        temperature: 1.0
        frequency_penalty: 0.3
        presence_penalty: 0.4

    claude-sonnet:
      provider_kind: anthropic_compat
      base_url: https://your-oneapi.example/v1
      api_key: sk-xxx
      model: claude-sonnet-4-6
      tool_call_mode: function_calling
      # 可选：仅暴露命名工具预设中的 schema；预设目录归 tool_loop.tool_presets 所有
      tool_preset: claude-minimal
      # prompt_style 省略 → anthropic_compat 默认 xml
      params:
        temperature: 0.8
        # frequency_penalty / presence_penalty 被白名单过滤，不会发给 API

    claude-native-relay:
      provider_kind: anthropic_compat
      api_protocol: anthropic_messages
      # 根地址、.../v1、.../v1/messages 三种形式都可；适配层最终请求 /v1/messages。
      base_url: https://your-claude-relay.example
      api_key: sk-xxx
      # 官方 Anthropic API 选 x_api_key；Claude Code 示例中的
      # ANTHROPIC_AUTH_TOKEN 中转通常选 bearer。
      anthropic_auth_mode: bearer
      model: claude-sonnet-4-6
      tool_call_mode: function_calling

    local-qwen:
      provider_kind: local
      base_url: http://127.0.0.1:8000/v1
      api_key: none
      model: qwen2.5-72b-instruct
      tool_call_mode: xml_fallback
      prompt_style: narrative

    responses-example:           # 独立示例，不加入 routing_profiles 即不会生效
      provider_kind: openai
      api_protocol: responses
      base_url: https://api.example.invalid/v1
      api_key: YOUR_RESPONSES_API_KEY
      model: gpt-5.5
      tool_call_mode: function_calling

  routing_profiles:
    default:                     # 全 DeepSeek，等价旧行为
      chat:           deepseek-default
      intent:         deepseek-default
      sensor_judge:   deepseek-default  # 后台事件裁决；固定短 timeout、零 SDK retry
      probe:          deepseek-default
      summary:        deepseek-default
      detect_emotion: deepseek-default
      consolidation:  deepseek-default
      event_edge_proposer: deepseek-default # optional bounded Memory Event candidate relations
      perform:        deepseek-default   # 句级表演意图映射（仅 performance_mapping.provider=llm 时用到）

    claude-main:                 # 主对话走 Claude，杂活留 DS 省钱
      chat:           claude-sonnet
      intent:         deepseek-default
      sensor_judge:   deepseek-default
      probe:          deepseek-default
      summary:        deepseek-default
      detect_emotion: deepseek-default
      consolidation:  deepseek-default
      event_edge_proposer: deepseek-default
      perform:        deepseek-default
```

### `api_protocol`

`provider_kind` 表示服务商兼容类型，`tool_call_mode` 表示模型的工具能力；两者都不推导
实际 wire API。每个 preset 的 `api_protocol` 独立指定为：

| 值 | 行为 |
|---|---|
| `chat_completions`（默认） | 调用 `client.chat.completions.create(...)`，完整保持旧配置行为 |
| `responses` | 调用 `client.responses.create(...)`，由适配层转换消息、工具和结果 |
| `anthropic_messages` | 调用 Anthropic 原生 `POST /v1/messages`，转换 system、tool_use/tool_result 与 SSE 文本流 |

未知值会在该 preset 首次构建时 fail-fast，并在错误中包含 preset、provider、model 和非法值。
legacy `llm:` 合成 preset 始终是 `chat_completions`。不根据模型名、`provider_kind` 或
`tool_call_mode` 猜测协议，也不会在失败后静默换用另一个 API。

`anthropic_messages` 的认证头由 `anthropic_auth_mode` 明确指定：`x_api_key`（默认，官方
Anthropic API）发送 `x-api-key`，`bearer` 发送 `Authorization: Bearer ...`，用于采用
`ANTHROPIC_AUTH_TOKEN` 约定的 Claude Code 中转。两种模式都会发送
`anthropic-version: 2023-06-01`。该选项对 Chat Completions 和 Responses preset 无效。

Responses 分支将当前的 `system` / `developer` / `user` / `assistant` 上下文保持为结构化输入；
工具定义从 Chat 的 `function` 包装转换为 Responses function schema。多步工具循环中，模型的
`function_call` 和本地执行结果的 `function_call_output` 使用同一个 `call_id` 回填，绝不伪装成
普通 user 文本。适配器统一产出 assistant text、`{id,name,arguments}` 工具调用、完成状态、可选
usage 和仅当前请求可用的 raw response；raw response 不写入 memory 或持久化调试记录。

主聊天已经消费 SDK 真流式输出。`responses` 因而消费 `response.output_text.delta`，并校验函数参数
delta、output item 完成、`response.completed` 与失败/未完成事件；不会悄悄退化为非流式。Responses
请求显式 `store: false`，不使用 `previous_response_id`，每轮上下文仍由 PresenceKit 自己维护。

Anthropic Messages 分支把内部 `system` / `developer` 消息合并到顶层 `system`，把 OpenAI 形状的
function schema 转为 `input_schema`，并把模型的 `tool_use` 与本地执行结果的 `tool_result` 以同一
调用 ID 往返。它消费 `content_block_delta.text_delta` 和 `message_stop`；内部 thinking block 不展示、
不写入历史。

### 三个彼此独立的选择

- `api_protocol`：网关收什么 HTTP 路径与 JSON/SSE 形状（Chat Completions、Responses、Anthropic Messages）。
- `tool_call_mode`：模型是否采用结构化函数调用；`function_calling` 在三种 wire protocol 都可用。
  `xml_fallback` 只供隔离的 Path A probe / relay adapter 把工具描述和调用退化成文本标签，主聊天
  `run_llm()` 不携带 tools，也不会解析聊天正文里的 XML 为工具调用。
- `prompt_style`：system prompt 用自然段还是 XML 包装；它不决定 HTTP 协议，也不把 function calling 变成 XML。

### `tool_call_mode` 取值与 tool loop 的组合行为

| `tool_call_mode` | 隔离 probe adapter（`llm_client.chat(tools=)`） | Brief 28/109 tool loop（有效开关开启） |
|---|---|---|
| `function_calling` | 支持，Path A 严格接受原生 tool call；Path B 主生成不传 tools | 支持：`chat` preset 为此模式且有效开关开启时 `tool_loop_active()` 才可能为真，主生成走 `run_agentic_loop`（多步自主调用） |
| `xml_fallback` | 支持隔离 probe 返回单个 `<tool_call>`；严格解析失败即 fail-soft | 不支持：`tool_loop_active()` 恒为假，即使有效开关开启也维持原 `run_llm` 单发生成；聊天正文不会被当成工具调用 |

只有有效 routing profile 的 `chat` 指向 `function_calling` preset 时，tool loop 才可能激活；
有效开关优先取活跃角色卡 `presence_ext.tool_loop`（`"on"`/`"off"`），字段缺失或非法值才回落
全局 `config.tool_loop.enabled`。`intent` / `probe` 等轻量 preset 的 `tool_call_mode` 与 tool loop 无关
（探针在 tool loop 激活时会被跳过，见 `docs/tools.md` 路径C）。

模型 `tool_preset` 仍只维护内置工具白名单，不保存某次连接发现的动态 MCP 目录。MCP 的可选
domain 分类与筛选归各 server 的 `metadata_mapping` / `metadata_overrides` / `domain_selector`
控制；它们是客户端扩展，不是 MCP 官方分类标准。selector 在 category、MCP allowlist/local
policy、连接、registry、角色 proficiency 和 exclude_tools 之后继续收窄。原生 function-calling
与 tail-brace relay 使用同一份已筛选 schema；任何 preset 或 metadata 都不能自动把 Path A 或
不含 `mcp` 的角色类别扩大到 MCP。

### 路由解析规则

0. **per-char 覆盖**（Brief 29 · 3.2）：活跃角色卡 `presence_ext.model_routing` 声明的 profile
   名若存在于 `routing_profiles` → 替换第 1 步的 `active_routing`；profile 不存在时记 warning
   并回落全局 `active_routing`（fail-open，不因为角色卡配置错误就打不出字）。这一步对所有
   `call_category` 都生效，不只是 `chat`——本我挂 Claude 时 probe/summary/consolidation 等杂
   活类别也会跟着这个 profile 走，是预期行为，卡里自己在 profile 定义里把杂活类别指到便宜
   preset（参照下方 `claude-main` 样例）。
1. 取 `routing_profiles[active_routing]`（第 0 步可能已替换）。
2. 用 `call_category` 查 preset 名；查不到 → 回退到该 profile 的 `chat`；再查不到 → 第一个 preset。
   `sensor_judge` 是例外的兼容链：`sensor_judge → intent → chat → first preset`。所有新建
   profile 应显式声明它，并指向稳定、低成本的 `chat_completions` preset；旧 profile 缺失时仍可
   安全运行。该类别使用 10 秒超时与零 SDK 重试，且按 `preset + category` 独立缓存，不影响同一
   preset 的主聊天策略。失败 fail-closed 为不主动发言的裁决；失败台账经
   `GET /observability/api-calls` 以 `caller=sensor_judge` 查询，记录安全错误分类而不记录 prompt。
   `scenario_reconcile` 使用独立的安全路由：`scenario_reconcile → intent → chat → first preset`；
   旧 profile 缺失该 category 时仍兼容，默认单次超时 8 秒、零 SDK retry，且调用发生在
   可见 Dream 回复之后。模型路由管理面展示实际 effective preset 和来源；reconciler 审计
   只记录 category、preset/source、决定与是否应用，不记录输入、回复、剧本或 Prompt。
   **`probe`/`summary` 等轻量角色未在某个 profile 里单独声明时走的就是这条回退**：
   缺失不报错、不阻塞聊天，直接落到该 profile 的主聊天 preset（Brief 93 §5 核实项，见
   `tests/test_model_presets.py::TestRoutingFallback`）。管理面板「配置」页 §1 的
   probe/summary 只读展示（`GET /character/{char_id}/model-routing` /
   `resolve_routing_info()`）读的就是这份真实解析结果，不是另一套展示专用逻辑。
   少数明确声明“直接 preset 名”的配置可通过 `preset_name` 绕过这条回退链；显式 preset
   不存在时抛 `ValueError`。`practice.reviewer_preset` 使用此严格语义；常规配置推荐用
   `practice.reviewer_category: consolidation`，继续继承 per-character routing profile。
3. `vision` 不进 routing_profiles：继续用独立的 `vision:` 块；手机自动化可选 `phone_control_vision:` 固定覆盖槽位，空字段继承通用 `vision`，不引入多 preset 或 profile。

ModelClient 缓存（`core.model_registry._model_clients`）以**解析出的 preset 名**为 key，不是
call_category 或 profile 名——每次调用都重新走上面 0~2 步解析 preset 名，天然随角色切换取到
正确的 client，无需额外失效逻辑。

模型、路由、代理或 vision 配置热更新时，管理端点会 `await llm_client.reload_client()`：先把
缓存与 vision singleton 从可解析集合摘除，再关闭所有旧 AsyncOpenAI/httpx 连接池；同一底层
client 只关闭一次，单个 close 异常只记 warning，不把设置请求或后端进程带崩。下一次 LLM
调用才按新配置惰性重建。

第 0 步还支持**显式 char_id**（Brief 30，非活跃角色路径，如 Stage 群聊里非活跃角色说话）：
调用方传 `char_id` 时只读该角色自己的卡 `presence_ext.model_routing`，不回落到活跃角色的
override；`char_id=None`（默认）才走活跃角色卡逻辑。`core.model_registry._char_model_routing()`
是这条路径的实现；`core.stage.views.StageCharacterView` 的所有生成方法都显式传 `char_id`。
后台记忆固化（profile / episodic / identity）和梦境 summary / impression 同样使用任务入队或
session 冻结的 `char_id` 直传最终 LLM 调用，不能在执行时重新读取当前活跃角色。

角色卡的 `model_routing` 绑定由 `GET/PATCH /character/{char_id}/model-routing` 管理
（Brief 87 §1，见下方「Admin 接口」）。只有 `null` 或字段缺失表示跟随全局
`active_routing`；字符串值（包括名为 `default` 的 profile）都是显式固定绑定。可选 profile
清单由 `GET /model-presets/routing-profiles` 提供。

### `reasoning_native` / `reasoning_extra_body`（Brief 32 · 内部思考链）

preset 侧可选字段，供 `config.thinking.mode: auto` 判断该 preset 走 native reasoning 还是
前置独白（`mode: native` / `mode: monologue` 显式指定时忽略这两个字段的自动判定语义，
但 `reasoning_extra_body` 仍在 `mode: native` 下生效）：

```yaml
    deepseek-reasoner:
      provider_kind: deepseek
      model: deepseek-reasoner
      tool_call_mode: xml_fallback    # deepseek-reasoner 不支持 function_calling
      reasoning_native: true          # 声明该 preset 有原生思考
      reasoning_extra_body: {}        # 原样经 OpenAI client 的 extra_body 透传，绕过参数白名单
```

- `reasoning_extra_body` 是**逃生舱**：`core/model_registry.py` 的 `PROVIDER_PROFILES` 参数白名单
  只放行 `temperature`/`top_p`/`max_tokens` 这类通用生成参数，reasoning 类参数（o 系
  `reasoning_effort`、anthropic 网关的 thinking budget 等）网关方言差异太大，代码不做每家适配
  （与 Brief 29 §4.3 同一原则）——用户自己按目标网关文档把整个 dict 填进 `reasoning_extra_body`，
  `llm_client` 在构建请求 kwargs 时原样并入 `extra_body=`（OpenAI python client 支持），不经白名单。
- `reasoning_extra_body` 只在**主生成**（`call_category=="chat"`）且解析到 native 路线时被注入；
  `intent`/`probe`/`summary` 等杂活类别不受影响，成本不会因为开了思考而全面翻倍。
- 详见 `cc-tasks/32-内部思考链.md` 与 `config.thinking` 顶层配置块。

---

## provider_kind 适配表

| provider_kind | 参数白名单 | 默认 prompt_style |
|---|---|---|
| `deepseek` | temperature, top_p, max_tokens, frequency_penalty, presence_penalty | narrative |
| `openai` | 同上 | narrative |
| `anthropic_compat` | temperature, top_p, max_tokens（**无 penalty**） | xml |
| `local` | temperature, top_p, max_tokens | narrative |

### 参数合并顺序

```
1. model_presets.defaults（全局默认）
2. preset.params（preset 覆盖）
3. provider_kind 白名单过滤（剔除不支持的参数）
4. max_tokens_override（调用方最后覆盖，如有）
```

---

## prompt_style 说明

| style | 行为 |
|---|---|
| `narrative` | 无操作，等于现状（默认） |
| `xml` | system 层用 `_layer` 名作标签包裹：`<1_system_prompt>…</1_system_prompt>`；user/assistant 不变 |

**重要**：`apply_prompt_style` 在 `sanitize_messages` **之前**调用，因为 `_layer` 字段会被 sanitize 剥掉。

---

## 向后兼容

如果 `config.yaml` **没有** `model_presets` 块，系统自动合成等价结构：
- 识别旧 `llm:` 块的 `base_url` → 推断 `provider_kind`（含 `deepseek` → deepseek；含 `anthropic`/`claude` → anthropic_compat；127.0.0.1/localhost → local；其余 → openai）。
- 合成 `legacy` preset，全部 category 路由到它。
- 行为与原先完全一致。

---

## 新增模型步骤

1. 在 `config.yaml` 的 `model_presets.presets` 下添加 preset（4 行最少：kind/base_url/api_key/model）。
2. 在 `routing_profiles` 下新建或修改一个 profile，把需要切换的 category 指向新 preset。
3. 切换：`PUT /model-presets/active-routing {"active_routing": "new-profile-name"}`，或直接改 `config.yaml` 重启。
4. 如果新 provider 的参数白名单与现有不同，在 `PROVIDER_PROFILES`（`core/model_registry.py`）中添加新条目。

---

## Admin 接口

| 端点 | 说明 |
|---|---|
| `GET /model-presets` | 返回 presets（api_key 打码，含 `api_protocol`）、routing_profiles、active_routing；活动角色有有效固定绑定时附带 `active_character_routing`（角色、profile、实际 chat preset），供管理面提示全局切换不会影响它 |
| `PUT /model-presets/active-routing` | 切换 active_routing 并热重载（仅 model_presets 模式） |
| `PUT /model-presets/presets/{name}` | 新增或更新一个 preset（合并更新；新建须提供 provider_kind；仅 model_presets 模式） |
| `POST /model-presets/presets/{name}/rename` | 重命名 preset；同一次原子写入会更新所有 routing profile 的 category→preset 引用并热重载；目标名不能为空且不得已存在 |
| `DELETE /model-presets/presets/{name}` | 删除一个 preset；被任意 routing_profile 引用或是唯一剩余 preset 时 409 |
| `PUT /model-presets/routing-profiles/{name}` | 新增或更新一个 routing profile 的 call_category → preset 映射（合并更新，值须是已存在的 preset） |
| `GET /model-presets/routing-profiles` | 可选 profile 清单（名字 + 各 category→preset 映射摘要），角色绑定下拉框数据源 |
| `POST /model-presets/presets/{name}/test` | 连通性测试：实际发一条 `max_tokens=1` 的请求，返回 `{ok, latency_ms, error?}`，不经缓存 |
| `GET /llm-params` | 读取当前 chat preset 的生成参数 |
| `PUT /llm-params` | 修改当前 chat preset 的生成参数并热重载（legacy 模式写回 llm: 块） |
| `GET /character/{char_id}/model-routing` | 读取角色卡 `model_routing` 声明 + 解析结果（`effective_profile`/`resolved_chat_preset`）（Brief 87 §1） |
| `PATCH /character/{char_id}/model-routing` | 绑定/清除角色卡的 routing profile；`model_routing: null` 清除声明回落全局 `active_routing`；非法 profile 名 422（Brief 87 §1） |

`GET /settings/prompt-assets` 的 `characters[]` 每项也带 `model_routing`/`effective_profile`/`resolved_chat_preset`
（`resolve_routing_info()` 现算，fail-soft：`model_presets` 配置缺失/损坏时省略这三个字段，不影响角色列表本身），
设置页角色列表可以直接读，不必对每个角色再单独调一次 `GET /character/{id}/model-routing`。

### Forced streaming compatibility (2026-09-09)

Preset `force_stream` defaults to false and supports Chat Completions only. The admin Preset editor saves it through PUT `/model-presets/presets/{name}` with hot reload; GET `/model-presets` returns the configuration. All calls using that preset, including tool decisions and final generation, buffer SSE into the existing normalized result. Tool fragments are assembled by index and require a complete finish and valid JSON before execution. Interrupted streams close and fail. Returned reasoning still uses the separate archive. Request snapshots record the actual stream flag.

Mobile still receives complete HTTP JSON; poll, ack, TTL and relay are unchanged. This is gateway compatibility, independent of client typing animation. Unsupported protocol combinations return 422. Real gateway diagnosis remains observe pending deployment verification.

## 模型目录发现（2026-09-11）

管理面「模型连接」编辑框提供获取可用模型、选择和手填。admin-only
`POST /model-presets/discover` 接收 base_url、api_key、preset_name（可选）、
api_protocol、anthropic_auth_mode；use_base_model 可引用基础模型连接。
空 key 仅在目录地址与已保存连接相同时复用密钥。请求不会保存或修改配置。
根 URL 映射 /v1/models；已有版本/代理路径保留，完整生成端点替换为 /models。
返回 status、models，成功可含 has_more；不支持、空目录、鉴权、格式、网络和超时
分别返回状态，均保留手填。目录不验证生成协议、工具能力或实际生成可用性。
管理面是唯一编辑入口，双端仍消费既有 routing profile，无需新增客户端设置。
本地 36 项回归通过，真实静态资源浏览器清缓存刷新验证了选择与手填降级；
远端目录响应在浏览器测试中模拟，真实中转可用性属于 observe。
## 聊天回合思考读取（2026-09-11）

`current`：GET `/chat/turns/{turn_id}/reasoning` 要求 memory.read，标准 desktop/mobile
profile 可读取已关联的 Reality owner 回合，返回 available/entries（含 parts）。
桌面/手机共享 owner 入口在生成期间关联调用，工具循环子任务同样关联，post-process
前停止采集关联，防止后台思考串入；关联失败不影响回复。旧归档不推测关联。
原 admin-only 全局归档接口不变。无新增生成开关，不修改正文、WS、poll、ack 或 TTL。
`roadmap`：客户端按气泡展示和真机验收、QQ/主动消息/Dream/Stage 回合关联。
前端工单见 cc-tasks/244-frontend-reasoning-handoff.md；按用户要求未跨仓修改。

## Model probe diagnostics (2026-09-11)

Admin-only preset test now uses 256 output tokens, a 30-second total budget and zero SDK retries. It returns category, safe error/hint, HTTP status, error type, declared protocol and request path. Provider bodies and credentials are never echoed; UI uses textContent. Empty visible output is a warning rather than evidence of working conversation. Network/TLS/timeout, authentication, quota, endpoint/model, rejected parameters and response schema are distinguished.

Validation: 69 related tests passed; cache-cleared Chromium Model Routing test rendered quota/protocol/status guidance. Live bounded probes succeeded for the configured Grok Responses and Gemini Chat Completions presets; another relay returned HTTP 403 INSUFFICIENT_BALANCE. No protocol/routing setting was changed. This is unrelated to desktop/device WS protocols. Native clients continue to open the backend management UI; no new local settings or secrets.
