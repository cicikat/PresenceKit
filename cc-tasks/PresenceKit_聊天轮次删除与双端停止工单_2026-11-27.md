# 工单：Reality 指定轮删除、独立回收库与双端停止生成

- 日期：2026-11-27
- 状态：调查完成 / 方案待实施；本单不代表功能已实现或验收通过。
- 范围：PresenceKit 后端、PresenceKit-desktop、PresenceKit-mobile。
- 本次交付：仅工单。不得以本单为由现在开始功能施工、生产数据操作、部署或客户端正式发布。
- 施工原则：复查当时工作区；当前仓库/分支施工；避开其他任务未提交改动。下列子单逐张验证、独立 commit、push、合并后再进入下一张，失败和未验收如实记录。

## 1. 用户目标与非目标

1. 桌面气泡右键、手机气泡长按出现“删除本轮”。后端删除准确的一轮，不仅隐藏某个前端气泡；两个前端实时收敛，刷新、重连、重启后不复活。
2. 两端都有“停止生成”，真正中断后端该请求的生成链，防止模型无限输出；不等同于断开 HTTP、隐藏 loading 或取消尚未发出的本机队列。
3. 所有删除（含手机发起）先进入后端**独立 SQLite 回收库**。只有桌面提供“删除记录 / 查看 / 恢复”界面；手机不做回收站、删除记录列表或恢复按钮，但必须同步恢复结果。
4. 删除针对 history、上下文和能精确证明血缘的向量/派生记录。无法定位的旧记忆明确报告，不以“删除成功”冒充“所有长期影响均已抹除”。
5. 本期只做 Reality owner 聊天；Dream、Stage、QQ 平台撤回、Agent 工作会话及已发生的外部工具动作不纳入删除/回滚承诺。共享 Reality 历史必须过滤，但不向 QQ 宣称撤回。
6. 停止不重做手机 token streaming；不新增自动重试、自动重新生成、生产部署、APK/桌面发布、全库备份、永久清空回收站。

## 2. 已查现状（以工作区代码为依据，施工前重核）

| 现状 | 拥有者 / 影响 |
|---|---|
| 桌面 HTTP 请求具备 request_id 与查询入口 | `admin/routers/chat.py`：`/desktop/chat`、`/desktop/chat/requests/{request_id}`；不能把 request_id、msg_id、turn_id 混为同一个 ID |
| 请求执行被 shield；客户端断线不代表生成停止 | `core/session_scope.py::execute_request()` 使用 `asyncio.shield(task)`；现在不能靠 AbortController 实现服务端取消 |
| 同一 owner 完整轮串行 | `core/owner_chat/service.py` 持 `core/conversation_gate.py::conversation_lock(uid)`；停止入口不能等持有此锁的生成结束才能执行 |
| 桌面有条件走 token 流，手机不是 token 流 | `core/owner_chat/generation.py` 的 `_use_stream` 限 desktop 且有 UI 连接；mobile 走非流式生成，再推最终结果 |
| 手机实时通道只推 final | `core/mobile_realtime.py` 明确 final-only；手机的 receive/排队取消不等于取消远端模型 |
| 最终提交与展示有独立阶段 | `core/owner_chat/delivery.py` 调 `core/turn_sink.py::record_assistant_turn()` 后推 canonical；停止必须定义与提交阶段的竞态 |
| 历史不只有短期 JSON | `core/memory/short_term.py` 含 `_turn_id`；`admin/routers/chat_log.py` 解析日期日志，再用事件账本和 mobile finals 补展示；只改 event_store 不会可靠清掉原始日志 fallback |
| 手机另有最终展示投影 | `core/mobile_realtime.py` 在事件库中保存 `mobile_finals`，带独立 history_index，墓碑触发器会删投影；恢复不能指望 trigger 自动补回 |
| 现成墓碑会抹正文 | `core/memory/event_store.py::tombstone_event()` 清正文和媒体引用；删除前必须持久快照，不能先墓碑再取快照 |
| 已有证据撤回级联，但并非全量事务 | `core/memory/invalidation.py` 处理部分 mid_term / episodic / storyline / dossier，缺血缘项只报 manual_review；现有 revert 不自动重建向量。不能直接当成本单的原子删除/完整恢复 |
| 桌面历史已有 turnId 关联 | `src/windows/chat/components/ChatPanel.tsx` 与 history/reconciliation 模块；一轮可能渲染多个气泡，不能按数组下标删 |
| 手机已有长按菜单与未派发取消 | `lib/widgets/chat_widgets.dart`、`lib/widgets/chat_scene.dart`、`lib/controllers/chat_controller.dart`；保留原“取消发送”语义，另加“删除本轮” |
| 两个常用客户端 profile 都有 chat/memory.read | `admin/scopes.py`；“只做桌面恢复”必须服务端鉴权，不能仅凭 User-Agent、客户端自报 channel 或隐藏按钮 |

