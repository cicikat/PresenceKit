# 263 — 决策合同、Jev 适配与双后端（小模型 / System One）

日期：2026-09-21。状态：施工中（2026-10-08 用户授权继续）；以下保留原规划，当前验收见文末阶段记录。

目标：把「只做结构化判断」的调用从聊天补全里拆出来，同一份决策合同可走小模型 JSON，也可走 Jev `systemone`。复杂决策默认 **一次 Jev + 必要时一次小模型**；禁止「Jev 判完再让主聊天模型重判」。

---

## 流程选择（先于施工）

用户问：改造后正常复杂一点的决策，是一次 Jev + 一次小模型，还是一次 Jev 后让主模型判。

**采用前者。** 主模型只演戏、说话、多步用工具；闸门类判断不进 `chat`。

| 方案 | 延迟与成本 | 失败语义 | 角色污染 | 结论 |
|---|---|---|---|---|
| Jev 后主模型再判 | 多一次 90s 档聊天；主模型可能推翻校准概率 | 主模型失败会把「已成立的 drop」改成开口 | 角色口吻混进是否打扰、是否调工具 | 不用 |
| 一次 Jev 包办含开放文本 | 快，但 Jev 不生成 query / summary / 工具参数 | 开放字段只能规则猜或丢弃 | 无 | 只适合全封闭任务 |
| **Jev 闸门 + 小模型补开放字段** | Jev 约 70–500ms；小模型只在需要正文/参数时打 | 闸门失败 = 不作为；补字段失败走既有缺参/drop | 主回复仍是角色 | **采用** |

细则：

1. **封闭决策一次 Jev 结束。** `sensor_judge`、`detect_emotion`、`detect_affection`、`scenario_reconcile`、`letter_eval`、invariants 的 same/contradicts/different、`confirm_talk` 的 cancel/send_anyway。不召唤小模型，更不召唤主模型。
2. **混合决策 = Jev 选动作 + 小模型填开放字段。** 例：Path A 探针先 choice 工具（含 `none`），若选中 `web_search` 再让小模型写 `query`；`ime_judge` 先 noul/choice 出 `worth_contact` / `activity` / confidence，再让小模型写 `summary` / `evidence` / `topic`。Jev 说不值得开口或 `none` 时，**跳过小模型**。
3. **主模型只消费已成立的事实。** IME 的 `CHARACTER_POLICY`、sensor 的 narrative、工具结果继续进角色上下文；角色决定「怎么说」，不再决定「该不该作为系统动作」。autonomy 的 `talk_owner` 仍是角色可见出口，但其硬闸（DND、梦境、预算、连续未回）保持程序判断；Jev 不替代 talk_gate。
4. **禁止第三条路：Jev 当 function calling 伪装。** 不把 OpenAI tools schema 自动编成几百个 choice。兼容点是决策 IR，不是一种协议硬转另一种。
5. **普通 provider 是完整后端，不是 Jev 的降级残局。** `json_chat` 必须能单独完成该 category 的全部决策合同（封闭问题 + 如有需要的开放字段）。未配 Jev、Jev 不可用、或该行仍指向 `chat_completions`/`responses`/`anthropic_messages` 时，业务可运行，不出现「没 Jev 就没 sensor/IME/探针」。详见下方「普通 provider 兼容」。

延迟预算（首版，可在 A 里按实测微调，不得叠成无限等待）：

- 纯 Jev：沿用该 category 现有短超时（sensor/ime 10s 请求、总墙钟 20s）；Jev 本身应远低于此。
- Jev + 小模型：两者串行，总墙钟仍受该 category 上限约束；Jev drop/`none` 不得再打小模型。
- 不得把 Jev 成功后再加一次 `chat` 主生成当作「决策确认」。

---

## 当前基线（只读审计）

