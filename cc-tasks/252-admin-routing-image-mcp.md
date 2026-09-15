# 工单 252：管理面路由 / 图像连接 / 思考开关 / MCP 卡顿

日期：2026-09-15。设置只改后端管理面板与对应运行时解析，不往桌面客户端塞新开关。

## 背景（查代码后）

- Routing Profile 编辑弹窗把已有名称 `disabled`，没有 rename/delete API；空 category 回退 `chat` → 第一个 preset。
- `rpg_kp` 已在 `model_registry` 与 RPG Dream 使用，管理面 `MR_CATEGORIES` 未暴露。前置独白已有 `monologue` 用途，但说明不够显眼。
- 图像「Presets」实际是写死的 `vision:` / `image_recognition:` 两行，用途表不能自选连接。
- 思考卡把开关和选项交错排成 `label.field`；功能开关总览用的是 `admin-toolbar` + `checkbox-row`。
- MCP 总开关 `PATCH /settings/mcp` 会 `await sync_mcp_servers()`（等每台连上）；折叠/展开走整页 `loadMcpPage()`，先刷「加载中」再重建 DOM，并给每个工具打一次调用记录。

---

## 工单 1 — Routing Profile 改名 / 删除 + 默认 preset

### 要做

- [ ] `POST /model-presets/routing-profiles/{name}/rename`：原子改名；同步 `active_routing`；扫描角色卡 `presence_ext.model_routing` 并改写。
- [ ] `DELETE /model-presets/routing-profiles/{name}`：不能删最后一个；若删的是当前生效方案则切到剩余方案（优先名为 `default` 的）；角色卡绑定清除为跟随全局。
- [ ] 管理面：编辑时名称可改；列表有删除；保存时若改名先 rename 再写映射。
- [ ] `model_presets.default_preset`：当前生效路由方案卡片下增加默认 preset 下拉；未选 / 新建 profile 未填的 category 解析到它，再回退 chat → 第一个 preset。
- [ ] 删除/重命名文本 preset 时同步更新 `default_preset`；仍被 default 引用则拒绝删除。
- [ ] PUT profile 允许把某 category 设为空字符串以清除映射（走默认）。

### 验收

- 定向测试：rename/delete/default_preset 解析与角色卡改写。
- 浏览器：改名、删除、切默认 preset 后列表与生效方案一致。

---

## 工单 2 — `rpg_kp` 与前置独白进入 Routing Profiles

依赖工单 1 的编辑器。

### 要做

- [x] `MR_CATEGORIES` 增加 `rpg_kp`（RPG Dream 中立裁决，缺省回退 chat）；说明写进弹窗和「各 category 是干什么的」。
- [x] `sensor_judge` 一并进编辑器（ime_judge 回退链真实用到，现在只能手改 yaml）。
- [x] `monologue` 文案标明「前置独白 / 额外思考链」，与思考卡 hint 对齐。
- [x] `GET /model-presets` / routing-profiles 的 effective 摘要带上 `rpg_kp`。
- [x] `config.example.yaml` 示例 profile 补 `rpg_kp`。

### 验收

- UI 源码合同测试含 `rpg_kp`；解析回退不变（缺省 → chat）。

---

## 工单 3 — 图像连接 CRUD，用途自选 preset

### 要做

- [x] 新增 `image_presets.presets`（命名连接，含 `kind: vision|ocr`）与 `image_presets.routes`（chat_upload / life_diet / life_cart / life_bill / phone_automation）。
- [x] 无新块时从现有 `vision:` + `image_recognition:` + `phone_control_vision` 合成，不改运行语义。
- [x] 管理面：图像 Presets 有新建 / 编辑 / 删除 / 测试，名称不锁死；Routing · 图像用途每行自选连接。
- [x] 运行时：`media_processor` 聊天图、`life_records` 三类、`phone_control` 视觉按 routes 解析；旧 `vision:` / `image_recognition:` 仅作无新块时的回退。
- [x] 删除仍被用途引用的连接 → 409。测试接口接受连接名，保留 general/ocr/phone 别名。

### 验收

- 图像连接/用途定向测试 + 生活记录路由测试。
- 浏览器：新建连接、改用途、删除被引用时拒绝。

---

## 工单 4 — 系统 prompt：过时生活数据必须确认

可与 1–3 并行。

### 要做

- [x] 在 `1.5_fact_boundary` 加一句：手机电量、步数、餐食、行为等都要确认事件，不要把过时信息当成当前信息。
- [x] 更新 `docs/prompt-layers.md` 与既有 fact_boundary 测试。

### 验收

- `tests/test_prompt_avatar_identity_anchor.py` 断言新句存在。

---

## 工单 5 — 思考开关对齐功能开关总览

可与 4 并行；同页于工单 1/2。

### 要做

- [x] 开关（生成思考 / 角色心声 / 应用于主动消息）用 `admin-toolbar` + `checkbox-row`，同类放一起。
- [x] 选项（方式 select、独白预算）单独一组 `field`，不要夹在开关中间。
- [x] 仍走 `GET/POST /settings/thinking`，保存按钮保留（不像总览那样逐项 PATCH）。

### 验收

- 浏览器：开关与选项分组可见；保存后回读一致。

---

## 工单 6 — MCP 启用卡顿与展开闪动

可与 1–5 并行。

### 要做

- [ ] 折叠/展开只改当前卡片 DOM + localStorage，禁止为此 `loadMcpPage()`。
- [ ] `loadMcpPage` 已有内容时不要先刷成「加载中」；刷新尽量保持滚动位置。
- [ ] `PATCH /settings/mcp` 写盘并发信号后即返回，不把每台 MCP 连上作为 HTTP 完成条件；前端用返回体更新，不再整页 loading。
- [ ] 工具调用记录仍仅在首次进入/手动刷新时拉，展开不重打。

### 验收

- UI 合同：collapse 不再调用 `loadMcpPage`。
- 浏览器：勾选总开关、展开工具列表不再整页跳。

---

## 顺序

1 必须先做（2 改同一编辑器）。3 独立但页面同文件，1 提交后再做。4、5、6 可与 3 穿插；5 动思考卡 DOM，1 提交后做更安全。

每完成一张工单：相关测试通过 → 独立 commit。
