# 触发器统一写入与广播 — 设计文档（Phase 1）

> 状态：Phase 1 已落地；本文保留原设计推导，路径按当前 datapath 校准
> 范围：把"叶瑄发话已完成"这件事收口到一个函数，让所有入口走同一条写入 + 广播路径
> 不做：触发器决策时机重构、冷却参数热改、调度器分类拆分 —— 全部留给 Phase 2

---

## 一、问题陈述（依据 codex 报告）

当前"叶瑄说完一句话"的善后散落在多条链路，质量参差：

| 入口 | 写 event_log | 写 short_term | broadcast | 备注 |
|---|---|---|---|---|
| `/desktop/chat`、`/mobile/chat` | ✓ user+assistant | ✓ user+assistant | ✓ 全 channel | 基线，conversation_gate 串行，post_process 关键块 await |
| 普通 scheduler 触发器（morning_greeting / period_reminder / birthday / festival / timenode / topic_followup / reminders / diary_reminder / hr_high / hr_critical / garden_bloom 等） | ✓ assistant only | ✓ assistant only | ✓ 全 channel | 弱于基线：无 probe、无 conversation_gate、post_process 不 await |
| **sensor_aware** | ✓ assistant only（异步、不 await） | ✓ assistant only（异步、不 await） | ✗ **直推 desktop_ws，跳过 broadcast** | 漏 mobile、漏 QQ、离线 fallback 失效 |
| **sleep_end**（`admin/routers/watch.py:_flush_sleep_buffer`） | ✓ 但**写成 user+assistant**（污染 user 行） | 同左 | ✓ broadcast | 没传 `trigger_name`，被当成 owner turn；同时绕过 `watch.py:on_watch_event` |
| 维护型任务（diary_inject / episodic_decay / episodic_sweep / dlq_monitor / activity_switch / dnd） | — | — | — | 不是 assistant turn，不在 Phase 1 范围 |

副问题：

- garden_bloom / harvest_expired / vase_wilted / handle_ask / handle_gift / handle_self 等花园事件，冷却名已登记但 `_is_ready / _mark` 没真正节流，部分还叠 30% 概率（codex 报告原话）。
- 普通触发器 post_process 用 `asyncio.create_task` 不 await，意味着触发器返回时记忆可能尚未落盘，并发场景下可能与下一条用户消息交错。

---

## 二、目标

Phase 1 必须达成：

1. 任意入口产生一句叶瑄的话之后，**三处必同时有新条目**：
   - `data/runtime/memory/{char_id}/{uid}/event_log/{date}.md`
   - `data/runtime/memory/{char_id}/{uid}/history.json`（short-term）
   - 所有目标 channel 的下行（按 fanout 策略）
2. `/desktop/chat`、`/mobile/chat` 对客户端的可观察行为**完全不变**（字段、时序、behavior 都一致）
3. 所有触发器写入语义统一：`trigger_name` 非空、assistant only；source 在 sink 内部保留，落盘仍编码到 `trigger_name`

Phase 1 不破坏：

- `/desktop/chat`、`/mobile/chat`、`POST /sensor/realtime`、`POST /watch/*` 这些外部接口
- `channels.registry.broadcast()` 的现有契约
- `capture_turn()` 的现有签名（仅可能在调用方向上微调）

Phase 1 不做：

- 触发器决策时机重构、体感信号接入、概率模型、冷却热改、分类调度器 → **全部 Phase 2**

---

## 三、核心抽象：`record_assistant_turn`

新增模块 `core/turn_sink.py`，作为"叶瑄发话已完成"的唯一汇聚点。

### 3.1 函数签名（草案）

```python
# core/turn_sink.py

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence, Union

class TurnSource(str, Enum):
    USER_CHAT = "user_chat"   # /desktop/chat、/mobile/chat
    TRIGGER   = "trigger"     # 普通 scheduler 触发器
    SENSOR    = "sensor"      # sensor_aware（trigger 子类，单独打标便于追踪）
    WATCH     = "watch"       # 手表事件（hr_high / hr_critical / sleep_end）

FanoutPolicy = Union[str, Sequence[str]]
# "all"  →  channels.registry 注册的全部通道
# ["desktop", "mobile"]  →  指定通道
# "broadcast"  →  "all" 的别名

@dataclass
class TurnResult:
    turn_id: str
    written_to_memory: bool
    fanout_targets: list[str]
    fanout_failures: dict[str, str] = field(default_factory=dict)
    post_process_scheduled: bool = False

async def record_assistant_turn(
    *,
    assistant_text: str,
    uid: str,
    source: TurnSource,
    trigger_name: Optional[str] = None,   # source != USER_CHAT 必填
    user_text: Optional[str] = None,       # source == USER_CHAT 必填
    fanout: FanoutPolicy = "all",
    payload: Optional[dict] = None,        # 内部兼容壳；当前只读取 payload["behavior"]
    await_critical_post_process: bool = True,
    bypass_gate: bool = False,             # 仅 hr_critical 等极高优先级允许 True
    pipeline=None,                          # 由调用方注入或从 pipeline_registry 取
) -> TurnResult:
    ...
```

