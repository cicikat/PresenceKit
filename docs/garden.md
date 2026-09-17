# docs/garden.md — 花园系统

---

## 定位

花园是一个独立于对话 prompt 的情绪伴生系统：调度器按当前 `mood_state` 给对应花槽自动浇水，角色也可通过内部工具维护花园；相关事件再通过调度器让他自然提一句。Desktop 和 Mobile 仅查看或刷新状态，不提供玩家写操作。

当前花园状态**不会直接注入 prompt**；只有浇水工具结果、开花、采后处理、花瓶枯萎等事件会变成一次普通调度器消息。

---

## 代码入口

| 功能 | 文件 |
|---|---|
| 花园核心逻辑 | `core/garden/manager.py` |
| 花种、阶段、概率常量 | `core/garden/constants.py` |
| 数据路径 | `core/sandbox.py` → `DataPaths.garden()` |
| 角色内部浇水工具 | `core/tools/garden_tools.py` |
| 工具注册 | `core/tool_dispatcher.py` → `water_garden` |
| 自动浇水触发器 | `core/scheduler/triggers/garden_water.py` |
| 每日采后扫描触发器 | `core/scheduler/triggers/garden_daily.py` |
| 调度器注册 | `core/scheduler/loop.py` |
| 管理面板状态接口 | `admin/routers/garden.py` |
| 路由挂载 | `admin/admin_server.py` |

---

## 数据文件

路径统一走 `get_paths().garden()`，生产环境位于
`data/runtime/characters/{char_id}/garden/`，测试模式会整体偏移到
`data/test_sandbox/{session}/runtime/characters/{char_id}/garden/`。

| 文件 | 内容 |
|---|---|
| `plants.json` | 五个花槽当前状态：花种、阶段、growth、播种/浇水/开花时间 |
| `storage.json` | `harvest` / `vase` / `history`，保存开花后的收获、花瓶和历史记录 |

初次读取或浇水时，`_bootstrap()` 会自动创建五个槽位和空仓库。

---

## 生长机制

五个槽位按情绪映射：

| 槽位 | 花 | mood |
|---|---|---|
| `calm` | 雏菊 | `neutral` / `gentle` |
| `bright` | 向日葵 | `happy` / `surprised` |
| `low` | 蓝铃 | `sad` |
| `yandere` | 红玫瑰 | `yandere` / `angry` |
| `adrift` | 蒲公英 | `thinking` / `sleepy` |

每次浇水 `growth += 10`。阶段阈值：

| stage | growth |
|---|---|
| `seed` | 0 |
| `sprout` | 100 |
| `budding` | 200 |
| `bloom` | 300 |

到达 `bloom` 时，当前花进入 `storage.harvest`，槽位立即重新播种；返回结果里会带 `events: [{"type": "bloom", ...}]`。

---

## 浇水路径

### 自动浇水

`garden_water` 冷却 300 分钟；触发后有 30% 概率命中。

```
_check_garden_water()
  → _is_ready("garden_water")
  → _mark("garden_water")
  → garden_manager.auto_water_tick()
      → mood_state.get_current()
      → mood 映射到 slot_key
      → water(slot_key, reason="auto")
```

自动浇水本身不发言；开花事件先进入短期事件缓存。当前 `EXECUTE_MODE="live"` 时由
`propose_garden_bloom()` 报名，gating 选中后经统一 `execute_prompt()` 调用
`_pipeline_send(..., trigger_name="garden_bloom")`。

### 角色内部浇水工具

`water_garden` 注册为 `info` 类工具，供角色在对话上下文中决定维护花园时使用；它会被 pre-pipeline 探针覆盖，但不是普通客户端的玩家操作：

- 相关上下文：花园状态、角色是否已维护花园
- 关键词：`浇花`、`花园`、`浇水`
- 实现：`core/tools/garden_tools.py` → `garden_manager.force_water()`

工具按当前心情选择槽位，返回一段状态描述给 LLM，最终由他自然回复。Desktop / Mobile 的花园页始终只读；浇水及其他写操作仅来自角色内部工具、自动调度或受控状态机。

---

## 每日采后扫描

`garden_daily` 冷却 24 小时，负责处理 `storage.harvest` 和 `storage.vase`：

