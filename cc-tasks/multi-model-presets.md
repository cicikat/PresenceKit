# CC 施工任务：多模型 Preset 接口

> 协作方式：本文档为设计 + 施工说明，交给 Claude Code 实现。
> 施工前必读 `AGENTS.md` 与 `docs/prompt-layers.md`（涉及 prompt 边界）。

---

## 0. 一句话目标

把"只能跑一个 DeepSeek"重构成"按任务分流的多模型 preset 系统"：主对话可以走 Claude / DS / 本地，
轻量调用（probe / summary / detect_emotion）可以指向便宜模型；每个 preset 自带**生成参数默认适配**和
**prompt 结构适配**，并且**完全向后兼容**现有 `config.yaml` 的扁平 `llm:` 块。

---

## 1. 已定的设计决策（不要偏离）

| 决策点 | 选定方案 | 含义 |
|---|---|---|
| 路由颗粒度 | **按任务分流** | 复用现有 `call_category`（chat/intent/probe/summary/detect_emotion/consolidation/vision），每个 category 映射到一个 preset |
| Claude 接入 | **OpenAI 兼容端点** | 全部走 `AsyncOpenAI`，不引入第二套 SDK。Claude 经 one-api / openrouter / 官方 OpenAI 兼容层接入 |
| Prompt 适配深度 | **轻量转换钩子** | preset 带 `prompt_style`（`narrative` / `xml`），在 `sanitize_messages` 前加一道可选 transform；**不动 `prompt_builder` 主逻辑** |

非目标（本期不做）：Anthropic 原生 API、prompt caching、per-preset 独立 prompt 模板分叉、向量库/RAG 改动。

---

## 2. 现状（施工基准，已核对代码）

- `core/llm_client.py` 是**唯一 LLM 出口**。每次调用直接读 `get_config()["llm"]`，用模块级单例
  `_client: AsyncOpenAI`（+ `_vision_client`）。
- 已有的能力抽象可以复用，**不要重造**：
  - `tool_call_mode`：`function_calling` / `xml_fallback`（见 `chat()` 内分支与 `_build_xml_tool_desc`）。
  - `call_category`：`_CALL_TIMEOUTS` 已按 probe/intent/detect_emotion/summary/consolidation/chat/vision 分类。
  - `reload_client()`：把单例清空，下次调用按最新 config 重建（热重载入口）。
  - `sanitize_messages()`（`core/prompt_layer.py`）：发送前剥离 `_layer` / `_drop_priority` / `speaker_id` / `timestamp`。
- 对外的入口函数：`chat()`、`chat_stream()`、`summarize_turn()`、`detect_emotion()`，以及类封装 `LLMClient`。
  - 注意：`summarize_turn()` 和 `detect_emotion()` 目前**直接调 `_get_client()` + `get_config()["llm"]["model"]`**，
    没走 `chat()`。重构时这两个也必须改为走 preset 路由（它们天然属于轻量 category）。
- 调用方约 60 处通过 `from core import llm_client` + `llm_client.chat(..., call_category=...)`。
  **公共函数签名保持不变**，只换内部实现，调用方零改动。
- 现有 admin 接口 `admin/routers/settings_llm.py`：`GET/PUT /llm-params`、`GET/PUT /vision-params`。
  本期保留并让 `/llm-params` 作用于"当前 chat preset"（见 §7）。

---

## 3. 配置 schema（新增 `model_presets` 块）

新增顶层 `model_presets`。`llm:` 与 `vision:` 旧块保留，作为向后兼容回退（见 §6）。

