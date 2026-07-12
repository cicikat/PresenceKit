# docs/stage.md — Multi-Character Stage

> 状态：P3 reality Stage 已实现；dream Stage 仍 fail-closed，等待 dream-local 多角色状态适配。

Stage 是多角色群聊的一等 session 实体。它持有在场角色、群设置和共享 transcript，并负责在 owner
发言后串行决定谁说话。

---

## 一、当前实现

| 能力 | 文件 |
|---|---|
| Stage / Settings / TranscriptEntry 数据模型 | `core/stage/models.py` |
| Stage 与共享 transcript 持久化 | `core/stage/store.py` |
| 纯规则 want-to-speak 仲裁 | `core/stage/arbiter.py` |
| 一整轮 Phase A + Phase B 编排 | `core/stage/runner.py` |
| per-character 只读生成视图 | `core/stage/views.py` |
| 群在场感 / transcript prompt 渲染 | `core/stage/context.py` |
| reality 群记忆投影 | `core/stage/projection.py` |
| reality Stage 运行入口 | `core/stage/runtime.py` → `run_reality_stage_turn()` |
| 沙盒路径 | `core/data_paths.py` → `stage_group_dir()` / `stage_meta()` / `stage_transcript()` |

持久化布局：

```text
data/runtime/groups/{group_id}/
├── meta.json
├── transcript.json
└── arbiter_trace.jsonl
```

虽然物理路径位于 `runtime/`，两份文件是当前 Stage session 的共享真值，数据治理登记为
`canonical / shared / per_group`。所有路径必须通过 `get_paths()` 获取。

---

## 二、运行合同

`run_owner_turn()` 是 P2 的最小可调用入口：

1. 读取 active Stage，追加 owner 发言。
2. 在一次 `conversation_lock(stage.owner_uid)` 内完成整轮。
3. Phase A 对未发言角色逐条重算，产生 `min_responders..max_responders` 条直接回应。
4. Phase B 每条后重算，最多产生 `max_ai_chain_depth` 条自主续聊。
5. 每条有效回复先追加共享 transcript，再调用可选 delivery callback。

生成由调用方提供 `generate_reply(stage, speaker_id, transcript, turn_id, triggered_by)` callback。
它可以同步或异步返回文本。delivery callback 同样支持同步或异步。

共享 transcript 每条包含：

```text
speaker_id / content / timestamp / _turn_id / triggered_by
```

`speaker_id` 只能是 `owner` 或 Stage roster 内的 `char_id`。同一 Stage turn 的所有发言共用
`_turn_id`。

---

## 三、P3 Reality 接入

`run_reality_stage_turn()` 串起 P2 runner、per-character view、跨通道发送与回合后投影：

- 每个 `speaker_id` 对应一个缓存的 `StageCharacterView`，显式加载自己的角色卡、LoreEngine 和
  `MemoryScope.reality_scope(owner_uid, speaker_id)`，不热切换全局 active character。
- 角色自己的长期/近期记忆仍由现有 `Pipeline.fetch_context()` 读取；共享 transcript 通过
  `2.2_stage_presence` 与 `4.2_stage_transcript` 两个独立 prompt 层注入。
- Stage 回复通过 `channels.registry.broadcast(..., char_id=speaker_id)` 发送，旧客户端仍可忽略字段。
- 每轮完成后，未投影 transcript 按 roster 逐角色入 `summarize_to_midterm` slow queue；
  输入保留说话人名前缀，且按本段的发言/被点名次数计算 `memory_strength`（0.4–0.9）。
  mid-term 记录仍携带 `source="group:{group_id}"`，后续由原 fixation pipeline 晋升。
- 同轮 AI↔AI 的直接接话会异步更新全局双向关系记录；冷却 6h，关系仅作为 presence 提示，
  不参与仲裁打分，也绝不存 owner↔角色关系。
- `projection_cursor` 保证投影幂等；transcript 裁剪时游标同步回退，避免漏掉后续新消息。
- scheduler cooldown 支持显式 `char_id` 键。统一执行层双写角色键与旧全局键，旧触发器兼容，
  新的多角色 proposal 可按角色隔离。

## 四、HTTP API（`/group/*`）

挂载于 `admin/admin_server.py`，prefix=`/group`，全部要求 Bearer token 鉴权。

| 方法 | 路径 | 描述 |
|---|---|---|
| `GET` | `/group/list` | 列出所有 Stage 群 |
| `POST` | `/group/create` | 建群（roster + domain + settings）；settings 缺省时从 `group_defaults` config 读 |
| `GET` | `/group/{id}` | 取群详情（roster + settings + 近 50 条 transcript） |
| `DELETE` | `/group/{id}` | 硬删除群 + transcript；正在跑的轮次下轮 load 不到自然结束 |
| `POST` | `/group/{id}/send` | 触发 arbiter 一轮，立即返回 `{round_id, status:"accepted"}`，整轮经 WS 异步推送 |
| `GET` | `/group/{id}/history?before=` | 分页 transcript，`before` 为 Unix 时间戳上界（可选） |
| `GET` | `/group/{id}/arbiter-trace?limit=` | 倒序读取最近仲裁分项与选择记录 |
| `GET` | `/group/{id}/settings` | 读群设置 |
| `PATCH` | `/group/{id}/settings` | 部分更新群设置（min/max_responders 等） |
| `PATCH` | `/group/{id}/roster` | 改群成员（`{roster:[char_id,…]}`）；max_responders 自动夹紧到新 roster 长度 |

`POST /group/{id}/send` 触发 `run_reality_stage_turn()` 作为异步 task，不阻塞 HTTP 返回。

### WS 群聊帧（`channels/desktop_ws.py`）

| 帧 | 字段 | 触发时机 |
|---|---|---|
| `group_round_start` | `round_id, group_id` | 每轮 Phase A 开始前 |
| `group_round_end` | `round_id, group_id` | Phase B 完成后 |
| `channel_message` | `content, msg_id, char_id, round_id` | 每条角色回复 |
| `message_stream_start` | `msg_id, char_id?, round_id?` | 流式开始（1v1 路径，群聊保留字段） |

群聊 deliver 路径绕过 `channels.registry.broadcast`：
- **desktop WS**：直接调用 `push_message(content, char_id=..., round_id=...)`；
- **其他通道**（mobile、QQ）：经 `registry.get_active()` 轮询发送，无 `round_id`（v1 无群聊 UI）。

## 五、当前边界

- Stage 不热切换全局 active character；角色 view 只复用 Pipeline 的读与生成步骤，不调用
  `post_process()`。
- 原始共享 transcript 不写 short_term / mid_term / episodic / identity；只有摘要投影通过
  fixation pipeline 入链。
- Stage 不进入 `perceive_event`；它是由入口显式创建、关闭和驱动的 session。
- Stage `domain="dream"` 仍拒绝进入 reality view / projection。现有 dream state、body tracker、
  dream log 与 exit afterglow 均绑定单角色；在这些状态改为 per-character dream view 前，不允许
  用 reality 适配器伪装梦境群聊。
- proactive 群触发与 think-delay 尚未接入；v1 主动群触发按规格保持关闭。

接入现有 Pipeline 前，必须先构造显式的 per-character view，保证角色卡、prompt scope 和记忆 scope
都由 `speaker_id` 决定，不能依赖全局 active character。
