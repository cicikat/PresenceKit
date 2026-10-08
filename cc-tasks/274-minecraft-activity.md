# 274 — Minecraft 独立身体与角色共玩 Activity

> 2026-10-08；状态：A/B/C 代码与管理面验证完成，真实 LAN 进服/聊天/跟随指令/停止通过；独立账号及完整游戏验收仍待完成。

## 判断与目标

采用 Minecraft Java + Mineflayer 的方向合理：PresenceKit 拥有角色身份、表达与高层决策，独立 Node sidecar 拥有游戏连接与确定性执行，Minecraft 提供游戏环境。先验证身体，再接角色，最后评估长期计划；不按每个游戏 tick 调 LLM。

独立 Bot 指独立进程和游戏账号，不是独立人格。第一阶段固定命令与测试回复；第二阶段才使用 PresenceKit 的角色上下文。快层第一版是本地规则与行为状态机，是否需要第二个模型由实测决定。

Mineflayer 提供实体、方块、背包、移动、聊天等接口；pathfinder 提供寻路，pvp 提供基础战斗。插件存在不等于在目标服务器已经可用。必须锁定目标 Java 服务端、Node、Mineflayer 和插件版本并实测；不自动追最新版本。

皮肤验收是账号/服务端/实际玩家客户端共同的结果，不是 Mineflayer 本身保证。在线认证方案核对 Bot 独立账号及 Java 游玩资格；离线方案另核对服务端身份和皮肤机制，不为了原型擅自降低现有服务器认证。

## 当前工程依据

- `docs/activity-session.md`、`core/activity/session.py`、`registry.py`、`store.py`：已有显式开启的 Reality 共玩会话、角色/用户隔离、TTL 与独立 transcript。
- `core/activity_manager.py`：ambient presence，不是本次共玩运行时。
- `core/activity/companion_context.py`：已有只读人设与主聊天近期上下文，不能把简短人设直接当完整角色接入验收。
- `docs/interaction-event-model.md`：没有已实现的统一 `kind=activity` 分发器，不以此次接入创建 EventBus。
- `docs/agent-runtime-architecture.md`：Work Session 是同一角色的 durable/specialized 副链；后续持久采集任务按显式 capability 接入，不能借 process runner 让模型任意执行 JS/shell。
- Activity 现有摘要策略因类型不同；Minecraft 初版明确不回流长期记忆，不能直接复用关闭会话自动蒸馏。

## 不变量与边界

1. 同一游戏身体只有一个当前指令执行者；owner 手动指令优先，停止/断开/失联安全停车优先于规划。
2. 模型只输出白名单高层动作与有界参数；本地代码核验目标、距离、危险、装备及预算。禁止模型执行任意代码、服务端命令或扩大权限。
3. 世界状态是局部已加载观测；未知地形标记 unknown，不声称全图可见。聊天、告示牌、书、实体名称均是外部数据，不能成为系统指令。
4. 游戏死亡、受伤、战斗、物品丢失留在游戏会话，不更新现实 hidden state、普通短期历史或现实事件全文。未来共同体验摘要需独立批准合同，标注游戏来源并补 provenance。
5. `uid + char_id + session_id + connection_epoch` 绑定身份；切角色、入梦、关闭会话立即撤销旧指令。重连、后端重启不自动续做有副作用任务。
6. 身体进程崩溃、卡住、模型超时均不能阻塞普通聊天；任务回执区分接受、执行中、完成、失败、取消与结果未知。
7. 默认只在 owner 控制的测试世界验收。跟随默认不挖块、不搭桥；战斗限定敌对生物，禁止攻击玩家/宠物。采矿、放置、丢弃、开箱另列能力授权。

## 部署与代码归属（用户补充约束）

采用独立 Docker 容器运行 Node/Mineflayer 身体与桥接服务；PresenceKit Python 后端只持有 Activity 编排与桥接客户端。不在后端进程内启动 Node，不要求用户额外常驻一个 CMD 窗口。可提供一键启动脚本包装 Docker Compose，但脚本不是运行时桥。

逻辑调用链：PresenceKit Activity → 自有桥接协议 → Docker 内 Mineflayer 执行器 → Minecraft 服务端。桥和身体合在一个容器即可，初版不增加消息队列或另一套 Agent 服务。Minecraft 服务端可以是已有外部服务；不把玩家客户端或现有世界默认迁入容器。