### 2.1 施工前补齐的调用链清单（写在 A 单交付记录）

- 从实际发送/上传/activate 入口追踪 ingress → request → canonical turn → 用户/assistant event → short_term → event_log → 派生/向量 → 两端 HTTP/WS/poll/cache/outbox。
- 列出 `_turn_id` 的分配时机、失败/停止前是否已有用户落盘、所有 send 前写入及 send 后 slow/deferred 队列；逐一指定删除过滤和写入闸门。
- 对日期日志、分段消息、assistant-only 主动消息、附件、工具活动、提示引用、跨日边界列出可确定关联与不能关联的情况。
- 确认 mobile WS 订阅/重连、桌面事件分发是否丢弃未知类型，新增 mutation 控制帧不能破坏 final-only 正文约束。
- 复查三仓当时 SHA、脏文件与相关 AGENTS；不得把其他任务改动一并交付。

## 3. 冻结产品语义

### 3.1 什么叫“本轮”

- 以服务端 `(owner_id, char_id, realm=reality, turn_id)` 为唯一边界；一个用户输入及对应全部 assistant 分段/附件展示属于一轮。assistant-only 主动消息单独一轮。
- 用户气泡或任一 assistant 分段触发同一操作；确认框预览“用户消息 + 对应回复”，列出将删除的气泡数量，不删相邻轮。
- 只接受服务端授予、可查证的 canonical 身份；禁止按正文相似度、时间接近、数组位置、客户端临时 ID 推断。
- 旧记录若能由精确日志块/事件映射建立稳定 ID，可经确定性迁移支持；无法唯一关联则禁用菜单并提示“此旧记录缺少可靠轮次标识，暂不能按轮删除”。不得为了覆盖率猜配。
- 正在生成的轮先显示“停止生成”，停止终态后再删除；已完成并落盘的轮按下述删除流程执行。尚未发出的本机消息继续用“取消发送”，不伪造后端删除记录。

### 3.2 停止语义与保存策略

- 停止目标为服务端已接纳的精确 request，冻结 owner/character/session 来源；也可从同角色“当前生成”列表获取 execution_id 后由另一端停止。不能拿“当前活跃角色”去猜目标。
- 发送立即创建可取消请求；尚未取得生成锁的服务端排队请求也可取消。停止早于 admission 的竞态必须有取消意图/握手：同 ID 迟到 admission 不得再执行；同时校验 scope、时效并限制该索引容量。
- 服务端状态：`queued → running → stop_requested → stopped`；另有 `committing → completed`、`failed`、重启后的 `interrupted`。`stop_requested` 只是受理，不是已停止。
- stop 与提交竞争必须有一个线性化点：stop 先赢则禁止走完整 turn_sink/学习/继续 fanout；提交先赢则返回 `already_completed`，不能把已完成结果改写成“已停止”。
- 停止之后不再启动新 LLM 请求、fallback、工具轮、模型重试或后处理学习。关闭 provider stream/response、取消并等待本轮子任务；保留已发生工具动作的事实，明确“停止不会撤销已执行动作”。独立持久任务不能假装已取消。
- **停止轮保留用户输入和已安全展示的 assistant 前缀，标记“已停止，内容不完整”**，只作为可恢复的聊天展示记录，不进入正常记忆提取、向量固化和后续模型上下文；无安全前缀则仅显示状态。两端与重启后展示一致，仍可删除本轮。
- 停止轮必须有持久 canonical turn_id，不能只存在组件 state；中途 tool/protocol 帧、未闭合控制标记不得进入保存前缀。不调用 LLM 补齐半句或标签。
- 对手机非流式路径，若停止前无安全可见前缀，不伪造输出；保留输入 + 已停止。无需为了停止改造手机 token streaming。
- 客户端先进入“正在停止…”，拿到终态或查询证据后退出 busy；超时显示“停止状态未确认 / 重查”，不自动重发。重复点击/两端同时停止幂等。
- 能保证的是本系统不再消费/继续请求并关闭连接；不能保证远端 provider 已立即停算或不再计费。上游不响应关闭时显式记录，不谎报。
- 复用现有最大输出/请求预算；核验长时间无 delta、无限 delta、工具循环都有有界退出。若新增运营开关，必须同单接管理面热更新；不能只加 YAML。