### 3.2 行为契约

按顺序：

1. **入参校验**
   - `source == USER_CHAT` → 必须有 `user_text`、不能有 `trigger_name`
   - `source != USER_CHAT` → 必须有 `trigger_name`、不能有 `user_text`
   - `assistant_text` 非空

2. **生成 `turn_id`**（UUID4），随后回填给 `capture_turn`

3. **进入 `conversation_gate(uid)`**（per-uid lock，与 owner 入口共享）
   - `bypass_gate=True` 时跳过（仅供 hr_critical 这类不能等的事件用）
   - 串行化的好处：触发器与用户输入不会交错写入 short_term / event_log

4. **`capture_turn`**（保持现有契约）
   - `USER_CHAT`：写 user + assistant
   - 其余：写 assistant only，event_log 通过 `trigger_name` 记录触发来源
   - 失败入 `capture_turn_retry` 慢队列（已有逻辑保留）

5. **退出 conversation_gate**

6. **fanout**
   - 解析 `FanoutPolicy` → 具体 channel 实例列表（从 `channels.registry`）
   - 逐个 `await channel.send(message, user_id, behavior=behavior)`，单个失败不阻塞其他
   - 失败计入 `TurnResult.fanout_failures`
   - **此步之后，任何"直推 desktop_ws / mobile_queue"的代码必须删除**

7. **post_process 慢队列触发**（与 owner 入口一致）
   - 入 slow_queue：`summarize_to_midterm` / `reflect_to_episodic` / `consolidate_to_growth` / `consistency_check` / `user_profile_update`
   - `await_critical_post_process=True` 时等待关键块（`detect_emotion` + `mood_state.update` + `capture_turn`）完成再返回
   - TTS / 表情包等副作用保持 `asyncio.create_task` 模式，不接管

8. **返回 `TurnResult`**

### 3.3 与现有模块的关系

- **不替换 `_pipeline_send`**：它继续作为"跑完 pipeline 拿到文本"的封装，只是内部的 `broadcast + capture + create_task(post_process)` 这一段替换为 `await record_assistant_turn(...)`。触发器代码层面无感。
- **不改 `capture_turn`**：它仍然是写入原语，签名稳定。
- **不改 `channels.registry.broadcast`**：record_assistant_turn 内部就是调用它。
- **behavior 通道**：sensor_aware 现在直推 WS 时携带 action 包。迁移后由 `DesktopChannel.send(text, user_id, behavior=...)` 接收 behavior，自己决定怎么序列化（WS 在线发原 channel_message 再发 action；离线写 `data/runtime/agent_actions.json`）。action 协议保持不变。
  - `MobileChannel.send` 将 behavior 同条写入 `data/runtime/mobile_queue.json`；`QQChannel.send` 忽略 behavior。

### 3.4 当前追加：Narrative Message 双轨

`record_assistant_turn()` 当前还会为桌面 WS 预生成共享 `msg_id`：

1. 原始 `assistant_text` 先供 desktop 双轨展示；reality memory / event_log 写入前移除
   `<say>` 等展示标签；
2. `core/narrative_parser.py` 解析 `<say>` / `<do>` / `<env>` / `<feel>`；
3. desktop WS 额外 fire-and-forget 推一条同 `msg_id` 的 `message_segments`；
4. QQ / mobile 输出移除展示标签；desktop segments 保持原行为，旧桌面客户端忽略未知消息即可。

segments 是只读展示视图，不得替换 Dream archive 中的原始回复。

---

## 四、各调用方迁移清单

