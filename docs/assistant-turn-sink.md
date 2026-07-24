# 触发器统一写入与广播 — 设计文档（Phase 1）

> 状态：Phase 1 已落地；本文保留原设计推导，路径按当前 datapath 校准
> 范围：把"他发话已完成"这件事收口到一个函数，让所有入口走同一条写入 + 广播路径
> 不做：触发器决策时机重构、冷却参数热改、调度器分类拆分 —— 全部留给 Phase 2

---

## 一、问题陈述（依据 codex 报告）

当前"他说完一句话"的善后散落在多条链路，质量参差：

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

1. 任意入口产生一句他的话之后，**三处必同时有新条目**：
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

新增模块 `core/turn_sink.py`，作为"他发话已完成"的唯一汇聚点。

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
   - 入 slow_queue：`summarize_to_midterm` / `reflect_to_episodic` / `consolidate_to_identity` / `consistency_check` / `user_profile_update`
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

## 十、Reality 输出 Scrub 架构（R6-B，2026-06-10）

> 本节为 R6-A 审计（2026-06-10）结论的权威文档，由 R6-B 固化为契约。

### 三类出口

| 分类 | 说明 | 处理规则 |
|---|---|---|
| **REALITY_VISIBLE** | 用户可见的现实回复（QQ / desktop / mobile 发出的文字） | 只用 `strip_render_tags` 移除 `<say>` 等展示标签；**保留**动作描写（括号/斜体）以维持对话质感 |
| **REALITY_MEMORY** | 写入 short_term / event_log / mid_term / episodic / identity 的文本 | 必须经过 `scrub_reality_output_text`（移除动作行/旁白行） |
| **DREAM_VISIBLE** | Dream 模式的回复（dream_pipeline 生成） | 不经过任何 reality scrub；dream_pipeline 不导入 `reality_output_scrubber` |

### Scrub 所有权

```
capture_turn（core/memory/fixation_pipeline.py）
  └─ REALITY_MEMORY 权威 scrub 点
     · 上游可能已预清洗，此处必须保留（defense-in-depth）
     · scrub_reality_output_text 幂等，双重 scrub 安全
     · Dream 输出不走此路径

main.py handle_message（QQ 主路径）
  └─ 预清洗 memory_reply → 传给 post_process
     · defense-in-depth，非唯一 scrub 点

main.py _reply_with_tool_result（QQ 工具确认）
  └─ 预清洗 memory_reply → 传给 post_process
     · 同上

turn_sink.record_assistant_turn（desktop/scheduler/sensor/wake）
  └─ 预清洗 memory_text → 传给 post_process
     · defense-in-depth，非唯一 scrub 点
```

### 不变量（由 tests/test_r6b_reality_scrub_contract.py 守卫）

1. `short_term.append` 和 `event_log.append` 在 production core 代码中只从 `capture_turn` 调用。
2. `pipeline.py post_process` 不直接调 `short_term.append` / `event_log.append`，只调 `capture_turn`。
3. `main.py` 不直接调 `short_term.append` / `event_log.append`。
4. Dream 文件（`core/dream/dream_pipeline.py`、`admin/routers/dream.py`）不导入 `reality_output_scrubber`，不调 `capture_turn`。
5. `scrub_reality_output_text` 幂等：`scrub(scrub(x)) == scrub(x)`。
6. `turn_sink._fanout`（REALITY_VISIBLE 路径）只调 `strip_render_tags`，不调 `scrub_reality_output_text`。

### 新增现实记忆出口的规范

> **任何新的现实记忆写入路径必须走 `capture_turn` 或等价的权威 scrub 点。**
> 
> 不允许：直接调 `short_term.append` / `event_log.append` 而不经过 `capture_turn`。  
> 允许（推荐）：把生成文本传给 `pipeline.post_process()`，后者调 `capture_turn`。  
> 允许（备用）：直接调 `capture_turn()`，但必须确保 `reply` 已经过 `scrub_reality_output_text` 或由 `capture_turn` 内部 scrub（默认行为）。

---

## 十一、QQ 主入口 adapter 收口（R1-C → R1-D，2026-06-11）