文本路由只有 `chat_completions` / `responses` / `anthropic_messages`。`provider_kind` 是参数白名单，不推导协议。管理面 `MR_CATEGORIES`：`chat` `intent` `probe` `summary` `detect_emotion` `consolidation` `perform` `monologue` `sensor_judge` `ime_judge` `scenario_reconcile` `event_edge_proposer` `rpg_kp`。`intent` 是兼容回退槽，源码无独立生产调用（仅测试与 `sensor_judge`/`ime_judge`/`scenario_reconcile` 缺配置链）。

主动开口链已经是「sensor/IME 出候选 → autonomy 评估 → `talk_owner`」。Jev 应接在**候选形成之前的闸门**，不要插进角色说话。

### 适合 Jev（首批或明确二批）

| 调用 | 入口 | 现输出 | Jev 形状 | 流程 | 优先级 |
|---|---|---|---|---|---|
| `sensor_judge` | `core/scheduler/sensor_judge.py::judge` | JSON `{score:0-100, reason≤20字}` → `intent_tier`；失败 drop | score + 并行 noul（刚聊过/在专注/深夜/同类刚出现） | **一次 Jev**。`reason` 用规则模板或省略；排障看分数/概率/confidence | P0 |
| `detect_emotion` | `llm_client.detect_emotion` | 八选一标签；失败 `neutral` | choice | **一次 Jev** | P0 |
| `detect_affection` | `llm_client.detect_affection` | yes/no；失败 false | noul，阈值代码化 | **一次 Jev**；仍走 `detect_emotion` category 或拆子用途，A 里定 | P0 |
| `scenario_reconcile` | `core/dream/scenario_reconciler.py` | `stay` / `advance_next` / `uncertain`；保守、发送后、fail-open | choice 三选一 | **一次 Jev**；uncertain 语义保留，不得因 Jev 更「自信」就推进剧本 | P1 |
| `letter_eval` | `core/mail/letter_writer.py::evaluate_letter` | 1–5 分 | score 五档 | **一次 Jev** | P1 |
| invariants `_relation` | `core/dream/invariants.py` | `same` / `contradicts` / `different` | choice | **一次 Jev** | P1 |
| `ime_judge` 决策头 | `core/ime_awareness.py` | JSON：activity、worth_contact、confidence、summary、evidence、uncertainty、topic | choice(activity) + noul(worth_contact) + score(confidence) | **Jev 闸门 + 小模型写文案**。confidence&lt;0.65 或 worth_contact=false → 不打小模型、不入队 | P0 |
| Path A `probe` 封闭子集 | `core/pretool_router.py` `call_category=probe` | native FC 或 `<tool_call>` JSON | choice(工具名含 `none`) + 该工具 enum 字段 | **Jev 选工具；开放 string 再打小模型或走缺参询问**。`get_time` 仍走现有 fast path，不进 Jev | P1 |
| `confirm_talk` | autonomy soft block 后的二选一 | `cancel` / `send_anyway` | choice | **一次 Jev 或继续主模型 function call**。首版可不动；若动，不得让主模型推翻硬闸 | P2 |

### 混合 / 后置，不要首批整槽替换

| 调用 | 原因 | 建议 |
|---|---|---|
| `ime_judge` 全文 | `summary`/`evidence`/`topic`/`uncertainty` 是生成 | 决策头 Jev，文案小模型 |
| Path A 含 `web_search` / 提醒正文 / MCP | 开放参数、嵌套 object、`additionalProperties` | Jev 只选名；参数小模型或 `WAITING_INPUT` |
| Path C relay `{true: 意图}` | 与探针同形，但暴露面含 MCP | 可复用探针 IR；MCP/自由参数仍走小模型 |
| `perform`（llm provider） | 每句封闭 vocab，但变长数组 | 句数少时可一次 Jev 多 question；默认仍规则表。不挡发送 |
| `practice` reviewer | score 0–10 可 Jev；`strengths`/`one_improvement` 要生成 | Jev 打分 + 小模型评语，或整段留 consolidation |
| `interest_seed` | pick 是 choice，rationale 要生成 | 后置 |
| `character_loader` 一致性检查 | ok 是 noul，issue 要生成 | 后置；失败不得改用户可见回复 |