| 调用方 | 当前路径 | 改造后 | 行为差异 |
|---|---|---|---|
| `admin/routers/chat.py::run_owner_chat_turn` | 自己 await post_process + broadcast | `await record_assistant_turn(source=USER_CHAT, fanout="all", ...)` | 等价；首批回归 |
| `core/scheduler/loop.py::_pipeline_send` 内部出口 | broadcast + capture + create_task | 调 `record_assistant_turn` | 触发器代码层面无感 |
| `core/scheduler/triggers/time_based.py`（morning_greeting / night_reminder / random_message / weather_alert / daily_journal / spontaneous_recall） | 经 `_pipeline_send` | 不动 | 写入语义不变，多了 conversation_gate 串行 |
| `core/scheduler/triggers/diary.py`（diary_reminder / diary_share_reminder） | 同上 | 同上 | 同上 |
| `core/scheduler/triggers/period.py` | 同上 | 同上 | 同上 |
| `core/scheduler/triggers/birthday.py` 四档 | 同上 | 同上 | 同上 |
| `core/scheduler/triggers/timenode.py` | 同上 | 同上 | 同上 |
| `core/scheduler/triggers/festival.py` | 同上 | 同上 | 同上 |
| `core/scheduler/triggers/memory.py::topic_followup` | 预判 LLM + `_pipeline_send` | 预判 LLM 保留；发话路径不动 | 同上 |
| `core/scheduler/triggers/garden_water.py::garden_bloom` 等 | `_pipeline_send` | 不动；**冷却节流另议**（见开放问题 #2） | 同上 |
| `core/scheduler/triggers/garden_daily.py` 各事件 | 同上 | 同上 | 同上 |
| `core/scheduler/triggers/watch.py`（hr_high / hr_critical） | `_pipeline_send` | hr_critical 用 `bypass_gate=True` | 极高优先级不被用户输入阻塞 |
| `core/scheduler/loop.py::reminders` 分支 | `_pipeline_send` | 不动 | 同上 |
| **`core/scheduler/triggers/sensor_aware.py`** | `_pipeline_send(output_mode="return") + desktop_ws.push_message` 直推 | 显式 `record_assistant_turn(source=SENSOR, fanout=["desktop", "mobile"], payload={"behavior": ...})` | **行为变更：从 desktop-only 到 desktop+mobile fanout** |
| **`admin/routers/watch.py::_flush_sleep_buffer`** | broadcast 但无 `trigger_name`，写成 user+assistant | `record_assistant_turn(source=WATCH, trigger_name="sleep_end", ...)`，回写也接入 `watch.py:on_watch_event` 统一入口 | **修 bug：user 行不再被污染、watch 事件流不再被绕过** |

---

## 五、待你确认的产品/设计决策

1. **sensor_aware 的 fanout 范围**
   - 选项 A：`fanout="all"`（含 QQ）—— 与"漏 fanout 是 bug"的判定最一致
   - 选项 B：`fanout=["desktop", "mobile"]`，跳过 QQ —— 体感反应跟桌面环境强相关，QQ 用户收到可能困惑
   - **我倾向 B**：体感反应跨设备到手机端合理，跨到 QQ 失去语境
   - 你怎么定？

2. **garden 事件冷却节流是否在 Phase 1 内修**
   - codex 报告指出冷却名已登记但未真正节流
   - 选项 A：Phase 1 顺手补 `_is_ready / _mark` —— 改动小、收益明确
   - 选项 B：拆成 Phase 1.5 单独提 PR
   - 选项 C：留给 Phase 2 一起进冷却参数热改
   - **我倾向 A**

3. **触发器是否经过 `conversation_gate`**
   - 选项 A：全部经过 —— 写入一致性最强，代价是用户正在打字时触发器被短暂阻塞
   - 选项 B：默认经过，hr_critical 等极少数 `bypass_gate=True`
   - 选项 C：全部不经过，保留现有"触发器随时插话"语义
   - **我倾向 B**：99% 串行 + 极少数兜底逃生

4. **post_process 是否 await 关键块**
   - 选项 A：默认 await —— 与 owner 入口一致，触发器返回时记忆已落盘
   - 选项 B：默认 create_task —— 触发器返回更快
   - **我倾向 A**：只 await 关键块（emotion + mood + capture_turn），慢队列继续异步，代价可控
   - 这条与 #3 联动

5. **sleep_end 是否要顺便修"绕过 watch.py:on_watch_event"**
   - 这是 codex 报告里捎带提到的副问题
   - **我倾向修**：既然要重写这段，事件流一起接通，否则下次又是补丁

---

## 六、测试方案

### 6.1 单触发器验证（按 codex 报告里的触发器表逐个跑）

每个触发器触发后 3 秒内必须满足：

- `data/runtime/memory/{char_id}/{uid}/event_log/{date}.md` 末尾多一行，`trigger` 字段正确
- `data/runtime/memory/{char_id}/{uid}/history.json` 末尾多一行 assistant（owner 入口是 user+assistant 一对）

每个触发器触发后 5 秒内必须满足：

- 桌面 WS 在线时：客户端收到 message
- 桌面 WS 离线时：`data/runtime/agent_actions.json` 或 DesktopChannel fallback 文件出现条目
- mobile 端 active 时：`data/runtime/mobile_queue.json` 出现条目
- QQ 在线时：QQ 通道发出消息（仅当 fanout 含 QQ）

### 6.2 回归测试

- `/desktop/chat` 发一条 → memory 与 channel 行为**与改造前字节一致**
- `/mobile/chat` 同上
- sensor_aware 触发 → **预期差异**：多了 `data/runtime/mobile_queue.json` 条目（按开放问题 #1 决议可能也加 QQ）
- sleep_end 触发 → **预期差异**：`data/runtime/memory/{char_id}/{uid}/history.json` 的 user 行不再被括号 prompt 污染

