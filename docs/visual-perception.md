# 视觉观测生产者契约

`POST /perception/visual` 是本地客户端向 PresenceKit 提交视觉观测的唯一入口；服务端只接收和做 shadow 分析，不会自行截屏。桌面客户端的截屏/场景变化判断由 Emerald-client 的对应工单实现。

请求为 `multipart/form-data`：

- `image`：一张当前屏幕或相机图片；服务端只在内存中处理，不把原图写盘。
- `source`：`screen` 或 `camera`。
- 鉴权：Bearer token 必须具有 `sensor.write` scope。

处理前置条件与节流：`visual_perception.enabled=true`；视觉连接参数优先取 `visual_perception`，缺失字段继承 `vision`。同一 `source` 成功接收后冷却 5 分钟；冷却或关闭时仍返回 202 与 `accepted=true`，但 `processing=false`。生产者应只在可见场景发生显著变化时发送，不应周期性上传屏幕。

响应 `{"accepted": true, "processing": true|false}`。结果只进入 shadow trace，不注入角色 prompt 或主记忆。管理面以 `GET /perception/visual-trace`（`state.read`）只读查看；trace 仅保存抽取后的场景/活动/置信度/短描述或丢弃原因，保留 30 天。