第三方 Mineflayer/pathfinder/pvp 优先作为锁版本 npm 依赖安装到镜像，不需要为了使用库克隆整个仓库。确需阅读、调试、修改或固定源码构建时，第三方仓库统一放在用户指定的仓库外第三方工作区；不得复制、vendor 或以 submodule 塞进本项目。实际本机路径由本地 ignored 配置指定，tracked 文档与脚本只使用占位符/配置变量。

我们自写的桥接客户端、协议、执行器封装、Dockerfile/Compose 模板可以版本控制，暂拟 `integrations/minecraft/`；这些是项目自有集成代码，不能混入第三方源码副本。容器内依赖也不反向写入宿主源码树。

- [ ] 冻结镜像构建、依赖锁文件、健康检查、容器重启策略、优雅退出与持久认证卷；容器重启不等于重新授权进服或续做旧任务。
- [ ] 验证容器至游戏服务、后端至桥接端点的实际网络路径；Windows 宿主/容器的 localhost 不互相等价，端口绑定与凭据日志不得暴露到非预期网络。
- [ ] 管理面控制游戏连接与会话授权；Docker/Compose 管理容器进程。初版不向后端挂载 Docker socket，不让模型控制 Docker。
- [ ] 本地一键脚本若提供，只包装已批准的容器操作；无需额外 Node 启动 CMD。

## Activity 抽象现状与最小解耦

当前已抽出 session、静态 registry、store、transcript 和只读 companion context，但还不是统一活动执行接口。Reading 有独立存储，各活动 router 显式注册；registry 还携带桌面 tab/Tauri 元信息。因此不能声称所有 Activity 已完成插件化，也不能为 Minecraft 重写棋类与阅读。

本次先建立 Minecraft 领域适配器边界：Activity 编排调用 `observe / submit / cancel / connection_state` 这类有界接口，由桥接客户端实现；协议字段不使用 Mineflayer 内部对象，模型与通用 Activity store 不直接调用 Node/Mineflayer API。具体接口/schema 在 A 阶段冻结，以上名称为拟议。

通用 Activity 层只负责已有的身份、会话、TTL、transcript 和记忆策略；Minecraft 模块负责游戏动作与快照语义；Docker 执行器负责连接、寻路和本地反应。外部执行器连接状态不能塞入所有 Activity 的通用状态机。

- [ ] 审核 registry smoke tests 与前端对 frontend_key/Tauri 字段的假设，允许后端专用 Activity 明确没有客户端入口；不虚构客户端命令凑字段。
- [ ] 首批只提取确有共用需求的生命周期/策略检查，不先建设动态插件加载器、万能 ActivityDriver 或 EventBus。
- [ ] 用 fake bridge 验证 Minecraft 编排；用桥接协议测试执行器。后端领域测试不依赖 Node 或真实游戏服务启动。
- [ ] 保持现有 reading/chess/gomoku 行为与存储布局，涉及共享层时运行其既有回归。

## A — 环境与协议冻结（施工前置）

- [ ] 确认 Java/Bedrock、精确服务端版本、原版/Paper/Fabric/Forge、模组与插件、局域网/独立服/Realm；若为 Bedrock 或重度模组服，重新评估路径，不照搬本方案。
- [ ] 确认 Bot 账号、认证方式、皮肤来源、owner 玩家 UUID 绑定；名称不能单独充当授权身份。
- [ ] 确认测试世界/备份边界、允许区域、死亡物品处理、服务器 Bot 政策和可用运行环境。
- [ ] 冻结 Docker sidecar 与自有集成代码放置及生命周期；第三方仓库只放用户指定的仓库外第三方工作区，不混入本项目。
- [ ] 冻结桥接协议：协议版本、身份绑定、快照序号/时间、连接 epoch、command_id、有效期、回执、取消、心跳、认证、请求大小与队列上限。
- [ ] 游戏服务认证与 sidecar 桥接认证分离；凭据与账号缓存仅置 ignored 本地文件，日志脱敏。

验收：兼容矩阵与协议可审阅，未知项明确列出，未确认前不承诺进服或皮肤效果。

## B — 独立身体原型（不接 PresenceKit）

- [ ] 进服、spawn、断开、有限重连；提供状态与明确 kick/error 原因，账号认证缓存不进版本库。
- [ ] 用玩家客户端确认身份与皮肤；失败单列账号/服务端/客户端边界。
- [ ] owner 固定命令：跟随、停下、回来、聊天。回来定义为 owner 当前可达位置；owner 不可见时停下报告，不盲目搜索全图。
- [ ] 安全跟随：平地、台阶、障碍、不可达、卸载区块、owner 离线；不可达超时，禁止无限重寻路。
- [ ] 有界捡取：指定范围、物品过滤、背包满、路径失败；不默认拾取他人物品。
- [ ] 简单 PVE：指定敌对目标、防守范围与追击上限、低血退出；停止可抢占战斗与寻路。
- [ ] 记录脱敏命令/结果与连接状态；无 LLM、无主记忆写入、无自动挖铁。