### 6.3 并发场景

- 用户正在打字时触发器到点 → 触发器等待 conversation_gate，不交错写入
- hr_critical 触发 + 用户同时输入 → hr_critical `bypass_gate=True`，照常发，但 capture_turn 在内部锁保护下不损坏 short_term

---

## 七、Phase 2 预告（仅高层方向，详细设计另写）

阶段一稳定后（建议生产跑 1-2 周观察），再做触发器决策层重构：

- **输入信号**：在时间窗外引入 sensor_aware 的 active_score / focus_hint / 键鼠节奏
- **决策层**：每个触发器声明 `triggered_when(ctx) -> probability`，而非"到点就发"
- **冷却**：沿用 8min global + per-event + 35/50/65/80 四档阈值，参数集中到 `core/scheduler/policy.yaml` 热改
- **分类调度器**：生理节律（period / birthday）/ 被动反应（sensor_aware / watch）/ 主动碎碎念（random_message）各自独立
- **品味基线**：他不是闹钟，他是个会观察她当前状态的陪伴角色，在等合适的时机说话

Phase 2 设计文档另开，预计文件名 `docs/trigger-decision-layer.md`。

---

## 八、风险与未决事项

- **conversation_gate 锁粒度**：当前是 per-uid，触发器经过时是否需要更细粒度（per-trigger-type）？我倾向不需要，因为 short_term 写入本就需要 per-uid 串行。
- **slow_queue 堆积**：触发器集中触发（早上 8 点多个触发器同时到期）会让 slow_queue 临时变长。Phase 1 上线后需要观察队列长度指标；若堆积严重，Phase 2 考虑多 worker 或分队列。
- **DesktopChannel.send 是否支持 behavior**：sensor_aware 现在直推 WS 时附带 action 包，迁移要求 channel 层接收 behavior 并正确转发到 WS / 文件 fallback。如果当前签名不支持，需要先扩 channel 接口，**注意保持客户端协议向前兼容**。
- **`pipeline_registry` 注入**：`record_assistant_turn` 需要 Pipeline 实例触发 post_process，依赖 `pipeline_registry.get()`。调度器路径已有 `_pipeline` 引用，可直接传入；owner 入口同样从 registry 取。
- **沙盒路径**：所有 `data/*.json` 读写仍走 `core/sandbox.get_paths()`，record_assistant_turn 自身不直接落盘（透过 capture_turn / broadcast 间接落盘）。

---

## 九、落地步骤建议

1. 本设计文档 review、产品决策（开放问题 #1-#5）落定
2. 新增 `core/turn_sink.py`，单元测试覆盖 4 种 source + 3 种 fanout
3. 在 `core/scheduler/loop.py::_pipeline_send` 内部接入 `record_assistant_turn`（不改触发器代码）
4. 在 `admin/routers/chat.py::run_owner_chat_turn` 接入；跑 owner 入口回归
5. 改 `core/scheduler/triggers/sensor_aware.py`：删除 `desktop_ws.push_message` 直推
6. 改 `admin/routers/watch.py::_flush_sleep_buffer`：用 `WATCH` source + `trigger_name="sleep_end"`，回写接入 `watch.py:on_watch_event`
7. 按开放问题 #2 决议，决定是否补 garden 事件冷却节流
8. 跑 codex 报告里那张触发器表，逐个触发验证（第六节方案）
9. 生产灰度 1-2 周；观察 slow_queue 长度、capture_turn 重试率、fanout 失败率

---

## 附：与 codex 报告的对照

本文档对 codex 报告里指出的"最显著链路缺口"做了如下回应：

| codex 指出的问题 | 本设计的回应 |
|---|---|
| 没有统一的"assistant turn 完整 hook" | 新增 `record_assistant_turn` 作为唯一汇聚点 |
| 触发器弱于 /desktop/chat（无 gate、post_process 不 await） | conversation_gate 共享 + 默认 await 关键块 |
| trigger 分支只写 assistant 行 | 保持现有契约，本就是预期行为，元数据加全 |
| sensor_aware 绕过 broadcast 直推 WS | 强制走统一 fanout，behavior 由 channel 自行序列化 |
| sleep_end 没传 trigger_name 污染 user 行 | 用 `WATCH` source + `trigger_name="sleep_end"` 修正 |
| garden 事件冷却名挂着但未节流 | 列入开放问题 #2 等决议 |

codex 报告里维护型任务（diary_inject / episodic_decay / episodic_sweep / dlq_monitor / activity_switch / dnd / sleep_report / activity_remind）**不在本 Phase 1 范围**，因为它们不是 assistant turn。
