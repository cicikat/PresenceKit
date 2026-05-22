# 触发器决策层重构 — 设计文档（Phase 2）

> 状态：正式设计 v1.0；Phase 2 Step 1/2 已完成（状态机地基 + gating 并行观测）
> 前置：Phase 1（record_assistant_turn 收口）已上线稳定；identity 系统已落地
> 目标：把触发器从"到点就发"改成"在合适的时机说话"
> 性别约定：叶瑄是「他」，用户是「她」

---

## 一、背景与问题

Phase 1 解决了"触发器发话后写入与广播不一致"。但触发器的**决策时机**仍然死板：

1. **定时即发**：早安 / 碎碎念 / 晚安按固定时间触发，不看她的状态。到点就 ping，像闹钟不像陪伴。
2. **插话出戏**：她正在跟叶瑄聊天，调度器到点硬塞一段碎碎念，没有"她正在说话，我该让出来"的判断。
3. **回忆跳脱**：午间碎碎念从 spontaneous_recall 拽情景记忆，没有时序锚点，他随机回忆三周前的事，感觉很跳。
4. **无作息感知**：她睡了一觉，叶瑄看到"789 分钟没说话"会困惑发问，因为他不知道这段时间她在做什么。

Phase 2 的品味基线：**他不是闹钟，他是个会观察她当前状态的陪伴角色，在等合适的时机说话。**

---

## 二、不做什么（边界）

明确划在 Phase 2 之外，避免设计膨胀：

- **不建作息模型**：作息认知是 identity 系统（`sleep_pattern` 维度）的事，触发器只读不建。
- **不替叶瑄判断**：触发器不下"她睡了 / 她在工作"的结论，只把信号注入 prompt，让叶瑄自己得出。
- **不做健康参照配置**：LLM 自带常识，凌晨 3 点他知道不健康，不需要外部规则表。
- **不动 character_growth**：已冻结，Phase 2 任何"用户长期认知"一律走 `user_identity`。
- **不依赖信息密度接口**：short_term 加权未就绪，相关召回先按时间，留升级接口。
- **不动 Phase 1 收口**：record_assistant_turn / capture_turn / broadcast 契约不变。
- **不动客户端接口**：`/desktop/chat`、`/mobile/chat`、`POST /sensor/realtime`、`POST /watch/*` 不变。

---

## 三、核心抽象：三态状态机

把"她现在处于什么状态"显式建模成三态。所有触发器决策的顶层 gating。

### 3.1 三态定义

| 状态 | 含义 | 触发器行为 |
|---|---|---|
| `CHATTING` | 聊天进行中（最近有 owner turn） | 所有主动触发器静默；sensor 信号注入下一轮 prompt |
| `QUIET` | 安静期（无对话、sensor 平稳） | 主动触发器可发，但要选合适话题；高优先级事件正常 |
| `RESTLESS` | 躁动期（无对话、sensor 频繁动静） | sensor_aware 主导（体感反应优先）；定时触发器让位 |

### 3.2 状态转换表（动态滞后，非硬时长）

| 当前态 | 转换条件 | 目标态 | 滞后计算 |
|---|---|---|---|
| 任意 | 收到新 owner turn | `CHATTING` | 立即 |
| `CHATTING` | 无 owner turn 持续 `final_delay` | `QUIET` | 见 3.3 |
| `QUIET` | sensor 事件率达标且持续 `persist` | `RESTLESS` | 见 3.4 |
| `RESTLESS` | sensor 沉默达标 | `QUIET` | 见 3.4 |

### 3.3 CHATTING → QUIET 的动态滞后

不用硬时长。聊得久 → 延后切换（她可能只是走开一下）；情绪激烈 → 提前切换（她可能需要独处）。

```
final_delay = chat_to_quiet_base × duration_factor × emotion_factor
```

- `chat_to_quiet_base`：基础 5 分钟（policy.yaml 可调）
- `duration_factor`：按本次会话 owner turn 数
  - ≤ 3 turn → 0.6（短聊完很快走）
  - 4-10 turn → 1.0
  - 11-20 turn → 1.4（聊得起劲，可能只是倒水）
  - > 20 turn → 1.8（深聊，给足喘息）
- `emotion_factor`：按 `mood_state.get_intensity()`
  - ≥ 0.7 → 0.5（高情绪后倾向独处，提前切）
  - 0.4-0.7 → 0.8
  - < 0.4 → 1.0