```yaml
model_presets:
  active_routing: default        # 当前生效的路由方案名（见 routing_profiles）

  # 全局参数默认值。preset 未声明的参数从这里回退。
  defaults:
    temperature: 1.0
    top_p: 0.9
    max_tokens: 4000
    frequency_penalty: 0.3
    presence_penalty: 0.4

  presets:
    deepseek-default:
      provider_kind: deepseek    # 决定参数白名单 + 默认 prompt_style（见 §4）
      base_url: https://api.deepseek.com
      api_key: sk-xxx
      model: deepseek-chat
      tool_call_mode: function_calling
      # prompt_style 省略 → 用 provider_kind 默认（deepseek→narrative）
      params:                    # 覆盖 defaults，仅声明想改的
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
        # frequency_penalty / presence_penalty 即使 defaults 里有，
        # 也会被 provider_kind 白名单过滤掉（见 §4），不会发给 API

    local-qwen:
      provider_kind: local
      base_url: http://127.0.0.1:8000/v1
      api_key: none
      model: qwen2.5-72b-instruct
      tool_call_mode: xml_fallback
      prompt_style: narrative

  # 路由方案：call_category → preset 名。可以存多套方案一键切换。
  routing_profiles:
    default:                     # 全 DeepSeek，等价旧行为
      chat:           deepseek-default
      intent:         deepseek-default
      probe:          deepseek-default
      summary:        deepseek-default
      detect_emotion: deepseek-default
      consolidation:  deepseek-default

    claude-main:                 # 主对话上 Claude，杂活留 DS 省钱
      chat:           claude-sonnet
      intent:         deepseek-default
      probe:          deepseek-default
      summary:        deepseek-default
      detect_emotion: deepseek-default
      consolidation:  deepseek-default
```

路由解析规则：
1. 取 `routing_profiles[active_routing]`。
2. 用 `call_category` 查 preset 名；查不到 → 回退到该 profile 的 `chat`；再查不到 → 回退到第一个 preset。
3. `vision` **不进** routing_profiles：视觉继续用独立的 `vision:` 块和 `_vision_client`（本期不动），
   但 §4 的参数过滤思路对它不适用，保持现状即可。

---

## 4. provider_kind 默认适配（"默认适配"的核心）

不同模型家族对生成参数的容忍度不同（典型：Claude 经兼容层时 `frequency_penalty` / `presence_penalty`
常被忽略或报错）。用 `provider_kind` 驱动**参数白名单 + 默认 prompt_style**，让 preset 可以写得很薄。

在 `llm_client.py`（或新建 `core/model_registry.py`）定义：

```python
PROVIDER_PROFILES = {
    "openai": {
        "params": {"temperature", "top_p", "max_tokens",
                   "frequency_penalty", "presence_penalty"},
        "default_prompt_style": "narrative",
    },
    "deepseek": {
        "params": {"temperature", "top_p", "max_tokens",
                   "frequency_penalty", "presence_penalty"},
        "default_prompt_style": "narrative",
    },
    "anthropic_compat": {        # Claude 经 OpenAI 兼容层
        "params": {"temperature", "top_p", "max_tokens"},  # 不发 penalty
        "default_prompt_style": "xml",
    },
    "local": {                   # vLLM / llama.cpp / ollama 等
        "params": {"temperature", "top_p", "max_tokens"},
        "default_prompt_style": "narrative",
    },
}
_FALLBACK_PROFILE = PROVIDER_PROFILES["openai"]
```

**参数合并 + 过滤顺序**（实现成一个纯函数，便于单测）：

```
resolved = {}
resolved.update(model_presets.defaults)      # 1. 全局默认
resolved.update(preset.get("params", {}))    # 2. preset 覆盖
allow = PROVIDER_PROFILES[provider_kind]["params"]
resolved = {k: v for k, v in resolved.items() if k in allow}   # 3. 白名单过滤
# max_tokens_override 仍然最后生效（来自调用方）
```

`prompt_style` 解析：`preset.get("prompt_style") or PROVIDER_PROFILES[kind]["default_prompt_style"]`。

这样 `claude-sonnet` preset 里就算 `defaults` 带着 penalty，也不会发给 Claude；而 DS preset 正常带上。
新增模型时，作者只填 4 行（kind/base_url/api_key/model）就有合理默认。

---

## 5. 代码改造：adapter + registry

把 `llm_client.py` 内部从"读扁平 llm 块"改为"按 call_category 解析 preset"。**对外函数签名不变。**

### 5.1 ModelClient（每个 preset 一个）

```python
@dataclass
class ModelClient:
    name: str
    provider_kind: str
    model: str
    tool_call_mode: str
    prompt_style: str            # "narrative" | "xml"
    params: dict                 # 已合并 + 白名单过滤后的生成参数
    client: AsyncOpenAI          # base_url/api_key/http_client 已配好
```

