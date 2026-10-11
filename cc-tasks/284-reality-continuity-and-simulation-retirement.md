# 284 真实感知、活动聊天连续性与模拟机制退役

状态：已获授权，按 P1→P7 分单施工；运行验收逐项记录。
日期：2026-10-11。

## 1. 用户决策与验收边界

- 保留真实设备感知，把它描述为角色收到的观测数据，不配套编造角色动作、内心或用户情绪。
- 保留情绪表达与短时余韵，删除随机、钟点、工具等强制赋值；降低系统情绪提示的优先级，不删除真实事件中的情感内容。
- 删除纯伪造的虚拟活动；无活动允许为空。不以新随机机制替代旧随机机制。
- 删除整套现行成长子系统：兴趣自动播种、练习调度、等级、评分升级、技巧成长及 MCP 等级解锁。不重建成长替代品。
- 保留现有角色自主能力：self 文件 CRUD、外部文件阅读、浏览器、真实任务，以及授权范围内的图片/音频等 API。无需安排角色固定下棋或读书。
- Reality 活动对话进入同一 owner/char 的聊天与连续性记忆；活动状态、原始记录和概要均可追溯查询，不等关闭会话才留下两句印象。
- 上下文有界，不等于删除原记录。长活动片段按长文档/媒体思路投影概要与引用，原文和结构化状态保留。
- Dream 保持原有隔离，不借本单修改梦境回流政策。
- 本工单不承诺模型永不遗忘；验收承诺是证据持久保存、可检索可回读、接续时可提供当前状态与相关记录。

## 2. 源码审计：三个 activity 不可混淆

| 名称 | 当前来源与消费 | 处理 |
|---|---|---|
| 用户设备活动感知 | `admin/routers/sensor.py` 实时上报 → `core/memory/realtime_state.py` → `prompt_builder._format_realtime_awareness`；另有 `_load_activity_snapshot` 读取旧快照 | 保留有证据的新鲜观测，核对设备、principal、权限和 TTL；不得叙述成角色动作 |
| 角色 ambient 虚拟活动 | `core/activity_manager.py` 时段池/随机记忆/近20h练习 → `get_current`/`get_prompt_fragment` → prompt、HTTP、sensor_events、Stage/private_exchange | 删除随机状态生成及所有旁路 |
| 真实活动会话 | `core/activity/` 阅读、棋局、Minecraft 等 → session/store/transcript → 活动局部聊天和部分结束摘要 | 保留执行与状态，将 Reality 对话和可追溯记录接入统一聊天连续性 |

已确认的具体问题：

1. `activity_manager.get_current()` 读取缺失/过期状态时会随机生成并落盘；仅停止 scheduler 的 activity_switch 不够。
2. 随机持续15–45分钟，`thinking_about` 从记忆或静态池抽取；真实记忆不能证明角色当前正在想它。
3. `_pick_recent_growth_activity` 把20h内既往练习概率投影为“在练”；过去发生不等于当前进行。
4. `core/scheduler/loop.py` 另加随机“此刻…”旁白，独立于 activity_manager，也要删除。
5. `admin/routers/activity.py` 的 text 可来自真实会话，但 started_at/next_switch_at 来自随机状态，来源不一致；真实活动分支未统一使用 registry。
6. `prompt_builder._load_activity_snapshot` 使用300秒 received_at TTL；本轮全仓代码搜索未定位其现行生产写入者。不得宣称该旧文件链当前实时有效；P1继续核实，确为废链时删除读路径和专用守卫，不删除真实 sensor 链。
7. 设备类别当前部分文案直接说“用户现在在…”。类别推断应改为“设备上报/当前前台类别…”，不推断实际任务、注意力或内心。
8. `core/mood_helpers.py` 有深夜 sleepy 和工具 thinking 强制写入；`mood_state` 跨轮固定枚举、两轮切换和漂移，缺少来源回合与过期裁决；记忆召回还可 nudge mood。
9. `core/activity/transcript.py` 存局部 JSONL；`activity_summary.py` 只取最近40条对话，关闭后生成1–2句感受，明确不写棋谱/步骤/页码，且写死 gentle。此合同不满足本单连续性目标。
10. `core/growth/` 仍存在，不能因历史 character_growth 已删除就视为成长系统已退役。练习生成有真实文本产物，但评分/等级不证明能力成长。