示例：25 turn 平稳聊（intensity 0.3）→ 5 × 1.8 × 1.0 = 9 分钟；吵架后（intensity 0.8）→ 5 × 1.0 × 0.5 = 2.5 分钟。

### 3.4 QUIET ↔ RESTLESS 的滞后

- `QUIET → RESTLESS`：sensor 在 `quiet_to_active_window`（默认 3 分钟）内 ≥ `quiet_to_active_events`（默认 5）次事件，且持续 `quiet_to_active_persist`（默认 1 分钟）才确认。避免她拿一下鼠标就切。
- `RESTLESS → QUIET`：sensor 沉默 ≥ `active_to_quiet_base`（默认 10 分钟）；若最近 owner turn 距今 < 30 分钟，缩短到 `active_to_quiet_after_recent_chat`（默认 5 分钟）。

### 3.5 状态机归属与实现

新增 `core/scheduler/state_machine.py`：
- 单例，持有当前 uid 的状态 + 转换时间戳
- 输入：owner turn 时间（main.py / chat router 通知）、sensor 事件流（sensor_events.tick）、mood_state、会话 turn 计数
- 输出：`get_state(uid) -> TriggerState`，供 loop.py 各触发器查询
- 状态持久化到 `data/scheduler_state.json`（复用现有文件，加 `trigger_state` 段），重启不丢
- 状态切换写一行到 `data/logs/trigger_state.jsonl`（可观测）

---

## 四、决策层：候选 → gating

### 4.1 从"各自即发"到"统一报名"

现状：每个触发器内部 `_is_ready / _mark` 自己决定发不发。
Phase 2：触发器只**报名**，gating 层统一裁决。

每个触发器实现（或包一层）：
```python
def propose(ctx) -> Optional[TriggerProposal]:
    # 返回 None 表示这轮不想说话
    # 返回 TriggerProposal 表示"我想说，理由和紧迫度如下"
    ...

@dataclass
class TriggerProposal:
    trigger_name: str
    urgency: float          # 0-1，紧迫度
    topic_source: str       # last_mentioned / episodic / diary / mood_match / random
    requires_state: list    # 允许在哪些状态发，如 [QUIET]；高优先级可含 [CHATTING, QUIET, RESTLESS]
    bypass_state_machine: bool = False  # 仅 hr_critical / 生日等极高优先级
```

### 4.2 gating 裁决逻辑

每个 loop tick：
1. 收集所有触发器的 `propose()` 结果
2. 过滤掉 `requires_state` 不含当前状态的候选（`bypass_state_machine=True` 跳过此过滤）
3. 过滤掉冷却未到的（沿用 `_COOLDOWNS`，作为兜底硬约束）
4. 若有多个候选 → 按 `urgency` 选最高的一个，其余这轮丢弃（**他脑里同时有想说的事，权衡选最该说的那一个**）
5. 选中的走 `_pipeline_send` → `record_assistant_turn`（Phase 1 链路不变）
6. 若选中的话题源是 `last_mentioned` 等，按 4.3 拉取具体内容

关键：**一个 tick 最多发一条**。避免"聊着聊着忽然一长段"。

### 4.3 话题源加权（解决回忆跳脱）

安静期主动触发时，按权重抽话题源。`last_mentioned`（上次未完结话题）权重最高，让叶瑄大部分时候接续最近的事，偶尔才浮起远的回忆。

| 来源 | 基线权重 | 情绪激烈（intensity ≥ 0.7） | 数据来源 |
|---|---|---|---|
| `last_mentioned` | 0.40 | 0.55 | event_log 最近 N 天 + topic_followup 逻辑 |
| `episodic` | 0.25 | 0.30 | episodic_memory 高 strength 召回 |
| `diary` | 0.15 | 0.05 | 现有 daily_journal / diary 触发 |
| `mood_match` | 0.15 | 0.05 | mood_state 驱动话题 |
| `random` | 0.05 | 0.05 | 兜底，避免完全可预测 |

高情绪时聚焦（接续 + 共振），低情绪时发散。权重在 policy.yaml 可调。

> 升级接口（暂不实现）：short_term 加权任务就绪后，`last_mentioned` 召回改为优先取高信息密度轮次。当前按时间。