### 5.2 registry（缓存 + 热重载）

```python
_model_clients: dict[str, ModelClient] = {}   # preset 名 → 实例

def get_model_client(call_category: str) -> ModelClient:
    preset_name = _resolve_preset_name(call_category)   # §3 路由规则
    if preset_name not in _model_clients:
        _model_clients[preset_name] = _build_model_client(preset_name)
    return _model_clients[preset_name]

def reload_client():
    global _model_clients, _vision_client
    _model_clients = {}
    _vision_client = None
```

`_build_model_client`：读 preset → 复用现有 `_make_http_client(_get_proxy_url())`（代理逻辑不变）→
合并参数（§4）→ 解析 prompt_style → 建 `AsyncOpenAI(api_key, base_url, http_client)`。

### 5.3 改写出口函数

- `chat()`：把 `cfg = get_config()["llm"]; client = _get_client(); model = cfg["model"]` 这段
  换成 `mc = get_model_client(call_category)`，之后用 `mc.model` / `mc.client` / `mc.tool_call_mode` / `mc.params`。
  `_gen_kwargs` 改为 `dict(**mc.params, timeout=_timeout)`（`max_tokens_override` 仍覆盖 `mc.params["max_tokens"]`）。
- `chat_stream()`：同样走 `get_model_client("chat")`（或传入的 category）。
- `summarize_turn()`：走 `get_model_client("summary")`，不再直接 `_get_client()`。
- `detect_emotion()`：走 `get_model_client("detect_emotion")`。
- `_get_client()` 可保留为"取 chat preset 的 client"的薄封装，或删除并全部改引用（择一，保持一致）。
- `_get_vision_client()` / vision 分支：**保持不变**。

### 5.4 prompt_style 转换钩子（轻量）

新增 `core/prompt_style.py`：

```python
def apply_prompt_style(messages: list[dict], style: str) -> list[dict]:
    if style == "narrative":
        return messages                 # no-op，等于现状
    if style == "xml":
        return _to_xml(messages)        # 见下
    return messages
```

`_to_xml` 约束（保守，避免破坏召回/裁剪语义）：
- 只处理 `role == "system"` 的层；`user` / `assistant` 原样保留。
- 用消息里的 `_layer` 名做标签：`<{layer}>{content}</{layer}>`；无 `_layer` 用 `<context>`。
- 标签名做安全化（非 `[a-zA-Z0-9_]` 的字符替换为 `_`）。
- **顺序不变、不合并、不丢层**。drop_priority 裁剪已在 `prompt_builder` 里完成，这里不碰。

调用位置（关键，因为 `_layer` 会被 sanitize 剥掉）：在 `chat()` / `chat_stream()` 内，
**先** `messages = apply_prompt_style(messages, mc.prompt_style)`，**再** `sanitize_messages(messages)`。
顺序写反就拿不到 `_layer` 了。

---

## 6. 向后兼容（必须做，保证安全上线）

启动/热重载时，若 `config.yaml` **没有** `model_presets` 块：
合成一个等价 preset，全部 category 路由到它。逻辑：

```python
def _synth_legacy_presets(cfg) -> dict:
    llm = cfg.get("llm", {})
    preset = {
        "provider_kind": _kind_from_legacy(llm),   # 见下
        "base_url": llm["base_url"],
        "api_key": llm["api_key"],
        "model": llm["model"],
        "tool_call_mode": llm.get("tool_call_mode", "function_calling"),
        "params": {k: llm[k] for k in
                   ("temperature","top_p","max_tokens",
                    "frequency_penalty","presence_penalty") if k in llm},
    }
    return {"active_routing": "default",
            "defaults": {},
            "presets": {"legacy": preset},
            "routing_profiles": {"default": {"chat": "legacy"}}}
```

`_kind_from_legacy`：base_url 含 `deepseek` → `deepseek`，含 `anthropic`/`claude` → `anthropic_compat`，
`127.0.0.1`/`localhost` → `local`，否则 `openai`。

结果：现有 `config.yaml` 一字不改也能跑，且行为与今天一致。这是回归测试的基线。

---

