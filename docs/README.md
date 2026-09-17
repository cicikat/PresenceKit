# 文档索引

这里仅收录描述当前系统的文档。三仓查找入口请先看
[三仓文档总索引](three-repo-doc-index.md)；它按功能列出后端、桌面和手机端的权威文档、
支撑文档与历史文档。

带日期的排查、交接和执行快照可供溯源，不是实现或运行时的真值；历史文档的当前目录说明见
[user-teach/README.md](user-teach/README.md)。

## 入门与全局设计

- [AGENTS.md](../AGENTS.md)：任务入口、强制规则与关键文件速查。
- [ARCHITECTURE.md](../ARCHITECTURE.md)：系统全貌与主 pipeline。
- [agent-runtime-architecture.md](agent-runtime-architecture.md)：Brief 229 Agent Runtime 总体分层、同一角色 foreground 主链 / durable 副链语义、身份/realm 边界与现有组件迁移映射（后续实现目标，不代表当前能力）。
- [DESIGN.md](../DESIGN.md)：设计意图、准入标准与禁止行为。
- [api-reference.md](api-reference.md)：后端 HTTP/WS 端点与调用方。
- [three-repo-interface-catalog.md](three-repo-interface-catalog.md)：三仓 HTTP、WS、Tauri IPC、Android channel、relay、设置/观测闭环和当前缺口总账。
- [backend-integration.md](backend-integration.md)：desktop / mobile 共用的请求体字段契约（如 reply_to）。
- 桌面 v0.1 消息细节：`Emerald-client/docs/protocol-v0.md`；后端实现指针见
  [desktop-client-protocol.md](desktop-client-protocol.md)。
- [dev-environment.md](dev-environment.md)：Windows 沙箱开发与验证。
- [testing-matrix.md](testing-matrix.md)：后端自动化测试、评测脚本、CI 范围与发布验收矩阵。
- [known-issues.md](known-issues.md)：当前问题、观察项和技术债。
- [docs-truth-census.md](docs-truth-census.md)：三仓文档导航清单与漂移分类；它不是代码实现 authority。

## 核心运行时

- [channels.md](channels.md)、[scheduler.md](scheduler.md)、[tools.md](tools.md)、[assistant-turn-sink.md](assistant-turn-sink.md)、[wake-bridge.md](wake-bridge.md)
- [prompt-layers.md](prompt-layers.md)、[model-presets.md](model-presets.md)
- [mcp-server-authoring-template.md](mcp-server-authoring-template.md)：新建独立 MCP Server 时可复制的设计先行模板、数据契约与 Emerald 接入参考。
- [memory.md](memory.md)、[memory-storage-architecture-assessment.md](memory-storage-architecture-assessment.md)、[vector-store.md](vector-store.md)、[data-taxonomy.md](data-taxonomy.md)、[c1-root-asset-inventory.md](c1-root-asset-inventory.md)
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
- [fresh-clone-testing.md](fresh-clone-testing.md)、[system-readiness.md](system-readiness.md)（历史快照）
- [v1-cold-start-single-user-deployment.md](v1-cold-start-single-user-deployment.md)：v1 首次启动、readiness、迁移、备份恢复与单用户部署 runbook
- [user-teach/ubuntu-single-user-deployment.md](user-teach/ubuntu-single-user-deployment.md)：Ubuntu + systemd + Tailscale 的个人服务器源码部署示例与排障记录。
- [user-teach/server-backup-and-upgrade-runbook.md](user-teach/server-backup-and-upgrade-runbook.md)：Ubuntu 单机的每周备份、断点回传、恢复演练与源码升级命令。
- [test_record.md](test_record.md)：手动测试记录模板。

## 历史文档

历史快照的目录说明和逐份处置结论见 [user-teach/README.md](user-teach/README.md)。