### 3.3 删除范围与限制

| 对象 | 动作 |
|---|---|
| 两端聊天展示、短期上下文、日期日志、事件账本、mobile_finals | 快照后撤回对应轮；所有读取入口过滤，日志精确摘块或可重建投影；不能只靠墓碑而让纯文本 fallback 继续返回 |
| 本轮 request result cache、final/replay、手机未 ACK 队列、桌面降级缓存、本机持久化缓存 | 纳入过滤/失效；重放前校验轮状态；删除的正文不得因迟到 HTTP/WS/poll 重新出现 |
| 以本轮为来源的 event / 向量 / 派生记忆 | 仅按精确 ID/lineage 撤回并清检索可见性；向量 rebuild、fallback、置顶、重启重建均尊重失效状态 |
| 多轮共享派生记忆 | 保留其他有效来源；仅撤掉本轮贡献或标记需重算，不整条误删其他轮证据 |
| 无 lineage 的旧 identity/facts/其他聚合 | 不做关键词模糊删除；返回 manual_review 清单和数量。检查实际 prompt 注入面，明确未自动撤回范围 |
| 上传源文件、共享媒体、外部动作、运行诊断日志 | 默认不删共享源文件，不撤销工具，不篡改诊断事实；删除聊天可见引用即可。通用日志不得新增记录正文 |

- UI 固定提示：“从聊天与可定位的相关记忆中移除；删除记录保留在桌面回收站，可恢复。无法追溯的旧记忆需人工复核。”返回结果分别统计已删除、已失效、待修复、需人工复核，不能只显示一个布尔成功。
- 删除不是隐私彻底擦除：回收库有正文快照。该限制在确认框与桌面回收站写清。
- 普通生成正在使用目标角色上下文时，不能删除后任由旧快照继续生成：检测受影响的在途请求（含其他后续轮），要求先停止/重开；本期允许返回可操作的 `conversation_busy`，前端提示停止并重试，不能暗中取消无关请求。
- 后续 slow/deferred writer 在**执行及提交前**校验来源 tombstone/generation；已在执行的 writer 与撤回使用共同边界，禁止“检查后又落盘”的 TOCTOU。

## 4. 协议与鉴权草案（A 单冻结 OpenAPI/三仓类型后才能施工）

路径为拟议，不代表当前已存在。优先共用服务层与 owner Reality Session 授权，不在 Router 复制实现。

| 入口 | 语义 |
|---|---|
| `GET /conversation/executions` | 当前 scope 的 queued/running/stop_requested，供另一端发现可停止目标；有界列表，无 prompt/密钥 |
| `GET /conversation/requests/{request_id}` | 两端统一请求状态；补齐手机安全 scoped 查询，不冒用桌面 request 编码规则 |
| `POST /conversation/requests/{request_id}/stop` | 幂等停止；返回 execution_id、状态、终态原因、canonical turn_id（若已产生） |
| `POST /conversation/turns/{turn_id}/delete` | operation_id 幂等；精确整轮撤回，返回 deletion_id、revision、各投影结果与 manual_review |
| `GET /conversation/mutations?after=...` | scope 内持久变更补账；分页、有序 revision，含 deleted/restored/execution_state 与最少关联 ID，不含回收正文 |
| `GET /conversation/deletions`、`GET /conversation/deletions/{deletion_id}` | 桌面专属列表/详情；列表默认元数据，正文按详情读取，游标分页 |
| `POST /conversation/deletions/{deletion_id}/restore` | 桌面恢复；operation_id + expected revision/CAS，返回恢复/冲突/待重建项 |