## 7. Admin 接口（扩展 `admin/routers/settings_llm.py`）

最小集（本期）：
- `GET /model-presets`：返回 `presets` 列表（api_key 打码）、`routing_profiles`、`active_routing`。
- `PUT /model-presets/active-routing`：`{"active_routing": "claude-main"}` → 校验存在 → 写 config → `reload_config()` + `llm_client.reload_client()`。
- 保留 `GET/PUT /llm-params`：改为读写"当前 active_routing 的 chat preset 的 params"，
  这样旧前端调参面板继续可用。若处于 legacy 合成态，则写回旧 `llm:` 块。

可选（Phase 3，本期不强制）：preset 的增删改 CRUD、routing_profiles 编辑、前端 UI。

校验沿用现有范围：temperature 0–2、top_p 0–1、max_tokens 100–4000、penalty 0–2。

---

## 8. 分阶段实施（建议提交粒度）

- **Phase 1（核心，必须）**：`model_registry` / ModelClient / 路由解析 / 参数合并+白名单 / 向后兼容合成 /
  改写四个出口函数。`prompt_style` 先接 no-op（全部当 narrative）。先保证零行为变化跑通。
- **Phase 2**：`prompt_style.py` 的 xml transform + 在 chat/chat_stream 接线。
- **Phase 3（可选）**：admin 路由扩展 + 前端。

每个 Phase 独立可测、可回滚。Phase 1 合并后线上行为应与改造前**逐字节等价**（同一 config）。

---

## 9. 测试要求

新增 `tests/test_model_presets.py`，至少覆盖：
1. **参数合并+过滤**：anthropic_compat preset，defaults 带 penalty → resolved 不含 penalty；deepseek preset → 含。
2. **prompt_style 解析**：preset 省略 → 取 provider_kind 默认；显式声明 → 以显式为准。
3. **路由回退**：category 未配 → 落到 chat preset；profile 缺失 → 落到第一个 preset。
4. **向后兼容合成**：给只有 `llm:` 块的 config，`get_model_client("chat")` 能正确建出等价 client，
   `_kind_from_legacy` 各分支正确。
5. **xml transform**（Phase 2）：system 层被 `<layer>` 包裹、user/assistant 不变、顺序不变、标签名安全化；snapshot 断言。

回归：现有 `tests/test_detect_emotion.py` 等依赖 `llm_client` 的测试必须仍通过（因为 summarize/detect 改了路由路径）。
按 `docs/dev-environment.md` 在 Windows agent 环境跑 `pytest`（注意 TEMP 权限那条）。

---

## 10. 文档与 doc sync hook（别被 Stop hook 拦住）

`.claude/hooks/` 的 Stop hook 会在改了代码却没改对应文档时拦截。本任务需要：
- 新建 `docs/model-presets.md`：写清 schema、provider_kind 适配表、路由规则、向后兼容、扩展新模型的步骤。
- 在 `AGENTS.md` 的"任务 → 读哪个文档"表加一行：改多模型/preset/LLM 接入 → `docs/model-presets.md`；
  "关键文件速查"里把 `core/llm_client.py` 的描述更新为"多 preset adapter + 路由"，并登记 `core/model_registry.py`、`core/prompt_style.py`。
- 若仍被拦且某文件确无需文档，按 hook 约定显式声明：`no doc update needed: <原因>`。

---

## 11. 给 CC 的施工 checklist

- [ ] 读 `AGENTS.md`、`docs/prompt-layers.md`、本文件
- [ ] Phase 1：建 registry/ModelClient/路由/参数合并/向后兼容；改写 `chat`/`chat_stream`/`summarize_turn`/`detect_emotion`
- [ ] 跑回归（detect_emotion 等）确认零行为变化
- [ ] Phase 2：`prompt_style.py` xml transform + 接线（先 transform 后 sanitize）
- [ ] Phase 3（可选）：admin 路由 + UI
- [ ] 写 `tests/test_model_presets.py` 全部用例
- [ ] 写 `docs/model-presets.md` + 更新 `AGENTS.md`
- [ ] 在 `config.example.yaml` 加 `model_presets` 示例块（DS + claude + local 三个 preset）
```
