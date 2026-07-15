# Dream Seed Activity — 梦境预构

## 定位

Dream Seed 是 reality-side `ActivitySession`。用户与角色在睡前共同描述今晚梦的地点、氛围和行动，
关闭活动后将对话提炼为一颗短期种子，供下一次 Dream entry 使用。

## 生命周期

```text
POST /activity/dream_seed/start
  → POST /activity/dream_seed/chat
  → POST /activity/dream_seed/close
  → POST /dream/enter 消费种子
```

- 活动对话只写 `activity/.../dream_seed/.../transcript.jsonl`。
- 不写 `short_term`、`event_log`、`hidden_state` 或 Dream archive。
- close 调用独立 LLM 提炼，写入 reality-scoped `dream_seed.json`。
- 种子 TTL 为 12 小时，入梦时一次性消费。

## 路径

```text
data/runtime/activity/{char_id}/{uid}/dream_seed/{session_id}/
data/runtime/memory/{char_id}/{uid}/dream_seed.json
```

两条路径均按 `char_id + uid` 隔离。种子通过 `resolve_path(reality_scope, "dream_seed")` 解析。

## Dream 接入

`core/dream/dream_context.build_snapshot()` 在入梦构建冻结快照时消费有效种子，并把
`今晚的梦境设定：...` 前置到 `entry_reason`。读取或消费失败只记 warning，不阻断入梦。
