# Brief 84 · 伪流式统一：Stage 群聊 / 梦境聊天 / coplay 聊天口接打字机回放

> 背景：真流式（token 级）只接在 1v1 owner chat；群聊/梦境/coplay 都是整段
> `push_message`，视觉生硬。裁决：做**服务器端伪流式 helper**，复用 1v1 已有的
> `message_stream_*` 帧契约，前端零新帧类型。

## 现状盘点（省 CC 摸路）

- **真流式**：`pipeline.run_llm_stream()`（`core/pipeline.py:800`）→ `llm_client.chat_stream`
  逐 token → `admin/routers/chat.py:154-182` 经 `ui_push.push_stream_start/delta/end` 推
  WS 帧，结束后 `push_message(canonical, msg_id=同id)` 替换 + HTTP 响应共享 msg_id 去重
  （契约见 `docs/channels.md` §流式）。**只有这一处接线。**
- **QQ 侧伪流式**：`core/output/text_output.py` 多段分条 + 打字间隔——QQ 无流式概念，
  已是最优形态，**本单不动 QQ**。
- **未接流式的口**（全部整段推）：
  - Stage 群聊：`core/stage/runtime.py:51` 直接 `push_message(content, char_id, round_id)`；
    `message_stream_start` 帧的 `char_id?/round_id?` 字段在协议里已预留（`docs/stage.md` §四）。
  - 梦境聊天：`admin/routers/dream.py:108` `dream_chat`。
  - coplay/活动聊天：`admin/routers/coplay.py` / `chess.py` / `gomoku.py` / `reading.py` 中
    返回角色对话文本的接口（CC 落地时逐一确认哪些真的产出对话正文，纯状态接口不接）。

## 1. 🟡 伪流式 helper（一处实现）

`channels/ui_push.py`（或就近模块）新增：

```python
async def pseudo_stream_push(text, *, msg_id, char_id="", round_id="", profile="default") -> None
```

- 行为：`push_stream_start(msg_id, char_id, round_id)` → 按块推 `push_stream_delta` →
  `push_stream_end` →（调用方照旧发 canonical `push_message` 同 msg_id 替换，与 1v1 契约一致）。
- 分块策略：按标点/换行切句，句内 2-6 字一块，块间 30-80ms 随机；总时长上限 ~4s
  （超长文本自动加速，别让一段 800 字回放 20 秒）。参数进 config（`pseudo_stream:` 节，
  含总开关，默认开）。
- fail-open：WS 不在/推送异常 → 直接退化为原有整段 `push_message`，绝不因动画丢消息。
- device_ws 已有 delta 合并背压逻辑（`channels/device_ws.py`），伪流式帧天然兼容，不需改。

## 2. 🟡 三个口接线

- **Stage**：`runtime.py` deliver 处改调 helper（`char_id`/`round_id` 传入）；多角色连续发言时
  **串行回放**（上一条 stream_end 后再开下一条，本来就是逐条 deliver，天然满足）——这正是
  群聊节奏感的主要来源。
- **梦境**：`dream_chat` 生成完成后走 helper；梦境模式下 profile 可配慢一档（气氛）。
- **coplay/活动**：同上，对话正文走 helper；棋步/状态类 payload 不走。

## 3. 🟢 客户端确认（Emerald-client，另立小单）

前端已支持 1v1 的 stream 帧 + canonical 替换 + 3s fallback 去重；需确认群聊气泡/梦境视图/
coplay 视图对带 `char_id`/`round_id` 的 stream 帧的 msg_id→气泡路由。已在
`Emerald-client/cc-tasks/29-伪流式帧多视图路由.md` 出对应单，**后端本单可先行**（fail-open
退化保证旧前端行为不变）。

## 验收

- 1v1 真流式行为零回归（那条链不动）。
- Stage 一轮多角色：帧序 start→deltas→end→canonical 逐条串行，round_id/char_id 正确；
  WS 断开 → 整段消息照常送达（fail-open 断言）。
- 超长文本回放 ≤ 上限时长；config 关闭伪流式 → 行为与现状完全一致。
- `pytest -n auto`；文档：`docs/channels.md` 流式一节补伪流式 helper 与适用面，
  `docs/stage.md` §四 帧表更新。

## Commit 划分

1（helper + config）→ 2（stage 接线）→ 3（dream/coplay 接线）；2、3 可并行，均依赖 1。

## 追认（2026-07-17 复核）

coplay 棋类/阅读「AI 落子后主动评论」（`activity-companion-push` fire-and-forget 事件）
**不接伪流式**——裁决理由：该路评论是 ≤30 字短句，打字机效果在这个长度下不可感（回放
时长上限逻辑同样适用：短文本本就该瞬间完成）；而事件契约本身无增量帧，改造要动跨端协议，
成本/收益不成比例。若未来该路文本变长（>100 字量级）再并入，届时走「事件载荷带完整文本、
前端本地打字机」的客户端方案，不动事件契约。
