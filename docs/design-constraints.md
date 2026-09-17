# 设计约束：远程 owner turn 与日记镜像

本文记录 Brief 171 为仓库设计规则增加的约束；更高层的设计权威仍是
[`DESIGN.md`](../DESIGN.md)。

- 版本化 owner-turn API 是现有 Reality pipeline、conversation gate、冻结角色作用域和 turn sink 的适配器；不创建第二条 pipeline、记忆写入器或 event bus。
- Agent Runtime / Work Session 是同一角色的 durable / specialized 副链运行时，不是角色外的第二个 Agent。走副链只代表执行链不同；能力是否允许在 foreground 主链使用，由 capability / permission / confirmation / deployment 决定。
- 调用方身份、owner 作用域、provenance、live origin 和工具能力均来自服务端 token/profile 配置，不能由请求 JSON 覆盖。
- `deployment.mode=remote_server` 对服务端本机 OS 命令、文件系统浏览、legacy exit signaling 和桌面文件 fallback fail closed；客户端 action 必须经过现有 desktop WS acknowledgement。
- Obsidian 日记镜像只接受有界的日期 Markdown 条目和 metadata。它属于 runtime integration state，与角色 inner-diary API 分离；tombstone 不会物理删除源文件或镜像文件。
