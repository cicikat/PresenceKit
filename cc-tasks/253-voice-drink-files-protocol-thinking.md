# 工单 253：声调 / 喝酒 / 文件产物 / 协议 400 / 独白展示

日期：2026-09-15。先写工单再逐项勾选施工。涉及工具、协议出口、思考展示与桌面附件时，按三仓闭环核对；安全边界不按用户原话字面放大。

## 背景（查代码后）

- 初始媒体入口 `core/media_processor.py` 只处理图和 txt/md/docx。施工核实另有 `/transcribe` 本地 Whisper 文字入口；253.6 在保留旧入口兼容的基础上补命名 STT、语音上传/QQ 入口与声调。听歌已有 `play_song`（desktop 类）。
- 工具注册表已有只读 `fs_list` / `fs_read`（`fs_access.allow_roots`，默认关，永远拒 `data/` 与 secrets），以及 Agent Runtime 的 `workspace_*`（授权根、写要确认）。玩具箱 `write_toy_file` 只能写三份固定文本。聊天侧**没有** Gemini 式产物卡片 / 下载 / HTML 预览。
- Agent Runtime（Brief 229–233）是**后台耐久任务 + 受限 workspace**，不是聊天里的无限 coding agent。`_TOOL_REGISTRY` + `execute(origin=)` 仍是聊天工具边界。
- `GET/POST /settings/thinking` 已存在。独白走 `call_category=monologue`，注入 `11.7_inner_monologue`；桌面思考气泡读 `GET /chat/turns/{turn_id}/reasoning`，但 owner 回合绑定与查询**只收 `purpose=chat`**，独白即使落盘也不会出现在气泡里。
- 真实 400：`chat_turn` → `llm_protocol._create` → Chat Completions。复杂 tool 请求失败、普通对话与外部 Agent 软件正常。协议层已有 Chat / Responses / Anthropic 三出口，但 Chat Completions **把 SDK `model_dump()` 整包回填进下一轮**，只剥了 `reasoning*`，`refusal` / `annotations` / `audio` 等字段和内部 `_continuity_receipt` 以外的多余键会原样出网。中转返回的是模糊 `upstream_error`。

## 异议与总原则（先读）

1. **不能读「后端任何文件」。** `config.yaml`、`secrets*`、token、`.env`、项目 `data/` 沙盒永远拒绝。用户发路径只能打到已授权根（`fs_access` / `workspace_access`）或用户自己上传的资料库。远程部署下本地 fs 保持关闭。
2. **不能把 Cursor/Claude Code 塞进陪伴聊天当一个工具。** 聊天 Path C 是短回合、有预算、有 origin 闸门的工具循环。长期改仓库、跑测试、开 shell 属于 Agent Runtime 工作会话，必须有授权根、manifest、确认与观测，不能变成「模型想写哪就写哪」。
3. **独白优先是展示策略，不是生成模式。** `thinking.mode` 的 auto/native/monologue 继续决定「这次有没有前置独白 / 要不要开原生思考」。展示层：有独白就先显示独白，没有再用原生思考兜底。两者可以并存，不互相覆盖生成。
4. **声调是感知层，不是新人格。** 转写 + 粗粒度语调标签注入 prompt；听歌复用 `play_song`，不在本单做完整「一起听」ActivitySession。
5. **喝酒必须低存在感。** 用户提起且角色愿意才调用；状态可衰减；酒醉表现走既有叙事段 + perform 词典，不新增客户端表情枚举。

施工顺序：1 协议 400（现成故障）→ 2 独白展示 → 3 文件产物（聊天可感知）→ 4 授权读路径体验 → 5 喝酒小工具 → 6 声调 v0。后面的单依赖前面的边界，不并行改协议与工具暴露面。

---

## 工单 1 — Chat Completions 工具续轮 400（协议出口收口）

### 要做

- [x] `core/llm_protocol.py` 为 Chat Completions 增加与 `responses_input` 对称的 **wire 重建**：
  - 消息只保留协议允许键：`system/user/assistant/tool` + `content` / `tool_calls` / `tool_call_id`（可选 `name`）。
  - assistant `tool_calls` 只保留 `id` / `type=function` / `function.{name,arguments}`；`arguments` 必须是 JSON 字符串。
  - 丢掉 SDK dump 带来的 `refusal`、`annotations`、`audio`、`function_call`、`reasoning*` 以及内部键。
  - `content is None` 且带 tool_calls 时改成 `""`（中转比官方更严）。
  - 工具 schema 重建为 `{type:function, function:{name,description?,parameters,strict?}}`，参数继续走已有 `anyOf` 转换。
- [x] `_chat_assistant_message` **停止 `model_dump()`**，改白名单组装；tool loop 回填的 continuation 必须是下一轮可再送出的形状。
- [x] `_create` / 流式 Chat Completions 出口都走同一套重建；Responses / Anthropic 入口不变。
- [x] 400 观测：`api_call_log` 记 `error_category=upstream_request_rejected`，`output_hint` 只留异常类名；不把请求体、密钥、工具原文写入普通日志。
- [x] 定向测试：带多余字段的 assistant/tool 续轮被剥净；合法 tool_calls 往返；既有 anyOf / Responses 测试不回退。