消费端已定位：桌面 `src/shared/state/useBackendStatePolling.ts` / `store.ts` 轮询 mood/activity；Room/Pet/Live2D/CharacterPerformer 使用 mood、perform 和局部播放状态；手机 `profile_status_controller.dart` / `backend_client.dart` 读取活动及情绪。前端呼吸、眨眼和微动作保留，不产生后端事实或模型调用。

## 3. 目标合同

### 3.1 真实感知

观测包含来源设备、发生/接收时间、有效期限、事实字段及必要的推断标记。沿现有授权与敏感过滤，不扩大采集范围。

只投影角色当前可感知的数据。例如“设备上报前台类别为浏览器，最近有输入”，不能自动改写为“用户在查攻略”，也不能写成“角色看向屏幕”。角色回应由本轮上下文决定。

TTL过期即不作为当前观测；沉默、断连、无数据不补模拟信息。原始高频遥测不自动全部进入聊天或长期记忆；只有真实互动中的引用及有明确价值的事件才走既有记忆准入。视频观察同理，不作为用户内心事实。

### 3.2 持续活动、即时动作、情绪余韵、执行回执

| 概念 | 权威来源 | 规则 |
|---|---|---|
| 持续活动 | 已启动的真实 session/task | planned/starting/running/paused/completed/failed/cancelled；无活动为空；时间、状态、展示来自同一来源 |
| 即时动作 | 本轮显式表演意图、真实交互/播放事件 | 沿 say/perform；有界有效期，结束释放；不持久化为活动事实 |
| 情绪余韵 | 角色当轮明确表达；推断只能作低置信可选提示 | 来源 turn、时间、期限、版本；新回合优先，可改变或撤回；最长30分钟，不因读取续期 |
| 执行回执 | 工具/API/服务实际结果 | 角色说想做不等于已做；失败不得记为完成 |

持续活动复用现有会话与 Runtime，不新增大型 Agent 调度器。最小关联字段为 uid/char/realm、activity_id、session_id或task_id、revision、状态、开始/更新时间、证据引用；模型不得指定或伪造身份。

注意力转向聊天不等于任务暂停。棋局可以等待下一步，后台任务可以继续；只有真实暂停成功才记 paused。恢复先读状态和相关聊天，不强求角色凭空记住每一步。并发任务保留各自状态，不以一条 current 文案覆盖全部。

### 3.3 同一聊天与可追溯记忆

统一聊天意味着统一身份、历史、回合写入、检索与用户可见接续；共享历史与记忆不要求活动消息显示在主聊天窗口，棋步、工具回执、文件内容保留结构化记录并通过引用参与召回；不要求把所有活动生成塞进同一个函数或取消专用棋局执行器。