| 事件 | 条件 | 状态变化 | 发言策略 |
|---|---|---|---|
| `harvest_expired` | `now > expires_at` | 从 `harvest` 移到 `history`，标记 `expired` | 30% sample |
| `harvest_handle` / `ask` | 开花超过 3 天且未处理 | 从 `harvest` 移除，追加进 `history`（`kind="ask"`） | 不发言（G4） |
| `harvest_handle` / `dry` | 随机处理分支 | 从 `harvest` 移除，追加进 `history`（`kind="dry"`） | 不发言（G4） |
| `harvest_handle` / `vase` | 随机处理分支 | 进入 `vase`，从 `harvest` 移除 | 30% sample |
| `harvest_handle` / `gift` | 随机处理分支 | 从 `harvest` 移除，追加进 `history`（`kind="gift"`），写 `gifted_note` | 必走 `_pipeline_send`，但仍受用户活跃窗口影响 |
| `harvest_handle` / `silent` | 随机处理分支 | 只标记已处理，仍留在 `harvest` | 不发言 |
| `vase_wilted` | `now > wilts_at` | 从 `vase` 移到 `history`，标记 `wilted` | 30% sample |

处理概率：

- `ask`: 0.00-0.30
- `dry/vase`: 0.30-0.60
- `gift`: 0.60-0.80
- `silent`: 0.80-1.00

### 采后容器：dry/gift/ask 统一归宿（G4，Brief 83）

`ask` / `dry` / `gift` 三种采后处理的最终产物统一追加进 `storage.json` 的 `history`
列表（不新建容器、不新建 schema 顶层键），条目结构：

```json
{"kind": "dry|gift|ask", "flower": "<flower_id>", "mood_source": "<mood_key>", "ts": 0.0, "note": "..."}
```

`mood_source` 取该花种 `mood_keys` 的第一项（花种与情绪槽位固定映射，非当时实时情绪快照）。
三者处理完成后立即离开 `harvest`（此前只有 `vase` 会离开，`ask`/`dry`/`gift` 会一直滞留到
15 天后被 `harvest_expired` 误标为 `expired`，真实处理结果丢失——已在此工单修掉，见「当前边界」旧条目 3）。

只有 **gift** 保留主动消息（走既有 `propose_garden_handle_gift` proposer + `_pipeline_send`，
完整受 QUIET 状态机 / DND / 冷却 / `ProactiveLedger` gating，不是绕过账本的直发）；
`ask` 与 `dry` 不再产生任何调度器消息，`garden_handle_self` proposer 收窄为只覆盖 `vase` 分支。
`ask`/`dry`/`gift` 的处理结果改为通过 `GET /garden/state` 的 `history_recent`（最近 1 条
`history`）被动露出，不主动打扰。

---

## 客户端接口

`GET /garden/state`

需要管理面板 token，返回：

- `slots`：五个花槽的展示数据，含 `stage_progress`
- `harvest_count`：收获区数量
- `vase_count`：花瓶数量
- `history_recent`：`history` 列表最近 1 条（可能为空列表），G4 花园 presence 提示素材，
  免新建接口（Hard Rule 7）

接口只读取和必要时初始化状态，不执行浇水。Desktop / Mobile 只使用该读取能力和本地刷新；浇水及其他写操作仅来自角色内部工具、自动调度或受控状态机。

---

## 当前边界

1. 写入目前使用普通 `Path.write_text()`，没有接入 `safe_write` 或锁；现在已有 `garden_water`、`garden_daily`、`water_garden` 三条写路径，后续最好补 garden 专用锁。
2. `garden_bloom`、`garden_handle_*`、`garden_vase_wilted` 已有原生 proposer 和独立冷却；
   事件进入缓存后由 gating 每 tick 最多选择一个，只有真实发送成功才 mark。`garden_water` /
   `garden_daily` 扫描本体仍按原冷却执行状态变化。
3. ~~`ask` / `gift` / `dry` 分支会标记 `handle_triggered`，其中只有 `vase` 会从 `harvest` 移除~~
   已在 Brief 83（G4）修复：三者处理完成后立即离开 `harvest`，落 `history` 记录，详见上方
   「采后容器：dry/gift/ask 统一归宿」。