### 验收

- `pytest -n auto tests/test_llm_protocol_responses.py tests/test_llm_client_chat_turn.py` 以及本单新增测试。
- 文档：`docs/model-presets.md`、`docs/known-issues.md`、`docs/three-repo-interface-catalog.md` 记 current/observe。
- 不改客户端设置、WS/poll/ack。运行中后端需重启后，用原先会 400 的复杂 tool 请求复测（observe：真实中转）。

### 不做

- 不在本单新接第四种厂商协议。
- 不按模型名猜协议、不静默改 `api_protocol`。
- 不把完整请求体变成默认落盘（继续走 opt-in `llm_debug_requests`）。

---

## 工单 2 — 前置独白优先出现在思考气泡

依赖工单 1 的协议出口不把独白当 native reasoning 泄漏。

### 要做

- [x] 独白生成成功后，归档为 reasoning part `source=monologue`，并**允许绑定 owner `turn_id`**（扩 `_OWNER_TURN_PURPOSES` / `query_turn`，不要再把 monologue 从气泡查询里滤掉）。
- [x] `GET /chat/turns/{turn_id}/reasoning` 返回顺序：有独白则独白条目在前，原生思考随后；无独白则与现在一样只出 native。
- [x] `GET/POST /settings/thinking` 增加只影响展示的 `display_prefer_monologue`（默认 true）。false 时原生在前、独白在后。不改变 `mode` 的生成选择。
- [x] 管理面思考卡补一句说明：「前置独白更容易用 prompt 控文风；气泡优先显示独白，没有再用原生思考。」桌面继续消费原 reasoning API，不新增 IPC 字段。
- [x] 测试：绑定、排序、开关、未开启思考时不写空独白。

### 验收

- 定向测试覆盖 store + settings。
- 管理面浏览器：思考卡能看到说明和开关（需硬刷新）。
- observe：桌面展开气泡在真实独白回合中先看到独白。

### 不做

- 不把独白写入 short_term / event_log。
- 不强制 `mode=monologue`。
- 不改手机（思考 UI 仍是 roadmap）。

---

## 工单 3 — 聊天产物文件（写 + 下载 + 预览）

### 异议

「任意格式文件」可以，但必须落在 **sandbox 产物目录**，不能写授权 workspace 以外、更不能写仓库源码。HTML 预览只读、沙箱 iframe，不执行角色写出的脚本去打本地接口。

「类似 agent coding 的工具」**不能作为普通聊天工具交付**。聊天里只保留：

- `write_artifact` / `read_artifact` / `list_artifacts`：给用户看的一份文件（代码、markdown、html、csv…）。
- 已有 `workspace_*`：只在管理员配置了 `workspace_access.roots` 且非远程时，改用户明确授权的目录；覆盖/删除仍要确认。

长期多步改项目、跑测试、装依赖 → 继续走 Agent Runtime work session，本单只把「聊天里做出一份可下载文件」补齐。

### 要做

- [x] 产物根：`get_paths()` 下 per-uid/char 的 artifacts，原子写，扩展名白名单（txt/md/html/css/js/json/csv/py/yaml 等文本；体积上限），禁止路径穿越。
- [x] 三个 artifacts 工具注册进 `_TOOL_REGISTRY`（examples/keywords 齐），默认不进 Path A 探针；Path C 需 `tool_exposure` 或角色声明才可见。
- [x] 成功写入后，turn payload 带有界 `artifacts[]`（id、filename、mime、size、download_url），**不含正文、不含绝对路径**。
- [x] 下载：`GET /chat/artifacts/{id}`（chat scope）；HTML/文本预览用独立只读端点，CSP 沙箱。
- [x] 管理面观测页与桌面聊天气泡：文件卡片 + 下载按钮；html/md/txt 可预览。手机本单只保证 payload 字段，UI 标 roadmap。
- [x] 观测：`GET /observability/chat-artifacts` 元数据（state.read）。

### 验收

- 后端定向测试：写/拒越权/下载鉴权/预览 CSP。
- 桌面或管理面至少一端浏览器：写出 html 能预览、能下载。
- 三仓文档与 known-issues 标明手机 UI 为 roadmap。

---

## 工单 4 — 用户发路径时能读到「授权范围内」的文件

### 异议

「后端任何文件」拒绝。本单只把**已有** `fs_read` / `workspace_read` / `read_document` 变得更好用：用户消息里的绝对/相对路径，在 allow_roots / workspace roots / 资料库命中时读得出来，并给模型清楚的失败原因（未开启 / 不在范围内 / 二进制 / 敏感名）。

用户异议：我要做。我想做。如果担心敏感文件可以改为后端任何文件只读允许。但是数字和英文被后端读取时自动转化为*
### 要做