- 活动中的真实 user/assistant 对话走现有 owner conversation gate、speaker-aware history 和 assistant turn sink 或等价的唯一收口；不得直接绕过 assistant 脱敏。
- 使用真实 turn_id/request_id/msg_id/event_id/session_id/revision 关联。HTTP回包、WS、局部活动窗口和主聊天共享身份去重，避免同一回复记两次、播放两次。
- 活动窗口可保留为棋盘/阅读器等操作界面，但不是另一条孤立人格与记忆。主聊天也可询问和接续活动。
- 落子、页码、工具回执作为真实结构化事件/附件保存，不伪装成用户发言，也不每步都调用模型评论。
- 恢复活动先提供当前权威棋局/页码/任务状态，再提供相关近期对话与概要；历史叙述不能覆盖实际棋盘。
- 原始 transcript、结构化事件、文件或产物有稳定引用。上下文超预算时使用分段概要，附覆盖区间、修订与回读入口；摘要不是原文替代品。
- 提供受 principal 限制的分页查询：按活动/会话查历史与状态，按关键词/时间找活动记录，按引用回读片段。优先复用现有聊天搜索、长文档及文件读取能力，缺口才增最小工具；新 schema 通过 portable 检查。
- 长期记忆存有证据的事件概要及 source_ref，不仅存关系温度；不强行产生情绪标签。活动来源是 Reality 的事实来源，不能一概 coplay_echo 跳过所有固化；同时不把网页/书籍/游戏NPC文本转成用户事实。
- 区分用户发言、角色表达、工具事实、外部内容。统一聊天不改变外部内容的信任等级，也不自动写用户隐性状态。
- 先保存证据再生成概要，send之后后台摘要；失败标 pending/partial 可重试，期间仍可查原文。禁止摘要失败造成原文丢弃，禁止晚到摘要覆盖新修订。
- 源记录按既有用户删除/撤回与保留策略管理，不因上下文裁剪删除；索引/概要不能复活已撤回内容，不承诺无限容量。

### 3.4 情绪：保留表达，撤掉强制来源

取消随机标签、钟点sleepy、工具thinking、召回nudge及确定性“此刻…”旁白。sleepy/thinking仍可作为当轮明确表达/表现能力，现实时间仍可作背景。

情绪提示改为低优先级可裁剪余韵，包含来源与新鲜度；无信号不注入，不等价于强制neutral。检测失败不能覆盖已有信号。无需同标签连续两轮，新旧提交按源回合顺序裁决，不仅按模型完成时间。不要为了每轮表情额外调用模型。

普通聊天与视频复用表达语义，按实际播放应用perform；审计词典中否定、引用、用户情绪词、标点推断，避免自动贴角色标签。frontend无信号可用自然基线，该视觉基线不回写心理事实。

consumer清单至少包括花园、scheduler/presence_nag/状态机、episodic召回、user_hidden_state现实信号、贴纸、avatar指令、硬件灯效、桌面主题/Room/Pet/Live2D、手机状态。真实过去情感可留存；当前短时表达不直接成为稳定人格或用户状态。

### 3.5 整套成长退役

删除 `core/growth/`、interest_seed/practice/practice_help、slow_queue handler注册、prompt growth_self、growth activity概率、MCP proficiency过滤/执行闸门/升级回执，以及对应配置、管理路由/页面、数据注册、文档及专用测试。实施前全仓列引用，确认名字相同但不属于该子系统的概念不误删。

MCP去掉等级后仍受服务器启用、工具暴露、scope、origin、风险确认及owner授权限制，不能变成全部工具自动放行。图片/音频能力使用真实API可用性及授权，不自动新增API集成。

现有作品不物理删除、不自动导入人格；可通过正常文件/资料查询访问。旧兴趣、分数、等级与心得停止live消费，不包装成经验证能力。历史私有数据清理另列候选，不在本单自动执行。用户侧修改write_update产物的编辑UX为独立事项，不混入本单。

## 4. 分单施工与验收

按实际完成证据勾选；工单确认不等于运行验收。每单相关检查通过后独立commit，再施工下一单。

### P1 感知与来源合同校正（先做）

- [x] 列出真实sensor、旧activity_snapshot、虚拟activity的写读者和principal/TTL；核实旧快照现行写入者，缺失则退役废链。
- [x] 真实感知投影改为来源明确的观测描述；不制造角色动作或用户内心。
- [ ] 验证新鲜/过期/断连、设备切换、跨char/owner隔离及敏感过滤；复用相关sensor/prompt回归。
- [x] 同步受影响prompt/接口文档；若tag规则改变运行层激活eval。

### P2 删除虚拟活动（删除brief）

