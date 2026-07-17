# docs/model-presets.md — 多模型 Preset 系统

## 概述

把"只能跑一个 DeepSeek"重构成"按任务分流的多模型 preset 系统"：
- 主对话可以走 Claude / DS / 本地；轻量调用（probe / summary / detect_emotion）可以指向便宜模型。
- 每个 preset 自带**生成参数默认适配**（provider 白名单过滤）和 **prompt 结构适配**（narrative / xml）。
- **完全向后兼容**：现有 `config.yaml` 的扁平 `llm:` 块一字不改也能跑。

---

## 关键文件

| 文件 | 职责 |
|---|---|
| `core/model_registry.py` | ModelClient 构建 + 缓存、路由解析、参数合并+白名单、向后兼容合成 |
| `core/prompt_style.py` | prompt_style 转换钩子（narrative / xml） |
| `core/llm_client.py` | 唯一 LLM 出口，调用 model_registry 路由，在 sanitize 前应用 prompt_style |
| `admin/routers/settings_llm.py` | HTTP 接口：`/model-presets`、`/model-presets/active-routing`、`/llm-params` |

---

## 配置 schema

新增顶层 `model_presets` 块。旧 `llm:` 与 `vision:` 块保留。

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
      # prompt_style 省略 → anthropic_compat 默认 xml
      params:
        temperature: 0.8
        # frequency_penalty / presence_penalty 被白名单过滤，不会发给 API

    local-qwen:
      provider_kind: local
      base_url: http://127.0.0.1:8000/v1
      api_key: none
      model: qwen2.5-72b-instruct
      tool_call_mode: xml_fallback
      prompt_style: narrative

  routing_profiles:
    default:                     # 全 DeepSeek，等价旧行为
      chat:           deepseek-default
      intent:         deepseek-default
      probe:          deepseek-default
      summary:        deepseek-default
      detect_emotion: deepseek-default
      consolidation:  deepseek-default
      perform:        deepseek-default   # 句级表演意图映射（仅 performance_mapping.provider=llm 时用到）

    claude-main:                 # 主对话走 Claude，杂活留 DS 省钱
      chat:           claude-sonnet
      intent:         deepseek-default
      probe:          deepseek-default
      summary:        deepseek-default
      detect_emotion: deepseek-default
      consolidation:  deepseek-default
      perform:        deepseek-default
```

### `tool_call_mode` 取值与 tool loop 的组合行为

| `tool_call_mode` | 单发 FC（`llm_client.chat(tools=)`） | Brief 28 tool loop（`config.tool_loop.enabled=true`） |
|---|---|---|
| `function_calling` | 支持，探针/路径A/B 正常调用 | 支持：`chat` preset 为此模式时 `tool_loop_active()` 才可能为真，主生成走 `run_agentic_loop`（多步自主调用） |
| `xml_fallback` | 支持（`<tool_call>` 标签解析） | 不支持：`tool_loop_active()` 恒为假，即使总开关打开也维持原 `run_llm` 单发生成——小模型没有可靠的多步自主调用能力，这是设计边界，不是遗漏 |

只有 `routing_profiles[active_routing].chat` 指向的 preset 是 `function_calling` 时，
tool loop 才可能激活；`intent` / `probe` 等轻量 preset 的 `tool_call_mode` 与 tool loop 无关
（探针在 tool loop 激活时会被跳过，见 `docs/tools.md` 路径C）。

### 路由解析规则

0. **per-char 覆盖**（Brief 29 · 3.2）：活跃角色卡 `presence_ext.model_routing` 声明的 profile
   名若存在于 `routing_profiles` → 替换第 1 步的 `active_routing`；profile 不存在时记 warning
   并回落全局 `active_routing`（fail-open，不因为角色卡配置错误就打不出字）。这一步对所有
   `call_category` 都生效，不只是 `chat`——本我挂 Claude 时 probe/summary/consolidation 等杂
   活类别也会跟着这个 profile 走，是预期行为，卡里自己在 profile 定义里把杂活类别指到便宜
   preset（参照下方 `claude-main` 样例）。
1. 取 `routing_profiles[active_routing]`（第 0 步可能已替换）。
2. 用 `call_category` 查 preset 名；查不到 → 回退到该 profile 的 `chat`；再查不到 → 第一个 preset。
3. `vision` 不进 routing_profiles：继续用独立的 `vision:` 块。

ModelClient 缓存（`core.model_registry._model_clients`）以**解析出的 preset 名**为 key，不是
call_category 或 profile 名——每次调用都重新走上面 0~2 步解析 preset 名，天然随角色切换取到
正确的 client，无需额外失效逻辑。

第 0 步还支持**显式 char_id**（Brief 30，非活跃角色路径，如 Stage 群聊里非活跃角色说话）：
调用方传 `char_id` 时只读该角色自己的卡 `presence_ext.model_routing`，不回落到活跃角色的
override；`char_id=None`（默认）才走活跃角色卡逻辑。`core.model_registry._char_model_routing()`
是这条路径的实现；`core.stage.views.StageCharacterView` 的所有生成方法都显式传 `char_id`。

角色卡的 `model_routing` 绑定由 `GET/PATCH /character/{char_id}/model-routing` 管理
（Brief 87 §1，见下方「Admin 接口」），可选 profile 清单由
`GET /model-presets/routing-profiles` 提供。

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
| `GET /model-presets` | 返回 presets（api_key 打码）、routing_profiles、active_routing |
| `PUT /model-presets/active-routing` | 切换 active_routing 并热重载（仅 model_presets 模式） |
| `PUT /model-presets/presets/{name}` | 新增或更新一个 preset（合并更新；新建须提供 provider_kind；仅 model_presets 模式） |
| `DELETE /model-presets/presets/{name}` | 删除一个 preset；被任意 routing_profile 引用或是唯一剩余 preset 时 409 |
| `PUT /model-presets/routing-profiles/{name}` | 新增或更新一个 routing profile 的 call_category → preset 映射（合并更新，值须是已存在的 preset） |
| `GET /model-presets/routing-profiles` | 可选 profile 清单（名字 + 各 category→preset 映射摘要），角色绑定下拉框数据源 |
| `POST /model-presets/presets/{name}/test` | 连通性测试：实际发一条 `max_tokens=1` 的请求，返回 `{ok, latency_ms, error?}`，不经缓存 |
| `GET /llm-params` | 读取当前 chat preset 的生成参数 |
| `PUT /llm-params` | 修改当前 chat preset 的生成参数并热重载（legacy 模式写回 llm: 块） |
| `GET /character/{char_id}/model-routing` | 读取角色卡 `model_routing` 声明 + 解析结果（`effective_profile`/`resolved_chat_preset`）（Brief 87 §1） |
| `PATCH /character/{char_id}/model-routing` | 绑定/清除角色卡的 routing profile；`model_routing: null` 清除声明回落全局 `active_routing`；非法 profile 名 422（Brief 87 §1） |