- 所有入口 Bearer + 有效 `X-Presence-Session`，由 grant 冻结 owner/character/reality；跨 owner/char/realm、过期 session、错误 origin 全部 fail-closed。换角色后不能拿新 session 操作旧 scope。
- stop/delete 与普通历史按 chat grant 授权；回收库详情和 restore 采用独立 `chat.recovery` capability（仅 desktop profile 与 admin，mobile 默认不授予）。同步 scope/profile 文档和既有 token 展开/热加载行为，不静默要求用户换成 admin token。
- “仅桌面”指产品入口和服务端 capability，不是无法伪造的设备证明；持有同一高权限 token 的手工 HTTP 请求无法靠界面区分。不得依赖客户端自报 platform 鉴权。
- 未知/越权目标统一不泄漏存在性；合法但已完成返回明确终态；revision 冲突 409；存储/归档不可用 503；部分失败返回 operation 状态，绝不能假成功。
- stop/delete/restore 均用独立 operation_id 幂等，与原消息 request_id 分开。相同 ID 不同载荷拒绝；持久跨重启，定义有界索引与可追踪保留策略。
- 请求状态也需避免重启后永久 running；重启清理为 interrupted，不自动重新生成。被删除请求的查询不得再带原正文。

## 5. 独立回收库与可恢复一致性

### 5.1 存储合同

- 后端新建独立 SQLite 文件（不是向 mobile_finals/event_store 多塞一张回收表），走 sandbox/data_paths 与 scope resolver；具体路径在 A 单冻结，不硬编码 data 路径。
- 最少包含：schema_version、deletion_id、operation_id、owner/char/realm/turn、original order/time、原始消息及显示快照、事件/媒体引用、受影响派生对象与版本/hash、undo patch、删除者 credential 指纹、operation phase、错误摘要、revision。
- mutations 与操作状态可同库，回收正文与用于两端补账的元数据查询严格分层。不得让回收内容进入普通 history/prompt/vector/导出接口。
- 回收记录默认保留，不做隐式 TTL/自动清理；列表有界分页。库满、磁盘不足、快照失败则拒绝新的破坏性操作，不允许先删后补。空间治理/永久清除另开单。
- 纳入 protection inventory、既有备份/校验与角色删除/导出隐私规则；不在本单执行生产备份或整目录复制。数据库权限与其他私有状态一致，不把快照正文写普通日志/观测摘要。

### 5.2 删除流程：跨文件不假装单个 SQL 事务

1. 校验 grant、稳定 ID、expected revision、在途读写依赖；建立锁序：执行登记/取消锁不等待 conversation lock，变更写入统一 conversation gate → uid/memory lock → DB lock；不得在等待停止时持有生成所需锁。
2. 读取精确目标及 undo，写独立库 `prepared` 并提交，快照失败不动原数据。
3. 发布持久 tombstone / mutation fence，切断检索、history 与重放；落状态 `applying`。并发 admission/提交/慢队列必须看同一 fence，不能只靠 UI。
4. 分存储应用幂等撤回，记录每步 checkpoint；缓存失效，更新 authoritative revision；`completed` 后才报完整成功、广播两端。
5. 中途失败为 `repair_pending`，保留回收快照与读取 fence，前端展示“已隐藏，关联记录处理中”，不把半完成当完整成功。重启自动安全续作/隔离，并有只读查询与明确重试入口。
6. 断线广播失败不回滚数据；mutation log 支持补账。必须覆盖 prepared/applying/completed 每个崩溃点。

### 5.3 恢复流程

- 桌面确认后按 deletion_id 恢复原 turn_id、原时间/顺序、分段/样式/引用；不是新发一条消息，不调用模型、不重新执行工具、不重发系统通知/推送。
- 使用 patch/CAS，仅恢复本次撤回且之后未被独立修改的字段；禁止整文件旧快照覆盖后续轮或其他人工编辑。
- 保留之后独立的墓碑/删除与多次删除历史；多来源派生记录只撤销本操作的失效贡献。冲突逐项列出，默认不覆盖，允许停在 `restore_conflict`。
- 明确补建 mobile_finals/history 映射和检索投影；embedding 可异步重建，但在完成前返回 `index_pending`，不能宣称检索已恢复。原始向量 payload 若不适合长期存储，保留可验证重建输入/版本，不默认触发未经授权的计费 API。
- 恢复同步两端，对手机仅体现普通聊天重新出现，不提供回收库详情/恢复操作界面。原先已停止的记录恢复后仍是 stopped，不进入学习或上下文。

## 6. 实时同步、刷新与防复活