- [x] 删除activity_manager随机生成、thinking池/抽记忆、20h练习当前化、scheduler随机旁白和切换任务。
- [x] 清理prompt、sensor_events、Stage/private_exchange、HTTP和配置依赖；真实活动接口不得读取随机元数据。
- [x] 空状态正常展示；GET和prompt读取不得生成状态；重复读取不变更记录（后端回归通过，真实消费端展示待P7）。
- [x] 专用守卫/测试/文档随功能删除，保留真实感知及真实活动守卫；不清理用户authored池文件。

### P3 整套成长删除（删除brief）

- [x] 按3.5全量引用清单删除子系统及控制面；不保留假等级兼容运行链。
- [x] 定义队列旧practice任务退役：不得再次执行模型调用，明确终止/跳过原因；不回滚真实已完成作品。
- [x] MCP授权/暴露与schema回归，确认无等级依赖也无权限扩大。
- [x] 旧产物可查询，分数等级不再prompt注入；删除growth专项测试与文档条目，保留工具/权限独立守卫。

### P4 真实活动生命周期与查询底座

- [ ] 建立现有session/task的统一只读投影，区分持续活动与执行回执；不加随机idle调度。
- [ ] 当前棋局/阅读/任务状态和时间同源，暂停恢复基于实际执行，断连超时/重启不伪续。
- [ ] 稳定来源引用与有界查询，记忆概要能回到完整记录；增量事件幂等、revision防乱序。
- [ ] 范围测试：活动并发、打断不丢状态、跨char/owner、TTL、查询权限、portable schema；持久状态同单只读观测。

### P5 Reality活动统一聊天与完整记录接续

- [ ] 先迁移一个真实棋局垂直链：用户发言→专用棋局上下文→同一回合写入/脱敏→HTTP/WS去重→主聊天与局部窗口→记忆检索。
- [ ] 对话实时进入统一历史，不等close；结构化棋步独立归档并有聊天引用；无人评论也不丢事实。
- [ ] 接入分段概要、覆盖范围与原文分页查询，send后摘要、失败重试、撤回及修订守卫。
- [ ] 再接阅读、其余棋局、Minecraft及自主任务的真实结果引用；仅处理各活动实际可用的能力，不以注册声明代替运行验收。
- [ ] 替换close两句感受摘要及coplay blanket隔离，避免重复episodic写入；来源隔离与provenance同步。
- [ ] 验证跨窗口发言串行/关联，恢复会话看状态与旧对话、万字尾段回读、短期裁剪后查询、异常关闭、不重复发送/计数/记忆。
- [ ] 历史局部transcript按来源ID建立可查询索引；如需导入主聊天先dry-run及离线备份，不推测缺失回合/棋步，不双计历史指标。

### P6 情绪表达与短时余韵（原C1/C2修订）

- [ ] 删除强制赋值与召回nudge；保留显式表达能力和历史事件情感。
- [ ] 源turn/version/TTL/撤回及新回合优先；无信号与neutral分离；旧固定mood不自动续成新余韵。
- [ ] 低优先级可裁剪prompt、逐句perform；审计关键词误触发及慢后处理乱序；Dream隔离。
- [ ] 逐consumer解除持久枚举假设，禁止用户画面直接驱动角色花槽或用户内心事实。

### P7 控制面与跨端验收（原C3/C4扩展）

- [ ] 只读观测显示来源、状态、更新时间、有效期、实际注入状态、概要覆盖/索引/失败原因；权限按敏感度，默认不返回原文。
- [ ] 必要运营开关同单管理面热更新；删除机制不保留重新开启模拟的开关。
- [ ] 同步feature-control-surface、three-repo-interface-catalog和受影响桌面/手机文档；静态版本更新、硬刷新浏览器验收。
- [ ] 实测：普通聊天、活动窗口、桌面3D/Pet/Live2D及视频表达、手机聊天/状态、真棋局中断后恢复、长记录回读、真实自主文件/浏览器任务完成后查询。
- [ ] 实测没有新的活动也可自然交流；不凭空读书/练琴/困倦；无高频微动作模型调用。

