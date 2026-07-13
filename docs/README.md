# 文档索引

这里仅收录描述当前系统的文档。带日期的排查、交接和执行快照统一放在
[`archive/`](archive/)；它们可供溯源，不是实现或运行时的真值。

## 入门与全局设计

- [AGENTS.md](../AGENTS.md)：任务入口、强制规则与关键文件速查。
- [ARCHITECTURE.md](../ARCHITECTURE.md)：系统全貌与主 pipeline。
- [DESIGN.md](../DESIGN.md)：设计意图、准入标准与禁止行为。
- [api-reference.md](api-reference.md)：后端 HTTP/WS 端点与调用方。
- [desktop-client-protocol.md](desktop-client-protocol.md)：桌面 v0.1 协议权威入口（正文位于 PresenceKit-desktop）。
- [dev-environment.md](dev-environment.md)：Windows 沙箱开发与验证。
- [known-issues.md](known-issues.md)：当前问题、观察项和技术债。

## 核心运行时

- [channels.md](channels.md)、[scheduler.md](scheduler.md)、[tools.md](tools.md)、[assistant-turn-sink.md](assistant-turn-sink.md)
- [prompt-layers.md](prompt-layers.md)、[model-presets.md](model-presets.md)
- [memory.md](memory.md)、[vector-store.md](vector-store.md)、[data-taxonomy.md](data-taxonomy.md)
- [interaction-event-model.md](interaction-event-model.md)、[stage.md](stage.md)、[dream.md](dream.md)
- [garden.md](garden.md)、[coplay.md](coplay.md)、[intent-grounding.md](intent-grounding.md)

## 活动与设备

- [activity-session.md](activity-session.md)、[reading-activity.md](reading-activity.md)
- [gomoku-activity.md](gomoku-activity.md)、[chess-activity.md](chess-activity.md)
- [presence-device-firmware.md](presence-device-firmware.md)、[perform-mapping.md](perform-mapping.md)

## 安全、运维与发布

- [security.md](security.md)：鉴权、token 与 scope 实现。
- [security_model.md](security_model.md)：风险边界与部署假设。
- [token-rotation.md](token-rotation.md)、[private-content-manifest.md](private-content-manifest.md)
- [fresh-clone-testing.md](fresh-clone-testing.md)、[system-readiness.md](system-readiness.md)
- [test_record.md](test_record.md)：手动测试记录模板。

## 归档

历史快照的目录说明和逐份处置结论见 [archive/README.md](archive/README.md)。