### 不适合 Jev（本单禁止接入）

| 调用 | 原因 |
|---|---|
| `chat` / `monologue` / `letter_write` / practice 作品 / postcard / dream 主回复 | 生成正文 |
| `summary` / `consolidation` / `user_profile.extract_and_update` / invariants `observe` / `stage/char_relations` | 开放摘要、事实抽取、多字段生成 |
| `event_edge_proposer` | 变长候选 + `reason` 长文本；只写未审提案 |
| `rpg_kp` | `KpProposal`：decision 虽封闭，但 roll 必须带五条 outcome_branches、scene_updates、事实投影 | 主模型继续出完整提案 |
| Path C `run_agentic_loop` / autonomy 主循环 | 多步工具 + 角色判断「说什么」 |
| vision / OCR / `phone_control` 视觉步 | 图像协议，Jev 只吃文本 |
| `intent` 空槽 | 不要为 Jev 发明调用点 |

Path A 默认暴露 `info` + `desktop`。其中无参/枚举工具（`get_time`、部分桌宠动作、带 enum 的 mode）可进 Jev choice；`weather.city` 可用所在地规则，不必 Jev 生成城市名。

---

## 拟定行为合同

### 决策 IR

新增与聊天 messages 平行的内部表示（名称在 A 中固定），至少包含：

- `task_id` / `call_category` / `char_id`
- `state`：文本或 JSON，给模型看的事实，不含密钥
- `questions[]`：`choice` | `score` | `noul`，选项/档位封闭
- 可选 `open_fields[]`：需要小模型填写的字符串字段及长度上限
- `failure_policy`：`fail_closed_drop` | `fail_open_skip` | `fail_neutral`，沿用各业务现语义
- `confidence_gate`：低于阈值视为否定（IME 0.65 等）

编译器：

- `systemone`：IR → `POST /v1/systemone` 的 `state` + `questions`；`open_fields` 另一次 `json_chat`（仅闸门通过时）
- `json_chat`：IR → 现有 Chat Completions / Responses / Anthropic messages + schema 校验。一次请求应能同时回答 `questions` 与 `open_fields`（IME 整份 Assessment、探针的工具名+参数），不得因为引入 IR 就把「只用小模型」拆成两次聊天调用

两个编译器产出同一份 `DecisionResult`（选项、分数、概率、confidence、可选 open_fields）。业务代码只读 IR 结果，不读原始 SSE/JSON 散文。`json_chat` 没有原生校准概率时：choice 的 confidence 可缺省为 1.0 或省略，IME 的 0.65 门仍看模型给出的 `confidence` 字段；noul 用 yes/no 或 0/1，由代码映射。禁止 `json_chat` 去调 `/v1/systemone`。

### 普通 provider 兼容（Jev 不是运行时依赖）

本单加 Jev，不把决策从 DeepSeek / OpenAI 兼容 / Anthropic / 本地小模型上拆走。Jev 是可选编译器。

**配置期（零 Jev 必须能用）**

- 现网 `routing_profiles` 不改、没有 `systemone` preset、没有 TypeSafe key，全部决策 category 继续走当前文本协议。A/B/C 落地后默认仍如此。
- 不强制安装 `typesafe-sdk`。`systemone` 用项目既有 httpx；没配该协议的进程不得在 import/启动时连 TypeSafe。
- 决策 IR 对 `json_chat` 是完整合同：封闭 choice/score/noul 用 JSON schema（或现有「只输出一个词 / 只输出 JSON」）表达，不是 Jev 专用方言。
- 按 category 独立选后端。例如 `sensor_judge` 用 Jev、`probe`/`ime_judge` 仍用便宜小模型、`chat` 用主模型。某一行没有 Jev 不等于决策子系统不可用。
- 管理面保存 Jev 连接不得改写未点到的 category。示例 yaml 只给注释样例，不把生产 profile 改成 Jev。

