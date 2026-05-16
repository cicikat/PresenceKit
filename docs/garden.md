# docs/garden.md — 花园系统

---

## 定位

花园是一个独立于对话 prompt 的情绪伴生系统：调度器按当前 `mood_state` 给对应花槽自动浇水，管理面板读取当前花园状态展示。

当前它**不会注入 prompt**，也不会主动发消息；它更像一层可视化的长期情绪痕迹。

---

## 代码入口

| 功能 | 文件 |
|---|---|
| 花园核心逻辑 | `core/garden/manager.py` |
| 花种、阶段、概率常量 | `core/garden/constants.py` |
| 数据路径 | `core/sandbox.py` → `DataPaths.garden()` |
| 自动浇水触发器 | `core/scheduler/triggers/garden_water.py` |
| 调度器注册 | `core/scheduler/loop.py` |
| 管理面板状态接口 | `admin/routers/garden.py` |
| 路由挂载 | `admin/admin_server.py` |

---

## 数据文件

路径统一走 `get_paths().garden()`，生产环境位于 `data/garden/`，测试模式会落到 `data/test_sandbox/{session}/garden/`。

| 文件 | 内容 |
|---|---|
| `plants.json` | 五个花槽当前状态：花种、阶段、growth、播种/浇水/开花时间 |
| `storage.json` | 收获、花瓶、历史记录；当前只写入 `harvest` |

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

到达 `bloom` 时，当前花会进入 `storage.harvest`，槽位立即重新播种。

---

## 自动浇水

`core/scheduler/loop.py` 每 60 秒调度一次所有触发器，`garden_water` 自身冷却为 30 分钟。

执行流程：

```
_check_garden_water()
  → _is_ready("garden_water")
  → _mark("garden_water")
  → garden_manager.auto_water_tick()
      → 30% 概率命中
      → mood_state.get_current()
      → mood 映射到 slot_key
      → water(slot_key, reason="auto")
```

管理面板手动触发列表目前没有覆盖 `garden_water`。

---

## 管理面板接口

`GET /garden/state`

需要管理面板 token，返回：

- `slots`：五个花槽的展示数据，含 `stage_progress`
- `harvest_count`：收获区数量
- `vase_count`：花瓶数量

接口只读取和必要时初始化状态，不执行浇水。

---

## 当前边界

1. `HARVEST_HANDLE_SECONDS`、`VASE_WILT_SECONDS` 和处理概率常量已定义，但采后处理、花瓶枯萎、赠送逻辑还没有实现。
2. 写入目前使用普通 `Path.write_text()`，没有接入 `safe_write` 或锁；当前只有调度器一个自动写入口，若未来开放工具/接口浇水，需要先补并发保护。
3. 花园状态不进 prompt。如果要让叶瑄在对话中自然提起花园，需要新增 prompt 层或工具召回，并明确门控条件。
