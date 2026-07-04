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

### 路由解析规则

1. 取 `routing_profiles[active_routing]`。
2. 用 `call_category` 查 preset 名；查不到 → 回退到该 profile 的 `chat`；再查不到 → 第一个 preset。
3. `vision` 不进 routing_profiles：继续用独立的 `vision:` 块。

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
| `GET /llm-params` | 读取当前 chat preset 的生成参数 |
| `PUT /llm-params` | 修改当前 chat preset 的生成参数并热重载（legacy 模式写回 llm: 块） |