**运行期（配了 Jev 但用不了）**

「用不了」包括：无 key / 401 / waitlist、超时、429、上游 5xx、地域拒绝、熔断打开、协议/模型名非法、选项数超 System One 上限、进程里没有 systemone 客户端。

处理分层，沿用 261，不自动替用户挑模型：

| 情况 | 行为 |
|---|---|
| 该 category 主 preset 本来就是普通文本协议 | 只走 `json_chat`，与有没有 Jev 连接无关 |
| 主 preset 是 `systemone`，且 `fallback_routes[profile][category]` 指向合法文本 preset | 主尝试按现规则用尽后，**最多切一次**到该文本 preset，由 `json_chat` 跑**同一份 IR**（含开放字段，一次调用）。不递归、不切到 `chat` 主模型 |
| 主 preset 是 `systemone`，未配兜底 | 走该业务**原失败语义**（sensor drop、emotion `neutral`、affection false、IME `failed` 不入队、reconcile `uncertain`）。不暗中改用 `default_preset` 或 `chat` |
| `json_chat` 主用、Jev 仅在兜底 | 允许，但默认不这样配；文本失败且未配兜底时同样走原失败语义 |
| Jev 返回成功但 schema 对不上（缺题、未知 choice） | 计格式失败。禁止换模型「再编一个 JSON」来绕过；未配兜底则原失败语义 |
| 混合任务已 Jev 通过、文案小模型失败 | 不改闸门结果；IME 不入队，探针走缺参/失败。不把闸门重打给主模型 |
| Jev 选项超上限（探针工具太多） | **本轮不走 Jev**，直接 `json_chat` 完整探针；这是能力回落，不是 failover 计数里的失败 |

默认 `fallback_routes` 仍为空（261：不自动替用户选模型）。管理面在决策行主 preset 为 `systemone` 且兜底为空时给出可关闭提示：Jev 不可用将按该行失败语义处理；可一键选已有文本 preset 作兜底，不新建、不改 key。

**混合任务在纯小模型下的形状**

Jev 路径才是「闸门一次 + 开放字段一次」。纯 `json_chat` 必须保持**一次调用出完整结果**，与现网 IME / 探针一致：

- `ime_judge`：一份 JSON（activity、worth_contact、confidence、summary、evidence、uncertainty、topic）
- `probe`：现有 native FC 或 `<tool_call>`，工具名和参数一起出
- `sensor_judge` / emotion：一次 JSON 或单标签，不拆第二次

禁止「没 Jev 就先打一次小模型做 choice、再打一次小模型写文案」。

**测试与验收约束**

- 默认 CI / 定向回归**不准**依赖 TypeSafe 网络或 `systemone` preset；fixture 只用普通 provider。
- 至少覆盖：无 Jev 配置跑通 sensor / emotion / IME /（C 落地后）探针；Jev 主用 + 文本兜底成功；Jev 主用无兜底走原失败；非法把 Jev 绑到 `chat` 被拒。
- 热重载删掉 Jev preset 或改回文本协议后，下一次调用走 `json_chat`，不残留 systemone 客户端。

### 协议与路由

- 新 `api_protocol`: `systemone`。官方默认地址 `https://api.typesafe.ai/v1/systemone`，认证 Bearer。不按模型名猜测协议。
- `provider_kind` 可新增 `typesafe`（参数白名单空或仅 timeout）；**禁止**把 Jev preset 标成 `chat_completions` 去调 `/chat/completions`。也禁止普通 OpenAI 兼容 preset 填 Jev 模型名却走 Chat Completions 冒充决策后端。
- Jev preset **只能**绑定决策类 category 白名单（首版：`sensor_judge` `ime_judge` `detect_emotion` `scenario_reconcile` `letter_eval`；probe 在 C 准入后加入）。绑到 `chat`/`monologue`/`summary`/`consolidation`/`rpg_kp` 保存时 422。
- 未声明 `systemone` 的 profile 行为与现在完全一致。决策入口即使已改走 IR，`json_chat` 编译结果对业务的失败码、drop、neutral、IME 不入队必须与现网同语义。
- 失败兜底见上表；格式失败不换模型「再编一个 JSON」。账本 `caller` 保持原 category；记 protocol、主/备、是否用了 open_fields 二次调用（仅 systemone 路径）、是否因选项超限直接走了 json_chat。不写 state 正文、不写 API key。