- 桌面 WS 与手机 realtime 增加独立控制消息 `conversation_mutation` / `execution_state`，不把控制帧当聊天正文，不向手机流入原始 token。按 owner/char/session 订阅隔离。
- 每 scope 单调 revision，消息包含 mutation_id、operation_id、turn_id、关联 request/msg ID、state；允许重复和乱序，客户端按版本归并。
- mutations cursor **独立于 mobile poll/ACK/history_index**，不得拿删除事件推进消息 ACK。清理已删除队列项不能跳过未删除项或错误推进共享前缀。
- 首次进入、回前台、WS 重连、刷新、切回角色时对账；先确认同步水位，再把历史页与本地缓存合并。cursor 过旧返回 reset_required，全量重取当前可见历史与活动请求元数据，不返 archived 正文。
- 所有 HTTP/WS/final/poll/history page 到达前统一查本地删除集合/版本；迟到的旧页、旧 HTTP 成功、stream_end、retry、旧 outbox reconcile 都不能复活气泡。restored 的更高版本才解除 fence。
- 历史响应带 revision/snapshot 水位，分页跨修改失效时重取；不同历史入口都遵守同一 tombstone，不能日期页过滤而手机 finals 仍可见。
- 删除/恢复成功定向更新受影响轮和日期，不清空所有聊天、不重置未发草稿/附件。保留滚动锚点；删除末尾调整未读计数、空日期/分隔线，恢复按原时序插回，不跳到底。
- 跨日加载、搜索、引用预览、样式分段、工具活动关联与本地数据库均需检查；引用失效显示“原消息已删除”，不复制回收正文。
- 离线不乐观宣称删除成功；保留待重试状态/operation_id。换后端、角色或 session 时隔离 pending mutation，迟到回包不能污染新会话。
- 自动播放/TTS 若已排队且目标轮被删除/停止，应取消未执行部分；已播出的声音不可撤销，不因恢复自动重播。

## 7. 双端交互

### 桌面

- 右键有可靠 turn_id 的用户/assistant 气泡 →“删除本轮…”；确认展示范围与回收说明。操作中禁重复、失败留原气泡并提供重试；repair_pending 单独提示。
- 正在生成时输入区有明显“停止生成”按钮（含无 token、工具中、远端其他客户端发起）；不是禁用输入的 loading 图标。多个 queued/running 时明确目标，不“一键误杀所有”。
- 新增“已删除聊天”入口，按角色过滤、分页、删除时间/原时间/状态、详情与恢复确认；恢复后保留审计，不做自动物理清库。
- 保留右键复制、选中、回复与键盘可达性；更新中英文提示与无障碍标签。

### 手机

- 现有长按菜单加“删除本轮…”与确认；保留“取消发送”（仅 queued）、复制/回复等。手机删除也进入服务端回收库。
- 输入区新增停止按钮，区分 queued 取消 / 服务端停止 / 停止确认中；页面回前台后查询后端真实状态，不能以本机 timeout 代表 stopped。
- 不做删除记录列表、回收详情、恢复按钮；但接收桌面恢复与删除同步。

## 8. 分单实施顺序

### A — 身份、状态与协议底座

- 完成 §2.1 清单；冻结上述 ID、停止保存策略、scope、API/schema、mutation revision、锁序与归档清单。
- 建立稳定 request→turn 关联、跨端 scoped 请求状态、停止/提交仲裁及 capability 合同；新 API 尚不接 UI 时标记不可用，不暴露空实现按钮。
- 测试：跨 scope 拒绝、重复 admission、重启 interrupted、预接纳取消意图、commit-vs-stop 受控竞态。

### B — 后端真正停止

- 接通流式/非流式/tool loop/prepare 子任务及 provider close，持久 stopped 展示，不学习；手机/桌面请求都可控。
- 校验 shield 内真正执行 task 被停止，取消不落成普通错误后重试；工具副作用与 outcome_unknown 如实记录。
- 测试：无限 delta、无首 token、provider hang、工具等待、服务端排队、双端同时停止、锁释放后下一轮可发送、停止不触发 fallback。

### C — 独立回收库与整轮删除/恢复

- 独立库 + saga/fence + projection 过滤 + lineage 级联 + CAS restore；对慢队列、request replay、mobile queue 全链路防复活。
- 补只读状态/repair 入口、backup inventory，恢复不重放动作。
- 测试：精确整轮、不误删同正文/邻轮/其他 scope、跨日分段、共享 lineage、无 lineage manual_review、archive fail、断点重启、冲突恢复、删除两次、手机 finals 重建。

### D — 双端接入、桌面回收站与实时刷新

- 实现 §6/§7，更新 wire 类型与本地持久化 schema/迁移；两端共享停止语义，不照搬旧本机取消。
- 测试：右键/长按真实菜单与请求；乱序/重复 mutation、迟到回复、旧历史页、离线重连、角色/后端切换、草稿滚动保持；手机无恢复界面且 recovery API 被拒。
- 桌面构建/类型/交互测试，手机 analyze/相关 test；浏览器与 Android 设备未实测时显式留空，不能以 mock 代替。

