# 事件驱动的情绪与表达（2026-10-25）

当前实现以 `core/affect.py`、`core/affective_appraisal.py`、`core/memory/mood_state.py` 为准。施工与验证记录见 [工单](work-orders/affective-engine-2026-10-25.md)。这是可修正的角色情绪模拟，不代表用户真实动机、关系事实或心理学测量。

## 单一状态与时序

沿用 `get_paths().mood_state(char_id=...)`，char-only、schema_version=3，没有新增数据根或第二状态库。v2 只保留合法来源/TTL 的表达；更早的无来源枚举不认领为内心。旧文件只在下一次合法写入时升级，读取不落盘。

真实回合先 critical capture，再 fanout，再运行 slow：明确表达立即提交并推 avatar；认知评价在锁外进行；结果用同步 RMW 锁重读、校验来源水位与开关后提交。只有仍是最新源回合且未提交过的评价可以写入。时间相同但 turn_id 不同的回合允许按提交次序写入；旧时间、重复 turn、未来源时间不写。无需因一次评价推迟当前回复、表情或口型；它影响**后续**生成，不宣称本轮回复已被本轮评价约束。

## 内在评价与重评

输入仅接受具有 `can_affect_mood`、成功 critical capture 的单人真实 USER_CHAT/QQ/WECHAT 对话；trigger、sensor、ingest、debug、group、activity、web/coplay/dream echo 均不喂现实评价。复用 `detect_emotion` 模型路由；该名称保留为配置 ABI，旧单标签检测函数及 facade 已删除。独立硬件爱意探针仍复用此路由，含义不变。

每个合格回合最多一次评价服务调用，8 秒总墙钟预算，最多 500 输出 token。模型底层可按既有路由进行有限 transport failover；外层预算约束整次评价。输入为冻结角色 id 对应的 authored 描述/性格/规则、最多 4000 字真实用户输入和最近 8 条有效事件的有界评价/证据摘录，不读取用户 hidden_state，不把 assistant 的微笑倒推为快乐。支持文本 JSON 和既有 System One 闭合决策协议；跨 provider 的重试仍由公共 LLM 层负责。

模型给出 joy/anger/hurt/concern/disappointment/fear/sadness/surprise 八维候选、confidence、relevance、goal_impact、暂定 agency、strategy 和 correction_target。数值必须是有限 0..1；普通 confidence 至少 .55，更正至少 .7；非法候选整包拒绝，不降级生成 neutral。agency 是可错解释，不是真实用户责任认定。

事件贡献乘 confidence/relevance 后，用各维固定半衰期投影，混合使用有界饱和合成；心境 PAD 从较慢的贡献投影，不能反向写人格/关系。参数属于内部工程假设：joy 30 分钟、anger 45 分钟、hurt/sadness 2 小时、concern 1 小时、disappointment 4 小时、fear 20 分钟、surprise 3 分钟；心境至少 6 小时。低于 .04 不注入；7 天以外不贡献。静默不造事件，不触发模型，不积累永久情绪负债。

重评只允许指向提供给模型的已存在有效事件。新向量替换该事件的贡献，不再新增第二份贡献；保留原来源 turn、发生时间、最近 4 次被替代评价、更正 turn 与最多 600 字证据摘录。原始对话仍在既有聊天/事件账本。存储最多 48 条事件、192 个回执；不同情绪可并存。源事件撤回通过既有 invalidation 级联精确匹配 canonical `turn_id:user`，整条相关贡献失效，恢复走原可逆标记流程，不复活被推翻的旧判断。

## 表达与消费者

外层 `current/intensity/has_signal/source_turn_id/source_at/expires_at` 继续代表**明确表达**，最多 30 分钟。`<do>` 的可见表达优先于 `<feel>` 内心叙述；否定、引用、他人情绪不自动贴标签。内部 anger 可以与 outward happy 共存。

| 消费者 | 合同 |
| --- | --- |
| prompt | `expression_afterglow` 描述发生过的表达；新 `affective_state` 是可裁剪、优先级 5 的可错内在解释/调节建议，来源与版本在 provenance；中英框架文案同步 |
| letter / diary / thinking | 使用同一有效状态投影，优先混合感受；没有信号不强填内心 |
| garden | `get_inner_current` 映射内在主要情绪，缺内在信号/关闭时兼容明确表达；不会用微笑清掉愤怒贡献 |
| scheduler | 主动打断、overflow 和安静期计时仍用明确表达；不会由隐藏不悦自动催促用户；生成阶段可读混合状态 |
| episodic recall | 不新增情绪同调权重、nudge 或额外增强；既有标签仅用于质感措辞 |
| hidden_state / 事实整合 | 不消费 appraisal 向量；原表达与新评价都不作为用户内心证据；没有新增用户记忆写入点 |
| TTS / sticker / avatar / 手机与桌面轮询 | 继续只消费明确表达与已有表现请求；嘴型跟随实际音频；不把内部主情绪强行映成整脸 |
| Dream | 不写入现实引擎；进入 Dream 的既有显式快照保持其边界，修正了快照格式化缺少 char_id 的旧读取 |

策略 express/suppress/reappraise/pause/none 是建议，不是动作执行器或情绪必须被压抑的规定，不改变工具权限、关系、角色卡或用户事实。

## 控制面与观测

- `affective_engine.enabled` 默认 true，管理面高级功能开关热更新；关闭即回到仅明确表达余韵。既有事件保留可观测，但不参与内在 prompt、花园或 thinking。重新开启按真实经过时间投影，不续命。
- `GET /mood/state`（state.read）保留兼容表达字段，加 `affect`、event_count、appraisal_status、affect_prompt_eligible/layer，不返回证据正文和事件列表。资格不等于实际注入，后者查 Prompt 观测。
- `GET /mood/events?char_id=...&limit=20`（memory.read；1..48）返回有界事件、修正与来源摘录。
- 管理面「情绪·花园」同时显示上述两面；runtime 快照调用有效 load，不直接展示已过期的原始文件。
- 超时/非法模型结果计入 `/observability/runtime-signals`，实际请求沿既有 `/observability/api-calls` 的 detect_emotion 路由查询。最后一次评价状态持久保存在同一状态文件，进程计数不冒充历史总量。损坏文件返回 invalid_store 且无信号，写入失败沿现有错误观测上报。

`avatar_directive` 新增可选 char_id/version/source_turn_id/source_at/expires_at；表达仍是旧白名单、ttl_ms 最多 6000。后端只为当前角色推送；桌面统一 WS 入口先校验角色、来源版本和截止时间，过时指令确认收件后丢弃，投递延迟扣减 TTL。旧无版本包保持兼容；旧客户端忽略新增字段，跨角色/乱序保证要求更新桌面。手机未新增动作入口，仍轮询表达。

## 验收边界

确定性测试证明混合、修正、独立表达、去重/乱序、超时、开关、来源隔离、源撤回、读路径和角色边界。隔离管理面使用测试配置和合成证据验证控件与返回，不能证明真实模型的人格差异、长期拟人效果或原生设备表现。当前不实现多通道独立表情参数、目光资产或新的声线；已有 perform 请求仍由原解析/模型素材控制。模型盲评与真实桌面/手机联调为 observe。