建议顺序：P1→P2→P3→P4→P5→P6→P7。P5按活动独立小commit，不将整个跨端迁移挤成一个大提交。各单控制面若有实际行为改动，按仓库规则当单完成，不拖到P7；P7做最终联验。

## 5. 施工前必读与交付规则

按实际单元读取 `docs/memory.md`、`docs/prompt-layers.md`、`docs/tools.md`、`docs/scheduler.md`、`docs/channels.md`、`docs/security_model.md`、`docs/runtime-lifecycle.md`、`docs/agent-runtime-architecture.md`、`docs/character-files-and-agent-autonomy.md`、对应活动及长文档专题；跨端读取各仓AGENTS与接口文档。

只运行相关既有回归，缺口补最小必要测试；文档单检查结构、相对链接、diff和LF，不运行全量。源码施工遵守sandbox路径、provenance、tool schema、身份与脱敏入口；保护其他未提交文件。各单独立提交，检查换行与敏感值。真实聊天/视频/设备未验收必须保留not-run，不以fixture替代。

## 6. 本轮完成证据

- [x] 核对上述源码入口，区分设备感知、随机ambient、真实活动会话。
- [x] 将用户修订写入目标合同与分阶段工单。
- [ ] P1–P7全部施工及运行验收：P1代码校正已提交，设备身份/真实设备验收仍待完成；P2已提交；P3仅引用审计，P3–P7施工未完成。

删除候选已列入P1/P2/P3及P5旧摘要退役；历史私有文件、用户资产、无关模块与旧记录物理清理未授权，不执行。

### P1 施工证据

旧快照只有 data_paths/data_registry/迁移引用，没有生产写入者；删除 prompt reader 和专用守卫，不删除历史数据。sensor 仍由 sensor.write 接收，state.read 查询；纯内存整帧替换，重启清零。实时 prompt 限配置的 scheduler owner，各角色共享用户设备事实。设备类别、输入和 scheduler 事件改为观测措辞，拒绝未来接收时间。协议目前没有独立设备身份，设备切换隔离及真实设备实测仍待完成；不能据 fixture 宣称这两项已验收。

### P2 施工证据

删除 activity_manager、切换维护任务、scheduler 随机此刻旁白、prompt/Stage/private_exchange/sensor 随机活动消费及专项测试。真实 /activity/current 使用 registry 找真实会话并读取同源时间，无会话为空。143 项相关回归通过；P1 扩展回归 102 通过、2 项因在线 life_records 数据库变化导致生产目录快照不同而失败，保留记录，未停服务。跨端静态检查确认桌面归一化支持 nullable 时间与空 text；真实端运行 not-run。

提交：P1 `3fbb30d`，P2 `04e896a`。P2后续范围检查103项通过；补充只读过期记录守卫后75项通过。未重启生产服务，提交源码不等于生产运行已生效。P3审计已定位 core/growth、practice/interest_seed调度、MCP三处暴露/执行等级闸门、成长管理页和专项测试；尚未删除这些实现。P5的结构化引用、统一对话收口与界面独立合同已明确，尚未施工。

### P3 施工证据

删除 core/growth 源码、interest_seed/practice 调度及 proposer、slow_queue 注册、growth_self prompt/tag、MCP等级过滤/执行、配置、成长router/页面及专项测试。历史作品和路径accessor保留，不物理删除。旧队列项在 handler 之前明确跳过，runtime-signals 可查询。178项相关MCP/授权/portable/队列/调度回归通过。扩展扫描另有既有失配：管理页全量i18n守卫仍期待旧缓存标记/旧页面文字，架构守卫缺既有memory_consolidation与多项新增工具，旧probe守卫未接受info分类展开；未顺手修这些无关契约。浏览器已硬刷新并核对导航和观测总览无成长入口；feature-center动态加载实测未完成。