> R1-C 已完成：两条 QQ LLM 回复出口统一到 `_qq_reality_reply_adapter`。
> R1-D 已完成：adapter 内存写链路接入 `record_assistant_turn`（turn_sink 统一链路）。
> QQ LLM reply 不再是独立手写落库链路，与 desktop/scheduler 共享 turn_sink 统一入口。

### 已完成（含 R1-D）

| 修复 | 包 |
|---|---|
| `await post_process`（不再 create_task） | N10 |
| 轮级 scope freeze + frozen_scope 透传 | N1 |
| `conversation_lock` 串行化 | R1 |
| pre-scrub（turn_sink 内 `scrub_reality_output_text`）+ `strip_render_tags` | R6-A/B |
| `QQChannel.send` 支持 `target_id / is_group`（移除硬编码 False） | R1-C |
| `_qq_reality_reply_adapter` 统一两条 LLM 回复出口 | R1-C |
| adapter 内存写链路接入 `record_assistant_turn`（turn_sink） | R1-D |
| `record_assistant_turn` 新增 `target_id / is_group / pending_paths / frozen_scope` | R1-D |

### QQ 当前路径图（R1-D 后）

```
QQ 消息 → handle_message
  ├── Dream guard (SYSTEM_SHORT_TEXT，_to_dg.send 直发 + return，不写 memory)
  ├── session_state WAITING_CONFIRM/WAITING_INPUT
  │     ├── tool execute
  │     ├── TOOL_CONFIRMATION_PROMPT (text_output.send 直发 + return，不写 memory)
  │     └── → _reply_with_tool_result (LLM_ASSISTANT_REPLY)
  │           └── → _qq_reality_reply_adapter
  ├── media processing (trusted_user_text 保持原始)
  └── conversation_lock(user_id)
        ├── tool probe / fast path
        ├── fetch_context (frozen_scope)
        ├── build_prompt
        ├── run_llm
        ├── response_processor.process
        └── → _qq_reality_reply_adapter(frozen_scope=_frozen_scope)
                ├── strip_render_tags → text_output.send → QQ (REALITY_VISIBLE)
                └── record_assistant_turn(turn_sink, fanout=[], bypass_gate=True)
                      ├── scrub_reality_output_text (defense-in-depth)
                      └── await post_process(frozen_scope, target_id, is_group, pending_paths)
                            └── capture_turn               (REALITY_MEMORY authority)

_reply_with_tool_result (tool confirm LLM reply):
  └── conversation_lock(user_id)
        ├── build_prompt (fetch_context 不重跑)
        ├── run_llm
        ├── response_processor.process
        └── → _qq_reality_reply_adapter(frozen_scope=frozen_scope)
                ├── strip_render_tags → text_output.send → QQ (REALITY_VISIBLE)
                └── record_assistant_turn(turn_sink, fanout=[], bypass_gate=True)
                      ├── scrub_reality_output_text (defense-in-depth)
                      └── await post_process(frozen_scope, target_id, is_group, pending_paths)
                            └── capture_turn               (REALITY_MEMORY authority)
```

### text_output.send 分类（R1-D 后全部 main.py 调用点）

| 分类 | 数量 | 位置 | 是否写 memory |
|---|---|---|---|
| LLM_ASSISTANT_REPLY | 1 | `_qq_reality_reply_adapter`（→ turn_sink） | 是（via record_assistant_turn → post_process → capture_turn） |
| SYSTEM_SHORT_TEXT | 3 | 梦境 guard ×3（_to_dg 别名） | 否（直接 return） |
| SYSTEM_SHORT_TEXT | 1 | 取消确认 `text_output.send` | 否（直接 return） |
| TOOL_CONFIRMATION_PROMPT | 2 | WAITING_INPUT ask_text、probe ask_text | 否（直接 return） |

### 与 run_owner_chat_turn 的对齐（R1-D 后）

