# docs/perform-mapping.md — 句级表演意图映射（Brief 20）

## 定位

把 LLM 回复中本来就存在的 `*动作*` / `_感受_` 叙事段（NMP，`core/narrative_parser.py`）映射为
受控表演 spec（`perform`），附着在 `message_segments` 的 `say` 段上下发桌面端，驱动 3D/Live2D
角色逐句表演。聊天主 LLM 的 prompt 不变——语义层就是它已经在写的动作描写。

与 mood 基调层（`core/pipeline._maybe_push_avatar_directive`，Phase A v8 推送）是两层：mood 是
整轮对话的底色，`perform` 是句级覆盖，字段为 `null` 时客户端回落到 mood 基调层。

模块：`core/perform_mapper.py`，唯一入口 `enrich_say_segments(reply, say_segs, *, char_id)`。
接入点：`admin/routers/chat.py`（桌面流式路径）、`core/turn_sink.py`（fanout 路径），均在
`build_say_segments()` 之后、`push_segments` 之前调用。协议契约见 `docs/channels.md` §句级表演 spec。

## 段落归属

内部重新 `parse_narrative_segments(reply)` 取全类型段序列，按顺序扫描：累积遇到的 `do`/`feel`
文本，遇到 `say` 段时把「累积文本 + 该 say 文本本身」作为该 say 段的映射输入并清空累积；末尾
残余的 do/feel（say 之后才出现的动作）追加挂靠到**最后一个** say 段。say 段与调用方传入的
`say_segs` 按序对齐——两边都来自同一个 parser，顺序理应一致；数量对不上（或 parser 抛异常）
直接 fail-open，原样返回输入。

## provider = rules（默认，v1）

纯规则词典（`_WORD_RULES`，模块内常量表），对映射输入文本做关键词/正则匹配 → 填字段：

- 词典匹配优先在 do/feel 文本上进行；`expression` 字段的规则额外允许匹配 say 文本（明确的
  表情词，如"笑死我了"）。posture/head/gaze 只认 do/feel 文本。
- 同一字段多条规则命中时，**词典表内靠前的规则赢**，后续同字段规则被跳过（包括它附带的
  energy/intensity 副作用）。
- `energy` 基准 0.5，累加各命中规则的 delta 与 say 文本标点启发（`！`结尾 +0.2，`……`结尾或
  出现 ≥2 次 -0.15）后 clamp 到 0~1。`intensity` 默认 0.6，仅"鼓起脸/瞪了…一眼/哼"（愤怒）
  规则会覆盖为 0.5。
- 全程零匹配（含标点启发）→ 该 say 段不带 `perform` 键，不输出全 `null` 的空壳。

新增/调整信号词：直接编辑 `core/perform_mapper.py` 里的 `_WORD_RULES` 列表，注意规则顺序即
优先级，新增词条尽量放在语义更明确的位置之后，避免抢占更具体的规则。

## provider = llm（v2，config 门控）

整条回复一次调用（不是逐句调用）：把全部 say 段编号 + 各自挂靠的动作文本一次性交给 LLM，
要求输出等长 JSON 数组（每项为 perform 对象或 `null`）。返回值经 `_sanitize_llm_perform`
严格 schema 校验——非法枚举值/类型置为 `null`，四个离散字段全为空时整项按未命中处理；
JSON 解析失败或数组长度不匹配 → 该轮全部 fail-open（不带任何 `perform`）。

调用走 `core.llm_client.chat(..., call_category="perform")`（`_CALL_TIMEOUTS["perform"] = 10.0`，
`core/model_registry.py` 的 `_all_categories` 与 `config.example.yaml` 的 `routing_profiles`
示例已包含 `perform`，可路由到便宜快模型）。外层再套 `asyncio.wait_for(..., timeout=llm_timeout_sec)`
兜底（默认 3.0 秒，可配置）——超时直接放弃标注，segments 照常下发，绝不阻塞主推送。

## config

```yaml
performance_mapping:
  enabled: true        # false = enrich_say_segments 直接透传，行为与现在完全一致
  provider: rules       # rules | llm
  llm_timeout_sec: 3.0
```

`core/config_loader.get_config().get("performance_mapping", {})` 按现有模式读取，缺块时等价于
`enabled: true` + `provider: rules`（零成本零延迟，不引入行为变化）。

## fail-open 语义

`enrich_say_segments` 是唯一对外入口，内部任何异常（parser 抛错、字段数量不齐、LLM 超时/
解析失败）都被捕获并原样返回输入的 `say_segs`——绝不抛出，绝不影响 `message_segments` 主
推送流程。这与 `record_assistant_turn` 内 `message_segments` fanout 本身的 fail-open 姿态一致
（见 `core/turn_sink.py`）。
