# 收藏小本本（F5）

`save_quote(message_id, note?, related_event_ids?)` 收藏双方已经入账的现实原话；message_id 来自引用、search_events 或 read_message_context，不接受任意文本伪造来源。当前尚未完成的消息下一轮入账后才能收藏。最多关联3条可查证召回，保存有界证据快照；未给关联不替角色猜测。

同 uid/char/message 幂等收藏，不覆盖既有原话、笔记和关联。`search_quotes` 按原话/笔记搜索，默认20项最多50；`read_quote` 打开；`write_quote_note` 显式更新笔记，旧笔记记录保留。工具描述、examples、keywords 已注册到 memory 类发现面。原话保存 source message_id、作者、原消息时间及收藏时间，笔记独立更新；不会将整本常驻注入 prompt。

存储经 `get_paths().quote_notebook_db()`，DB按uid/char隔离，写入 provenance。来源后来裁剪或墓碑时，收藏快照仍保留并标注 source_available=false；用户删除收藏会删除快照及笔记。这与遗忘原消息是两项独立操作。超过完整来源可读上限时拒绝收藏，不把截断片段冒充完整原话。角色读取 assistant 原话仍经过现有脱敏函数。

管理面工具页提供小本本：按角色搜索、打开原话/日期/相关召回、更新笔记及删除。读取 `/observability/quotes` 和 `/{quote_id}` 使用 memory.read；修改 `/settings/quotes/{quote_id}/note` 和删除使用 admin。无新增开关、客户端消息合同或外部发信。

隔离回归覆盖双方收藏、重复、跨作用域、关联不可用、笔记保留、来源遗忘及删除。浏览器使用合成消息验证打开和笔记更新；真实模型发现工具、真实用户资料收藏未验收。