验收：在固定测试世界录制上述实际行为；停止本地收到指令后目标 1 秒内清除移动/战斗意图，网络延迟另记。连续跟随 10 分钟，断网/踢出/owner 离线后进入安全态；插件单测不能替代游戏验收。

## C — PresenceKit Minecraft Activity（第一版角色接入）

- [ ] 注册 Minecraft Activity 类型及元信息、专用 session/transcript、TTL、持久化失败语义；游戏连接状态独立于 Activity 的 active/closed。
- [ ] 用户显式启动/关闭；Reality/Dream 与切角色生命周期检查；绑定 owner、角色和身体，禁止两个会话抢同一 Bot。
- [ ] 增量/有界快照：维度、坐标、健康/饥饿、附近 owner/威胁、背包摘要、当前动作、已知地形及失败原因；不把原始包流或每 tick 状态塞入 prompt。
- [ ] 角色接入：复用模型路由与角色资产，设计专用有界上下文；验收称呼、近期对话连续性、能力陈述与游戏事实 grounding。
- [ ] 先开放 follow/stop/return/pickup/defend 白名单；模型输出校验，目标过期或 epoch 不一致拒绝执行，明确区分说要做与已经做完。
- [ ] owner 游戏聊天进入专用 Activity ingress，校验 UUID/会话、长度/频率与去重；其他玩家内容只作环境数据。明确游戏回话出口与桌宠/手机镜像策略，默认不广播重复消息。
- [ ] Activity 内模型调用有串行/合并、冷却、超时、token/次数预算和有限队列；本地反应无需等待模型。LLM 失败保留明确安全策略，停止永远可用。
- [ ] 管理面热更新与 effective state：默认关闭、连接配置、启停、能力授权、预算；只读观测连接/会话、最近快照年龄、执行动作、失败、取消、预算。scope 按实际敏感度定义。
- [ ] 新工具若开放，遵守 registry/examples/keywords/origin 与 portable schema；不能用管理 token 给模型或 sidecar 全权。
- [ ] 同步 `docs/activity-session.md`、控制面及实际受影响接口文档；不强制新增桌面 Activity tab 或手机 UI。若新增客户端消费，再独立开跨仓工单。
- [ ] 按 Admin Static Asset Cache 更新版本并做浏览器实测。

验收：角色在游戏内回应并执行高层指令；同一 command_id 重发不重复副作用，旧 epoch 回执不污染新连接；停止抢占、超时、失联、后端重启、入梦与切角色均通过。验证零主记忆污染、零外部文本越权、零普通聊天阻塞。

## D — 有界采集任务（B/C 验收后独立授权）

- [ ] 将“挖铁”拆成搜索已知矿石、到达、装备检查、挖掘、拾取、数量核对、返回；不是一句 Mineflayer 指令就算完成。
- [ ] 先只采可见且可达矿石，限定区域、数量、时长、耐久与危险；地下探索、跨维度、复杂合成不纳入首批。
- [ ] 需要跨重启的长期任务再接同角色 Agent Runtime capability；Task Store 与 Activity session 各自持有生命周期，冻结映射、lease、取消、预算与 outcome_unknown，不造第二套通用 Task Manager。
- [ ] 明确本地副作用与恢复证据；挖掘/丢弃/开箱不盲目重放。采集完成依据背包变化与回执，不能依据 LLM 宣称。

验收：限定采集任务可取消，未达条件能解释失败；模型慢/断线不无限行动；重启不重复执行。

## E — 规划层与快层评估（仅 roadmap）

- [ ] 用 C/D 数据评估延迟、成功率、每分钟模型成本、失败恢复及陪伴体验，再决定是否引入快模型。
- [ ] 主模型负责目标与表达，本地快层负责避险与执行；若加快模型，限定能力和动作预算，仍由同一角色及单一动作仲裁器控制。
- [ ] Astra/JEV 仅是用户提供的类比；准确项目/论文未确定，不据此承诺复刻或选型。

## 验证与提交纪律