### IME / sensor / 探针的具体语义

**sensor_judge**

- 输入仍是 event narrative + presence 上下文。
- Jev：开口价值 score + 扣分向 noul。代码合成 `intent_tier`，阈值表保持 0–40 drop / 41–55 weak / … 或改为概率门，A 中冻结并补回归。
- 不再要求模型写中文 `reason`；观测可存档分数与 top noul。
- 失败仍 drop，不主动开口。

**ime_judge**

- Jev 只输出 activity / worth_contact / confidence（及必要 noul：是否有新编辑、是否只是本系统聊天普通输入——后者优先继续用现有规则过滤，不交给模型）。
- 仅当 worth_contact 且 confidence≥0.65 且规则层 evidence 闸仍需要文案时，才打小模型生成 summary/evidence/topic/uncertainty。
- 小模型不得把 worth_contact 改回 true。Jev false 则整次结束。
- 入队、cooldown、TTL、开关复核保持 `ime_awareness.tick` 现合同。

**Path A probe**

- 暴露面仍由 `tool_exposure.path_a` 决定，不因 Jev 扩大。
- Jev choice 的选项 = 当前可见工具名 ∪ `{none}`，超过实现上限（建议 32，A 冻结）则本轮不走 Jev，回落小模型探针。
- 选中工具后：无参直接 `execute_structured`；仅 enum 由 Jev 第二组 questions 填；开放 string 走小模型或 `WAITING_INPUT`。
- 高危工具确认闸、origin=`user_live`、must-call 标记不变。
- Path C 激活时仍跳过普通探针；本单不让 Jev 替代 tool loop。

### 观测与管理面

- 模型路由：preset 可选 `systemone`；category 行若选了不兼容 preset，保存失败并说明。
- 只读 effective：该 category 实际协议、是否启用 open_fields 二次调用、最近一次决策（无正文）的 drop/select/confidence。
- 复用 `/observability/api-calls` 与 failover 统计；Jev 与小模型分协议计数。
- 桌面/手机无新设置。决策不进客户端。

---

## A — P0：决策 IR、systemone 出口、sensor / emotion 闭环

- [x] A1 冻结 IR schema、`DecisionResult`、category 白名单、失败语义表、超时/熔断/fallback 规则，以及「普通 provider 兼容」表。列出每个接入点：现入口、是否走 `llm_client.chat`、现校验、现失败返回值。禁止只接一个出口却宣称全覆盖。`intent` 空槽不发明调用。写明：`json_chat` 一次调用覆盖 questions+open_fields；`systemone` 才允许闸门后再打文本模型。
- [x] A2 `api_protocol=systemone`：独立 HTTP（非 OpenAI SDK、非必装 typesafe-sdk），代理/User-Agent/账本与现文本调用同纪律；未知协议 fail-fast。preset CRUD 拒绝 Jev 绑聊天类 category。无 systemone preset 时启动/热重载不连 TypeSafe。热重载清 client 缓存。
- [x] A3 JSON 编译器：把 `sensor_judge` 与 `detect_emotion`/`detect_affection` 改为「先 IR、再按 preset 协议编译」。**默认回归只跑 json_chat。** 小模型路径输出必须通过同一校验器，行为与现网兼容（含 emotion 宽松匹配与降级 neutral）。禁止未配 Jev 时走 systemone 或拆成两次聊天。
- [x] A4 Jev 编译器接入上述三处；sensor 去掉强制中文 reason；affection 阈值代码化。对照测试用固定 fixture，不打真实 TypeSafe。另测：systemone 主失败 + 文本 fallback 成功；systemone 主失败且无兜底 → 原失败语义。可选隔离 live probe 记入验收说明，不作为通过条件。
- [x] A5 管理面：协议选择、不兼容映射 422、effective 展示（含当前协议与兜底文本 preset）。决策行选 Jev 且兜底为空时提示「Jev 不可用将按失败语义处理」，可选手动指定已有文本 preset。静态 `?v=` 与 fragment 版本按 AGENTS 规则更新。浏览器验收：纯文本 profile 保存/刷新；Jev+空兜底提示；Jev+文本兜底保存。
- [x] A6 同步 `docs/model-presets.md`、`docs/feature-control-surface.md`，写清双后端与 fallback 默认关。相关测试与换行检查后独立提交。

