# 272 · QQ 空间与提醒对象

## A · 提醒（独立提交）
- [x] create/update/schema/projection 增加 target=user|self；旧数据默认 user，不猜测历史正文。
- [x] 到期按对象生成：user 自然提醒；self 使用现有受限工具链处理自己的事项。
- [x] 删除生成失败后的硬模板；失败保留待重试，不宣称执行成功。
- [x] 回归对象持久化、CAS/恢复、生成失败和到期分流，更新合同并独立提交。相关 63 项通过；真实模型行动未运行。

## B · QQ 空间（独立提交）
- [x] 核对上游协议与 Docker 登录，独立 transport，不混入 QQ 私聊发消息动作。
- [x] 注册查看动态/说说、发布、评论、点赞/取消点赞与删除工具；返回真实结果，写请求不自动重试。
- [x] 消息分发与客户端设置的 QQ 卡片加入热更新连接/授权/只读连接检测及 NapCat Cookie 同步。
- [x] 部署独立桥接、复用 NapCat 登录 Cookie；凭据仅本地 ignored/部署目录。登录网络探针与只读说说查询成功，已绑定当前活跃角色，开启读写权限。
- [x] 相关回归、管理面版本及浏览器验收、只读真实联调后独立提交。66 项通过，换行守卫通过；真实 fragment/handlers 使用隔离配置验证保存、关闭、写开关、Token 保留、地址拒绝、真实只读探针与真实 NapCat Cookie 同步。实际本地配置确认七个工具可见、tool loop 已启用、chat 为 function_calling。

交付边界：原后端进程未重启，OpenAPI 尚不包含新增 qzone router；首次模块加载待正常重启，之后配置热生效。未在真实账号执行发帖/评论/点赞/删除，未做真实模型 self 提醒行动验收。自身能力管理的 `test_self_management_grant_is_discovered_then_native_only` 既有 fixture 仍失败，移除全部 qzone registry/category 增量后同样失败，本单不改其授权行为。

边界：不做自动扫好友/自动回评，不扩展手机桌面协议；真实发说说/评论留给角色按工具权限调用，验收仅读。删除候选：A 的三次失败硬模板随对应旧测试删除；其余清理未授权。