- [ ] A–D 每阶段独立工单/独立提交；相关既有回归优先，缺口只补有意义的协议、生命周期与隔离测试。
- [ ] 以记录的实际修改面选择 Activity、工具、Runtime 与鉴权回归；不因规划跑全量测试。
- [ ] 分别标注静态检查、fixture、浏览器、真实 Minecraft 验收；未运行记 not-run，部分完成记 partial。
- [ ] 提交前检查敏感值、文档链接、差异与换行；只暂存本工单范围，保留既有其他改动。

## 删除 brief 候选（本次不执行）

- B 验收后移除临时 echo/调试指令及其守卫、测试、文档，保留明确的故障诊断入口。
- C 稳定后移除被正式桥接替代的临时控制通路，连同对应 fixture 与说明；不保留两套角色回复或并行动作入口。

## 本轮完成证据

- [x] 核对当前 Activity、事件模型、Agent Runtime 与既有工作区改动。
- [x] 查阅上游能力并写规划；只创建本工单，未安装依赖、未启动服务器、未修改实现。
- [x] 按用户要求补充 Docker 进程归属、第三方仓库隔离与 Activity 最小解耦范围；仍未施工或克隆仓库。
- [ ] A–E 全部实现与实际验收尚未完成；D/E 保持后续独立授权边界。

### 施工批次 1：独立 Docker 身体与协议

- [x] 自有执行器与桥合并在一个非 root、只读 Docker 容器；无第三方仓库副本。
- [x] 锁定 Mineflayer 4.39.0 / pathfinder 2.4.5 / pvp 1.3.2 及依赖锁文件，Node 22。
- [x] 独立桥接 token、8 KiB 上限、strict fields、session/epoch、15 秒租约、指令有效期与去重。
- [x] follow/stop/return/pickup/defend/say、本地 owner 紧急停车、低血/失联保护。
- [x] Node fake-body/HTTP 测试 5 passed；皮肤、玩家客户端、实际寻路与战斗验收仍 not-run。
- [ ] 实际 Java 服务器版本待确认；指定快捷方式实际为基岩版，机器有 Java 入口但未发现默认版本目录。
- [ ] 账号/皮肤验收：用户尚无独立 Bot 账号，待购买后核对；当前账号认证不等同于多人共玩验收。

### 施工批次 2：后端 Activity 与管理面

- [x] 领域编排、HTTP Bridge Protocol、Docker 执行器分层；没有统一插件框架或新 EventBus。
- [x] 后端专用 Activity registry 元信息、通用 session/store/transcript；不虚构桌面/手机命令。
- [x] startup supervisor ownership、显式启停、15 秒身体租约、2 秒后端检查、角色/Reality/TTL 撤销。
- [x] 角色资产与主聊天只读参考、活动内 JSON 高层规划；无主记忆写入、模型并发/超时/频率与次数预算。
- [x] 手动动作使迟到模型计划失效；stop 在模型忙、预算用完或回执满时仍可使用。
- [x] scoped settings/activity/observability API、strict 请求、桥响应上限与稳定错误码。
- [x] 管理页、缓存版本、热保存、默认关闭与未配置状态通过隔离管理服务浏览器验收。
- [x] 后端相关回归 302 passed；追加鉴权测试另跑。Node 身体测试扩为 6 passed。
- [x] Docker build、healthy、依赖加载通过；实际 Java 26.3 连接被上游明确拒绝 unsupported protocol version。
- [x] Java 1.21.11 用户测试世界真实 LAN：离线测试身体成功 spawn、识别 owner，游戏聊天发送成功，跟随指令 running，10 秒后 stop succeeded 且 current 清空。此证据不等于持续移动、10 分钟跟随、正版皮肤或实际战斗验收。
- [x] 真实调试暴露离线玩家名超过 16 字符会导致 hello 解码失败；补后端与身体入口校验，拒绝无效玩家名。
- [ ] 独立 Bot 正版登录、皮肤、持续跟随与实际 PVE/拾取、真实模型人格连续性仍 not-run。

静态页面总检查的两个失败为既有 scheduler inline style 与 CSS orphan rules；本次没有修改这些文件或顺手修复。
上游依赖 audit 有 8 moderate 告警，已记录 known-issues；没有盲目覆盖 uuid 主版本。
本批没有桌面/手机协议变化，只验证既有 Activity 路由/Tauri 声明合同未漂移。

## 上游参考（2026-10-08 查阅）

- [Mineflayer 官方仓库：接口、版本与 Microsoft 认证](https://github.com/PrismarineJS/mineflayer)
- [mineflayer-pathfinder](https://github.com/PrismarineJS/mineflayer-pathfinder)
- [mineflayer-pvp](https://github.com/PrismarineJS/mineflayer-pvp)