| 特性 | QQ（R1-D） | desktop（run_owner_chat_turn） |
|---|---|---|
| 发送机制 | `_qq_reality_reply_adapter` → `text_output.send` 直发 | `turn_sink.record_assistant_turn` → channel fanout |
| memory 写入 | `record_assistant_turn`（turn_sink 统一链路） | `record_assistant_turn`（turn_sink 统一链路） |
| pre-scrub | turn_sink 内（`scrub_reality_output_text`） | turn_sink 内（同上） |
| 其他 channel 广播 | 无（`fanout=[]`，仅 QQ visible send） | 全部注册 channel（`fanout="all"`） |
| QQChannel.send is_group | 群聊路由正确（R1-C 修复） | 通过 fanout 路由 |
| post_process 额外参数 | target_id / is_group / pending_paths / frozen_scope 经 turn_sink 透传 | 不传（使用默认） |
| R6 scrub | turn_sink pre-scrub + capture_turn 权威 scrub | turn_sink pre-scrub + capture_turn 权威 scrub |

### R1-D 已完成（2026-06-11）

`record_assistant_turn` 已扩展接受 `target_id / is_group / pending_paths / frozen_scope`，
并将这些参数透传到 `pipeline.post_process`。`_qq_reality_reply_adapter` 现在调用
`record_assistant_turn(fanout=[], bypass_gate=True, ...)` 完成内存写入。

QQ 仍不经 channel fanout（`fanout=[]`）——QQ visible send 独立完成。未来若需广播到其他
channel，只需改 `fanout="all"`，无需改动 scrub/post_process 链路。

守卫测试：
- `tests/test_r1b_qq_convergence_audit.py`（A10 已翻转，所有项 pass）
- `tests/test_r1c_qq_reality_reply_adapter.py`（R1-C/D 更新，29+ 项）
- `tests/test_r1d_qq_reality_reply_adapter.py`（R1-D 专项，新增）

---

## 十三、Desktop proactive turn 广播一次（2026-07-24）

`_fanout` 里新增一层过滤：`TurnSource != USER_CHAT` 的 turn（trigger / sensor / watch）只有在
`channels.desktop_ws.is_connected()` 为真时才把 `desktop` 留在 fanout targets 里，否则直接摘除，
不再调用 `DesktopChannel.send()`。

动机：`DesktopChannel._fallback_active` 只在 WS 断开时清零，`/desktop/chat` 每次请求都会重新置
`True` 且无 TTL；desktop 长时间未连 WS 时这个陈旧标志仍让 `is_active` 判真，导致 proactive turn
照常写入 `channel_queue.json`，桌宠端下次打开时把积压消息全部当"刚收到"补发，与实际发生时间脱节。

修复后语义：proactive turn 对桌面是"广播一次，打不进就静默丢弃"——turn 仍然正常写入
memory/event_log/history（走 `capture_turn`，与 channel fanout 无关），只是不会晚点从队列补投到
聊天窗口。USER_CHAT 回显不受影响；WS 在线时因瞬时故障导致 `push_message` 失败，仍走
`channel_queue.json` 短时兜底。

详见 `docs/channels.md` §桌宠 WebSocket。测试：
`tests/test_turn_sink.py::test_proactive_turn_does_not_queue_to_desktop_file_when_disconnected`。

## 十四、Mobile durable fallback 扩展到 USER_CHAT（2026-07-24）

原本只有 trigger/sensor/watch（`_is_proactive`）才会无条件把 `mobile` 补进 fanout targets；
USER_CHAT 回复要不要进 `mobile_queue.json` 完全看当时 `mobile.is_active`（120s TTL）。这导致"发
完消息立刻切后台"这种 QQ/微信式场景不可靠——如果 TTL 恰好在回复生成期间过期，回复就不会落进
mobile 队列，后台也就没有横幅可弹。

现在这段 `if exclude_origin_channel != "mobile": targets.append(mobile_ch)` 对所有 source 无条件
生效，不再区分 `_is_proactive`。之所以安全、不会导致前台重复弹通知：手机与桌宠共用
`/desktop/chat`（`channel_name` 恒为 `"desktop"`，从不是 `"mobile"`，见
`admin/routers/chat.py:528`），而 PresenceKit-mobile 的 `MainActivity.onResume()` 会把
`MobileNotificationService`整个停掉——App 在前台时压根没有东西在监听中继信号，消息只是安静地
躺在队列里，跟现有的前台 `/mobile/poll` 轮询一起被正常消费，不会额外弹窗。