## B — P0/P1：IME 决策头拆分（Jev + 小模型文案）

- [x] B1 把 `Assessment` 拆成决策字段与文案字段；规则过滤（测试包、本系统聊天无删除、TTL、cooldown）仍在模型前。
- [x] B2 双后端：`systemone` 否决则不打文案模型；通过但文案失败 → 记 failed，不入队。`json_chat` **一次**产出完整 Assessment，不得拆成两次小模型。Jev 熔断且配了文本兜底时，兜底走完整单次 JSON，不是「Jev 残缺结果 + 再补文案」。
- [x] B3 禁止文案模型改写 worth_contact。回归：低置信、无 evidence、stale revision、开关关闭。
- [x] B4 观测区分 `ime_judge` 闸门调用与文案调用。文档 `ime-ingest.md` 写清两段调用。独立提交。

## C — P1：Path A 封闭探针（可选 Jev）+ 开放参数小模型

- [x] C1 从当前 path_a schema 生成 choice 选项；超上限或主 preset 为普通文本协议时走现探针（一次出工具名+参数）。fast path `get_time` 不变。无 Jev 时 C 不得改变 Path A 暴露面或成功率语义。
- [x] C2 开放参数策略：规则（如 weather 所在地）→ 缺参询问 → 小模型补参。MCP / `additionalProperties` 工具本单不走 Jev。
- [x] C3 Path C 仍跳过普通探针。relay 是否复用 IR 在 C 内写明；默认本单不改 MCP relay。
- [x] C4 探针解析失败语义保持 fail-closed（不把散文当聊天）。独立提交。

## D — P1：其余一次 Jev 可替换点

- [ ] D1 `scenario_reconcile`：三选一；uncertain/stale/CAS 合同不变；不得更激进推进。
- [ ] D2 `letter_eval`、invariants `_relation`。`observe` 生成 items 仍 summary 小模型。
- [ ] D3 明确不接入：`rpg_kp`、`event_edge_proposer`、consolidation、profile 提取、vision。写入 known-issues 或本单「禁止」节即可，不留假 TODO。
- [ ] D4 若做 `perform`：仅 llm provider 且句数≤上限；失败 fail-open。默认不强制。独立提交。

## E — P2：对照评测与默认开关

- [ ] E1 用脱敏/合成的 sensor、IME、emotion 样本对比现用便宜小模型 vs Jev：校准、中文、延迟、失败率。无样本则保持默认关，配置可开。
- [ ] E2 默认：新协议可配，**不自动改用户 routing_profiles**。示例 yaml 可给注释样例，不写真实 key。
- [ ] E3 评测结论写回本单或 `docs/model-presets.md`；不把 live 一次成功当成生产默认。

---

## 施工顺序与闭环

建议 A → B → C → D → E。先有 IR 和两个编译器，再拆 IME，再探针，其余封闭点最后。每段相关测试通过、差异/LF 检查后独立 commit。

影响面：后端路由、决策入口、管理面模型路由。桌面/手机继续消费既有回复、主动消息和工具结果，不新增设置或协议字段。autonomy / talk_owner / Dream Guard 不因本单改口。