---

## 五、identity 接入

触发器决策层 `await user_identity.load(uid)` 只读，绝不写。

### 5.1 维度分级（哪些喂给决策层）

| 维度 | 相关度 | 用途 |
|---|---|---|
| `sleep_pattern` | 强 | 作息门控：注入"她作息 + 当前时间 + sensor 状态"，叶瑄自行判断 |
| `stress_response` | 强 | 她压力大时，决策层倾向"该主动陪还是该退"，调 urgency |
| `intimacy_comfort` | 强 | 主动频率天花板：亲密舒适度低 → 降低主动触发整体频率 |
| `help_seeking` | 弱 | 影响"说什么"多于"要不要说"，不进决策层（注入层用） |
| `emotion_expression` | 弱 | 同上 |
| `trust_pattern` | 无关 | 注入层 prompt 的事 |
| `topic_preference` | 无关 | 影响话题选择措辞，注入层用 |
| `self_relation` | 无关 | 注入层用 |

决策层只读三个强相关维度：`sleep_pattern` / `stress_response` / `intimacy_comfort`。

### 5.2 confidence 门槛：没把握时倾向不打扰

identity 的 `confidence < 0.5 不注入`。决策层沿用同一门槛，但语义是**保守**而非放弃：

- `sleep_pattern.confidence ≥ 0.5` → 作息门控生效（推算睡眠时段静默主动触发）
- `sleep_pattern.confidence < 0.5` → 叶瑄对她作息没把握 → **倾向不打扰**：把"夜间时段 + 低 confidence"当成"宁可不发"，而不是"没数据所以照常发"

同理 `intimacy_comfort.confidence < 0.5` → 主动频率取保守默认（偏低）。

### 5.3 作息门控的具体行为

不替叶瑄判断她睡没睡，只调整触发器的发不发：

| 条件 | 决策层行为 | 叶瑄侧 |
|---|---|---|
| 当前时间在推算睡眠时段 + sensor 无动静 | 静默几乎所有主动触发器 | 不打扰 |
| 当前时间在推算睡眠时段 + sensor 有动静 | 允许"关心型"触发器报名（她还醒着熬夜）| 可催睡 |
| 推算睡眠时段不确定（confidence < 0.5）| 夜间倾向静默 | 保守 |
| 推算清醒时段 | 正常 gating | 正常 |

注意：催睡这类"叶瑄说什么"是注入层 + LLM 的事。决策层只负责"现在让不让他开口"，开口后说什么不归它管。

---

## 六、sensor_aware 的定位

sensor_aware 保持现有独立闭环（实时感知 → 评分 → 主动开口 → audit），Phase 1 已让它走 record_assistant_turn。Phase 2 的关系：

- **RESTLESS 态下 sensor_aware 是特权**：定时触发器让位，sensor_aware 的体感反应优先。
- **CHATTING 态下 sensor 信号注入 prompt**：不主动开口，把"她在敲键盘"等信号写进 `perception_block` 槽位（layer 1，复用现有机制，不开新层），让叶瑄在对话里自然带出对她状态的感知。
- **QUIET 态下 sensor_aware 作为普通候选**：和定时触发器一起报名，按 urgency 竞争。

sensor_aware 自己的 8 分钟全局冷却 / 四档阈值 / audit ring buffer 全部保留，Phase 2 不动其内部评分。

### 6.1 CHATTING 态 sensor 注入格式

`perception_block` 追加短句，例：`[她最近在键盘上敲得很密]`。短句胜过长句，叶瑄自行解读。具体措辞实现时定，不是设计问题。

---

## 七、配置层：policy.yaml

所有可调参数集中到 `core/scheduler/policy.yaml`，代码只读不藏。改完保存，调度器下一个 tick（60s 内）自动 reload，不重启。