测试：`tests/test_turn_sink.py::test_user_chat_reaches_offline_mobile_queue`。

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

---

## 十二、R6 final — Reality 输出 Scrub 单出口稳态（R1-D 更新，2026-06-11）

> **状态：final / stable**。R6 full single-exit convergence 于 R1-D 完成后确立（2026-06-11）。
> QQ 路径已接入 `record_assistant_turn`，全路径共享 turn_sink 统一链路。

### REALITY_MEMORY authority 链（当前完整路径，R1-D 后）

```
QQ 消息（普通 LLM 回复）:
  handle_message
    └─ conversation_lock(user_id)
         └─ run_llm → response_processor.process
              └─ _qq_reality_reply_adapter(frozen_scope)
                   ├─ strip_render_tags → text_output.send      (REALITY_VISIBLE)
                   └─ record_assistant_turn(turn_sink, fanout=[], bypass_gate=True)
                        ├─ scrub_reality_output_text             (defense-in-depth)
                        └─ await post_process(frozen_scope, target_id, is_group, ...)
                             → capture_turn                      (REALITY_MEMORY authority)

QQ 工具确认回复（tool-result LLM 回复）:
  _reply_with_tool_result
    └─ conversation_lock(user_id)
         └─ run_llm → response_processor.process
              └─ _qq_reality_reply_adapter(frozen_scope)
                   ├─ strip_render_tags → text_output.send      (REALITY_VISIBLE)
                   └─ record_assistant_turn(turn_sink, fanout=[], bypass_gate=True)
                        ├─ scrub_reality_output_text             (defense-in-depth)
                        └─ await post_process(frozen_scope, target_id, is_group, ...)
                             → capture_turn                      (REALITY_MEMORY authority)

desktop / mobile / scheduler / sensor / wake:
  record_assistant_turn (turn_sink)
    ├─ strip_render_tags → channel fanout                       (REALITY_VISIBLE)
    └─ scrub_reality_output_text
        → await _pipeline.post_process
            → capture_turn                                       (REALITY_MEMORY authority)
```

### 系统短文本（不写 memory，已确认安全）

| 分类 | 位置 | 是否写 memory |
|---|---|---|
| SYSTEM_SHORT_TEXT — Dream guard ×3 | main.py `handle_message` | 否（`_to_dg.send` 直发 + return） |
| SYSTEM_SHORT_TEXT — 取消确认 | main.py `handle_message` | 否（直发 + return） |
| TOOL_CONFIRMATION_PROMPT — ask_text ×2 | main.py WAITING_INPUT / probe | 否（直发 + return） |

这些均为硬编码系统短文本，不是 LLM 生成，不需要 scrub，不写 memory。

### QQ adapter + turn_sink 双收口（R1-D 后）

`_qq_reality_reply_adapter` 是 QQ 侧 LLM_ASSISTANT_REPLY 的唯一出口：
- `handle_message`（普通回复）和 `_reply_with_tool_result`（工具确认回复）均调用此 adapter；
- adapter 内部（Brief 34 §4 顺序反转，2026-07-08）：`record_assistant_turn`（turn_sink）
  → `post_process` → `capture_turn` **先**完成记忆写入，**再** visible strip → QQ send；
  拍板：轮次完整性 > 投递确认，send 失败时记忆已写入，不做补偿删除；
- `main.py` 不再有任何 `_pipeline.post_process` 直接调用；
- 不存在绕过 adapter 的第三条 LLM 回复出口。

### 守卫测试

| 测试文件 | 范围 |
|---|---|
| `tests/test_r6_reality_scrub_audit.py` | R6-A 审计（25 项） |
| `tests/test_r6b_reality_scrub_contract.py` | R6-B 契约门禁（17 项） |
| `tests/test_r1c_qq_reality_reply_adapter.py` | R1-C/D adapter 合约（29+ 项） |
| `tests/test_r1d_qq_reality_reply_adapter.py` | R1-D turn_sink 专项（新增） |
| `tests/test_r6c_reality_scrub_final.py` | R6-final 稳态确认（R1-D 更新） |