### E — 三仓联调与交付记录

- 三仓协议总账标 current/open/observe；记录精确 SHA、接口 schema、实际命令/结果与兼容组合。
- 桌面原生右键、手机真机长按、双在线实时删除/恢复、另一端停止、弱网重连/重启补账列为人工验收；当前没有完成这些验收。
- 文档/读写数据路径/锁序/敏感信息/换行差异检查后按既有准则交付，不擅自部署或发正式包。

## 9. 测试与交付门槛

- 先复用 turn_sink、session_scope、chat_log、mobile realtime/queue、event tombstone/invalidation、short_term、工具循环与两端 reconciliation/outbox 测试；用例路径施工时从实际目录挑选，不捏造 PASS。
- 所有测试用合成 owner/character、独立 sandbox 与假 provider；不得用生产 history、真实回收库或真实模型调用试删。
- 网络只模拟外部边界，服务 API 走挂载的真实 Router；UI 走真实控件；断言实际文件/DB/可见状态与检索结果，不只断言 mock 被调用。
- 故障注入须证明：删除后重放不复活；停止不再输出/落学习；archive 失败不破坏原数据；restore 不覆盖后续编辑。先见业务失败再修复。
- 并发使用事件/屏障与有界等待，覆盖每个提交/关闭点，不靠 sleep 赌时序；跨进程重启测试检查真实持久化恢复。
- 默认定向测试；影响面证明需要全量或用户明确要求时才 `pytest -n auto`。按 dev-environment 选支持的 Python。纯本工单交付只做结构/路径/敏感信息/差异检查，不跑功能测试。
- 修改 Admin 静态运行内容时按缓存规则统一更新版本并浏览器验收；桌面/手机资源和本地缓存 schema 按各仓要求处理。
- 交付更新 `docs/three-repo-interface-catalog.md`、`docs/channels.md`、`docs/memory.md`、`docs/security.md` 及实际受影响的客户端协议/交互文档；只有设置控制面实际变化才更新其文档。
- CI/设备未通过不阻止按当前用户规则合并，但不得写验收通过；生产部署与正式发布仍需另行授权。

## 10. 风险、后续删除 brief 候选

- 最大风险不是菜单，而是旧日志 fallback、慢队列回写、shield 仍在运行、跨端迟到消息与恢复覆盖新数据。任何一项漏接都不能标“删除/停止链路完成”。
- 删除 brief 候选：统一请求状态后淘汰只面向桌面的重复状态适配；稳定身份迁移后淘汰基于文本 hash 猜配的新消息分支；统一 projection 过滤后淘汰多处分散 tombstone 判断。仅列候选，本次未授权清理，不执行；未来删除连同守卫、测试、文档一起处理。

## 11. 本轮工单交付记录

- 已调查三仓相关拥有者与现有取消/历史机制；仅新增本 Markdown，不修改运行代码/测试/客户端，不操作用户聊天数据。
- 未进行功能测试、浏览器/设备验收、迁移、备份、部署或发布；上面所有接口和子单均为拟议/待实施。


## 12. 施工结果（2026-11-27；续第 11 节的仅工单交付）

A–D 代码与 E 自动化/文档交付已完成并分仓推到 main。精确代码组合、命令与结果见 [实施交付记录](../docs/conversation-control-delivery.md#e三仓实现交付与剩余验收2026-11-27)。第 11 节是施工前历史记录，不再代表当前实现状态。

- [x] A：稳定身份、独立控制库与 scoped 状态协议。
- [x] B：真正取消 executor/provider stream、停止展示记录与终态仲裁。
- [x] C：独立删除归档、精确证据清理、跨进程读写栅栏与可重试恢复。
- [x] D：双端删除/停止、桌面恢复、实时提示及刷新/旧日重读；失败不伪装成功。
- [x] E：定向验证、构建、隔离交互、三仓文档与独立提交交付。
- [ ] E 人工验收：真实 provider、原生桌面、实体手机、双在线/后台弱网组合，仍 open；未授权生产部署或正式发布。

后端全量 CI 仍有非本单角色 fixture 前置条件 14 项失败，未降低断言。验收状态为 **代码已交付 / 实体端到端待验收**，不是全链路验收通过。