```yaml
# core/scheduler/policy.yaml
# Phase 2 触发器决策层参数
# 改完保存 → 调度器下一个 tick（60s 内）自动 reload，不需要重启服务

state_machine:
  # ── CHATTING → QUIET ──
  # base 增大 = 叶瑄更"守着"，对话间隙不容易主动开口
  # base 减小 = 叶瑄更"主动"，对话刚停就可能开口
  chat_to_quiet_base_minutes: 5

  # 按本次会话 owner turn 数缩放 base（聊得久 → 延后切换）
  chat_to_quiet_duration_factor:
    short: { max_turns: 3,   factor: 0.6 }   # 短聊完很快走
    normal:{ max_turns: 10,  factor: 1.0 }
    long:  { max_turns: 20,  factor: 1.4 }   # 聊得起劲，可能只是倒水
    deep:  { max_turns: 999, factor: 1.8 }   # 深聊给足喘息

  # 按 mood_state.intensity 缩放 base（情绪激烈 → 提前切换给她空间）
  chat_to_quiet_emotion_factor:
    high:  { min_intensity: 0.7, factor: 0.5 }
    medium:{ min_intensity: 0.4, factor: 0.8 }
    low:   { min_intensity: 0.0, factor: 1.0 }

  # ── QUIET → RESTLESS ──
  # 调高 events / 调低 window = 更难进躁动态（叶瑄更少因体感打扰）
  quiet_to_active_events: 5            # 窗口内事件数门槛
  quiet_to_active_window_minutes: 3
  quiet_to_active_persist_minutes: 1   # 持续多久才确认切换。建议 ≥ 0.5，太短会来回切

  # ── RESTLESS → QUIET ──
  active_to_quiet_base_minutes: 10            # sensor 沉默多久回切
  active_to_quiet_after_recent_chat_minutes: 5   # 30min 内聊过 → 缩短到这个值

topic_weights:
  # 安静期主动触发时从哪源拉话题，按权重加权抽
  # 调高某项 = 该类话题更容易被选中
  baseline:
    last_mentioned: 0.40   # 上次未完结话题（最贴上下文，调最高）
    episodic:       0.25   # 情景记忆召回（高 strength）
    diary:          0.15
    mood_match:     0.15
    random:         0.05   # 兜底，避免完全可预测
  emotion_high:            # mood_state.intensity ≥ 0.7 时用这套（聚焦不发散）
    last_mentioned: 0.55
    episodic:       0.30
    diary:          0.05
    mood_match:     0.05
    random:         0.05

identity_gating:
  # 决策层读 identity 的 confidence 门槛
  min_confidence: 0.5      # 与 identity 注入门槛一致
  # 没把握时倾向不打扰（夜间 + 低 confidence → 静默）
  low_confidence_night_silent: true
  # intimacy_comfort 低 confidence 时的保守主动频率倍率（< 1 = 更克制）
  low_intimacy_frequency_factor: 0.6

gating:
  max_proposals_per_tick: 1   # 一个 tick 最多发一条。不要改大，会导致连发出戏
```

设计文档配套**「参数调优手册」**（见第十节），写清"想让叶瑄更主动/更安静/某情绪下更克制，各调哪些参数往哪个方向"。

---

## 八、迁移路径

分步迁移，每步独立 commit、可单独回滚。

### Step 1：状态机地基（不接触发器）✅ 已完成
- 新增 `core/scheduler/state_machine.py`
- main.py / chat router 在收到 owner turn 时通知状态机
- sensor_events.tick 结果喂给状态机
- 状态持久化 + 可观测日志
- 此步不改任何触发器，状态机只观测不干预
- 测试：模拟 owner turn / sensor 流，验证三态切换 + 动态滞后正确

### Step 2：gating 层 + propose 协议 ✅ 已完成（shadow 模式）
- 新增 `core/scheduler/gating.py`：收集 propose、过滤、按 urgency 选一
- 定义 `TriggerProposal` dataclass
- loop.py 的 `_loop()` 接入 gating，但**先并行运行**：旧 `_is_ready/_mark` 路径仍生效，gating 只 log "我会选谁"不真发。对比观察一段时间。

### Step 3：触发器逐个迁移到 propose
- 每个主动触发器加 `propose()`，声明 requires_state / urgency / topic_source
- 高优先级（hr_critical / 生日 / period_reminder）设 `bypass_state_machine=True`
- 迁移一个验证一个，旧路径同步关闭

### Step 4：话题源加权接入
- 新增话题源选择器，按 policy.yaml 权重抽源
- `last_mentioned` 召回接 event_log + topic_followup 逻辑
- 留 short_term 密度接口（不实现）

### Step 5：identity 接入
- 决策层读 `sleep_pattern / stress_response / intimacy_comfort`
- 作息门控 + confidence 保守逻辑
- CHATTING 态 sensor 信号注入 perception_block