浏览器：A5 必须验收管理面路由页。IME/sensor 无独立 UI 新控件则不扩到其他页。阻断时写明「浏览器实测未完成」。

删除候选（本次不授权）：`intent` 空槽是否从管理面拿掉；sensor JSON reason 字段；detect_emotion 散文宽松匹配（Jev 后可能只留标签校验）。确认无消费者再另单连同测试删除。

---

## 验收时必须能回答的问题

1. 关掉 Jev / 不配 systemone，现网普通 provider 路径是否语义兼容（失败码、drop、neutral、IME 不入队、探针一次出参）？必须是；CI 不得依赖 TypeSafe。
2. 主 preset 是 Jev、未配兜底、Jev 401/超时/熔断时，是否暗中改用 `chat` 或 `default_preset`？必须不能；只能原失败语义。
3. 主 preset 是 Jev、兜底是普通文本 preset 时，是否用**同一份 IR**走 `json_chat` 且混合任务仍一次出全字段？必须是。
4. `chat` 能否被指到 Jev？必须不能。
5. IME/探针在 Jev 说「不作为」之后，是否还打了小模型或主模型？必须没有。纯小模型路径是否被拆成两次聊天？必须没有。
6. 主模型是否仍可能在 autonomy 里决定开口？可以，那是角色说话，不是重判 sensor/IME 闸门。
7. `rpg_kp` 是否仍走聊天模型出完整提案？必须是。

## 2026-10-08 A 阶段合同与验证

- [x] A1–A4：DecisionRequest / Question / DecisionResult；原生 HTTP 与文本双编译出口，sensor/emotion/affection 接入。文本编译保留原业务 prompt，一次覆盖原字段；原生投影由代码产生，不将聊天散文伪装原生答案。
- [x] Minecraft 加入 A：六选一 choice，confidence >=0.65、3s 总预算、零 SDK retry，失败本地规则；无路由自动变更。
- [x] A5 管理面协议与兼容映射、effective protocol、文本兜底、无兜底提示；近期决策只读观测。
- [x] A5 隔离管理面浏览器验收：纯文本、Jev 无兜底、Jev 显式文本兜底均保存并重新打开核对；A6 相关回归和独立提交。

首批白名单为 sensor_judge / detect_emotion / minecraft_reaction；affection 复用 detect_emotion。B/C/D 接入后才开放对应 category。score 原生取值 0..N-1 映射回既有业务范围。失败语义：sensor drop，emotion neutral，affection checked None/public false，Minecraft 原失败回退。保留原熔断/transport-only fallback、总墙钟与零原生 retry，不在失败后擅自调用 chat/default。原生坏类型/概率/闭集、越界、低 confidence 拒绝。

真实原生合成 choice probe：保存 jev 的 api_protocol 修为 systemone（不改地址/密钥/生产路由），/v1/systemone 返回有效结果，437ms。静态/fixture、真实连通与业务校准分别记录。

## B 阶段验收（2026-10-08）

IME Jev 闸门与开放文案已拆分；显式文本兜底同时承担通过后的文案，管理面清楚标明，无配置失败不入队。普通文本一次完整 Assessment。原生否决/低置信零文案调用，文案越权字段拒绝，传输失败完整单次文本回落，文案失败不入队。相关 131 项回归通过；隔离路由编辑器已核对缺文案提示与 Jev+文本保存重开。生产路由未切换，无真机新验收。

## C 阶段验收（2026-10-08）

本轮可见 schema 封闭选择、none 零文本、enum 填参、开放参数显式文本/缺参询问已接入。能力不兼容只走显式完整文本，未配则失败不执行；不扩大工具暴露面，不改 Path C 与 MCP relay。81 项相关回归通过。工具 reader scope 的旧夹具改为 owner-turn，以满足276默认禁止访客回复；未开放生产访客。原生出口补齐 no_outbound 守卫。