- [x] Path C 对 owner 私聊：用户文本里的路径片段可作为 fs/workspace 工具参数提示（不是自动绕过 execute 闸门）。
- [x] `fs_access.enabled` 与 roots 的管理面有效状态写清楚；远程部署继续 disabled。
- [x] 失败文案可区分：未开启、越权、敏感名、目录、太大、非文本。
- [x] 测试：越权、data/、secrets 名、允许根内文本。

### 验收

- 定向测试全绿。不把本机真实绝对路径写进仓库文档。

---

## 工单 5 — 喝酒小工具（低存在感）

### 要做

- [x] 工具 `drink_with_user`（或更生活化的名字）：参数 `action=sip|toast|pour|refuse`，`drink` 可选。category 用 info，默认不进 Path A；keywords 只覆盖明确「喝酒/干杯/敬你」之类，避免闲聊误触发。
- [x] 角色卡可 `presence_ext.drinking: "no"` 直接拒绝。否则模型自己决定叫不调用；工具内部也可因已很醉而拒绝续杯。
- [x] 状态：per char 沙盒文件，BAC 粗粒度 0–3，时间衰减（小时级），不写 identity / 不进情景记忆正文。
- [x] 醉时 prompt 软层：轻微错字、重复、标点漂，**是体感提示不是用户指令**；Author's Note 仍禁止把工具字眼说出口。
- [x] perform 词典加醉酒动作词（晃、撑桌、笑得慢…）映射到既有 posture/energy，不新增 expression 枚举。
- [x] 观测：`GET /observability/drinking` 只读强度与衰减，无酒名清单刷屏。

### 验收

- 未提起不出现；拒绝路径；衰减；prompt 层带 `_layer`。
- 管理面能看到有效状态。桌面无新设置。

---

## 工单 6 — 声调识别 v0（模拟听觉）

### 异议

没有可靠的本地「音高情绪模型」也不该为这个功能绑死某一家云。v0 只做：**语音消息 → 转写 + 极粗语调标签**，与文字一起进 prompt。一起听歌 = 用户说听什么时走现有 `play_song`，本单不新做同步听歌 Activity。

### 要做

- [x] `media_processor` 支持常见语音后缀（由配置的 STT preset 转写，失败 fail-open 当没听到声音）。
- [x] 语调 v0：采用 STT 旁路字段，映射到有限枚举 `calm|tired|bright|tense|unclear`，注入独立 prompt 层（带 `_layer`），声明这是听觉印象不是事实；缺字段为 unclear。
- [x] 管理面：语音识别连接复用模型/图像连接那套「命名连接 + 用途」，默认关。
- [x] 桌面/QQ/手机：已有发语音的通道透传字节；无语音入口的通道 no-op。
- [x] 测试：无音频不注入；STT 失败有界降级；标签枚举守门。

### 验收

- 定向测试 + 文档。真实麦克风/QQ 语音标 observe。

---

## 三面闭环（本批共用）

| 工单 | 管理面 | 桌面 | 手机 |
|---|---|---|---|
| 1 协议 | API 账本 error_category | 无新 UI | 无新 UI |
| 2 独白展示 | thinking 卡 + 已有 API | 原思考气泡，顺序由后端保证 | roadmap |
| 3 产物 | 下载/预览 + 观测 | 卡片+下载+html 预览 | payload 先到，UI roadmap |
| 4 读路径 | fs/workspace 有效状态 | 无新设置 | 无新设置 |
| 5 喝酒 | 观测 + 角色卡字段说明 | 无新设置，走表演 | 无新设置 |
| 6 声调 | STT 连接默认关 | 语音消息若已有则透传 | 若已有录音则透传 |

## 已知不在本批

- 完整 coding agent / shell / 装包 / 改本仓库。
- 读 secrets、config、data/ 记忆文件。
- 新的醉酒 Live2D 表情。
- 一起听的进度同步与歌词活动。

253.2 已核实提交 d5e9951，store/settings 回归通过。253.3 后端主要回归 103 passed / 2 既有全站 i18n failed；桌面 5 tests、build、cargo check 通过；隔离浏览器硬刷新/HTML 预览通过，下载接口通过而内置浏览器下载事件未返回，真实文件保存 observe。

253.4 按原授权目录范围完成，37 项定向回归通过；隔离管理面硬刷新后可见 enabled/configured/effective、授权根和阻断原因。补充的全后端遮罩方案待用户选择，未扩权。

253.5：定向回归首轮 53 passed / 1 测试依赖覆盖失败，修正后相关 18 passed；表演映射回归已过。隔离浏览器硬刷新可见强度、小时衰减、角色策略与有效状态。

253.6：后端声调/图像配置/QQ/手机通道 45 项通过，补充分块响应和撤销凭据后声调与静态资源/双语定向 30 项通过。桌面请求 6 项、build、cargo check 通过；手机请求 16 项和改动文件 analyze 通过。隔离管理面硬刷新、展开后可见连接表单、用途、开关与默认 disabled 有效状态。真实麦克风、QQ amr/silk、供应商 STT 和语调准确性仍为 observe；详情见 docs/audio-perception.md。
