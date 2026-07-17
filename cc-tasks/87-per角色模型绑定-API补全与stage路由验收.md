# Brief 87 · per-角色模型绑定：API 补全 + Stage 路由验收（机制已存在，补面子和验证）

> 核实结论（写单时已查代码）：**后端机制早已存在**——角色卡 `presence_ext.model_routing`
> 指向 `config.model_presets.routing_profiles` 里的 profile 名（Brief 29/30），
> `core/model_registry.py::_char_model_routing()` 按显式 char_id 解析（只读该角色自己的卡，
> 不回落活跃角色），Stage 生成也已传 char_id（`core/stage/views.py:105
> run_llm(messages, char_id=self.char_id)`）。「群聊统一用默认」的实际原因：角色卡没声明
> 该字段 + 没有绑定 UI。本单不造新机制，只补 API、验收和 UI（UI 在 client 侧 30 单）。

## 1. 🟡 绑定 API（写角色卡 presence_ext）

- `admin/routers/character.py` 加：
  - `GET /character/{char_id}/model-routing` → `{model_routing: str|null, effective_profile,
    resolved_chat_preset}`（把解析结果也回给前端，绑定后立刻可见「实际会用哪个 preset」）。
  - `PATCH /character/{char_id}/model-routing`，body `{model_routing: str|null}`；
    null = 清除声明（回默认 active_routing）。
  - 校验：非 null 值必须存在于 `routing_profiles`，否则 422（character_loader 注释已言明
    「存在于 routing_profiles 才生效」，API 层把它变成显式错误而不是静默失效）。
- 可选 profile 清单：`admin/routers/settings_llm.py` 补
  `GET /model-presets/routing-profiles`（名字 + 各 category→preset 映射摘要），
  前端下拉框数据源。
- 角色卡是 authored 资产：写入走 character_loader 的既有保存路径（保持字段顺序/其余
  presence_ext 键不动），写后触发其缓存边沿失效（loader 已有热路径缓存，确认 reload 生效）。

## 2. 🟡 Stage per-角色路由验收（真正的痛点验证）

- 单测：两个角色卡分别声明不同 `model_routing` → `_resolve_preset_name("chat", char_id=A/B)`
  解析出不同 preset 名；未声明的第三角色 → 回落 active_routing。
- Stage 集成断言：`StageCharacterView` 生成路径最终到 `llm_client` 的 preset 解析按
  speaker 的卡走（mock 到 preset 名层即可，不真调 LLM）。**若发现链路上哪里丢了 char_id，
  顺手修并补回归测试**——这是本单的核心验收，UI 是其次。
- 顺带：85 的 Phase B 轻量视图与 §3 短反应 prompt 也要走同一路由（同 speaker 同 preset，
  不因轻量而降级到默认——除非未来专门给短反应配便宜 preset，那是后话，本单不做）。

## 3. 🟢 说明（不做项）

- 不做「绑定单个 preset」的新粒度：绑定对象就是 routing profile（一个 profile 天然覆盖
  chat/intent/probe 等 category 的整套映射；「单机非角色扮演 preset」这类需求 = 建一个
  profile，把 chat 指到那个 preset，然后绑给对应角色）。想要新组合就在 config 里加
  profile——profile 的可视化编辑不在本单（避免把 config 编辑器搬进设置页）。
- 群设置（per-group override）不做：绑定跟角色走，跨群一致，符合「角色是稳定实体」。

## 验收

- PATCH 合法/非法 profile 的 200/422；null 清除后解析回默认。
- §2 两条路由断言绿；写卡后 loader 缓存边沿刷新生效（改完即用，不用重启）。
- `pytest -n auto`；文档：`docs/model-presets.md` 路由解析一节补 API 指针，
  `docs/feature-control-surface.md` 补该设置入口（AGENTS.md 规则：改控制面必须同步）。

## 配套

前端设置界面见 `Emerald-client/cc-tasks/30-设置页角色模型绑定.md`（依赖本单 API，后端先行）。