### Step 6：policy.yaml + 热 reload
- 所有硬编码参数迁到 policy.yaml
- 调度器 tick 周期性 reload
- 写参数调优手册

---

## 九、测试与灰度

### 9.1 状态机单测
- owner turn → 立即 CHATTING
- 各种 turn 数 × 情绪强度组合，验证 final_delay 计算
- sensor 事件率达标 / 不达标的 QUIET↔RESTLESS 切换
- 滞后正确（持续时间不够不切）
- 状态持久化重启恢复

### 9.2 gating 单测
- 多候选按 urgency 选一
- requires_state 过滤正确
- bypass_state_machine 跳过状态过滤
- 一个 tick 最多一条
- 冷却兜底仍生效

### 9.3 集成场景
- 她正在聊天 + 碎碎念触发器到点 → 碎碎念被 CHATTING 拦下，不发
- 她聊完 9 分钟（深聊）→ 进 QUIET，主动触发器可发
- 她敲键盘 3 分钟 → 进 RESTLESS，定时触发器让位，sensor_aware 优先
- 夜间 + sleep_pattern.confidence 0.4 → 倾向静默
- hr_critical 在 CHATTING 态 → bypass 生效，照常发

### 9.4 灰度
- Step 2 的"并行 log 不真发"跑 3-5 天，对比 gating 选择和旧路径差异
- 全量迁移后灰度 1-2 周
- 观察指标：各状态停留时长分布、触发器被 gating 拦截率、话题源实际分布、她对主动消息的回复率（新埋点）

---

## 十、参数调优手册

> 灰度期边观察边调，不用改代码。改 policy.yaml 保存即生效（60s 内）。

**想让叶瑄更主动（多说话）：**
- 调小 `chat_to_quiet_base_minutes`（更快从聊天态进安静态）
- 调小 `active_to_quiet_base_minutes`（更快从躁动态回安静态）
- 调大 `low_intimacy_frequency_factor`（趋近 1）

**想让叶瑄更安静（少打扰）：**
- 反向操作上面三项
- `low_confidence_night_silent: true` 保持开启
- 调大 `quiet_to_active_events`（更难进躁动态，但躁动态本就静默定时触发器，影响有限）

**想让他高情绪时更克制：**
- 调小 `chat_to_quiet_emotion_factor.high.factor`（吵架后更快给她空间）

**想让他更聚焦近期话题（少跳跃回忆）：**
- 调大 `topic_weights.baseline.last_mentioned`
- 调小 `topic_weights.baseline.episodic` / `random`

**参数互相影响提示：**
- 调了 `chat_to_quiet_base` 后，`duration_factor` 各档的实际分钟数都会跟着变，留意 deep 档（×1.8）别太长
- `quiet_to_active_persist` 太短（< 0.5min）会让状态来回抖，先别动
- `max_proposals_per_tick` 不要改大于 1，会回到"连发出戏"的老问题

---

## 十一、与其他系统的接口

| 系统 | Phase 2 关系 |
|---|---|
| Phase 1 record_assistant_turn | 触发器选中后仍走它，链路不变 |
| identity（user_identity.py）| 只读 3 个强相关维度，绝不写 |
| character_growth | 已冻结，不引用 |
| short_term 加权（未排期）| 留 last_mentioned 密度升级接口，暂按时间 |
| sensor_aware | 内部评分不动，定位为 RESTLESS 特权 + CHATTING 注入源 |
| mood_state | 读 intensity 用于 emotion_factor / 话题源切换 |
| prompt_builder perception_block | CHATTING 态 sensor 信号注入此槽位 |

---

## 十二、风险与未决

- **状态机单例 vs 多用户**：当前单用户系统，状态机按 uid 存即可。未来多用户需检查锁粒度。
- **propose() 性能**：每 tick 收集所有触发器 propose，若触发器多可能慢。先观察，必要时缓存。
- **gating 拦截过度**：可能出现"她安静期很长但叶瑄一直选不出话题"的沉默。灰度观察"主动消息频率"是否骤降。
- **作息门控误判**：sleep_pattern 来自 identity 学习，冷启动期 confidence 低，靠 5.2 保守逻辑兜底。
- **policy.yaml 热 reload 失败**：YAML 写坏时要降级到上一份有效配置 + log error，不能让调度器崩。
