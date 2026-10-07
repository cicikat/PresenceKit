# 小红书续读合同

首读 `read_xiaohongshu` 返回统计和 `read_id`；同一用户、角色、服务地址可在30分钟内复用。缓存最多8个帖子，服务访问凭据不进入工具文本。统计来自页面，未知返回 null，缩写点赞数保留原显示值而不伪造精确整数。

- `continue_xiaohongshu_comments` 每次请求1..30条；扩大滚动加载目标并按稳定评论ID去重，累计目标最多300。服务没有请求游标接口；没有新评论时明确失败，不把重复首屏报告为续读。
- `read_xiaohongshu_images` 按原帖序号读取，每次1..4张；成功与失败分别标记。缓存最多60张，超过时明确 cache_truncated，不能把缓存末尾称为原帖末尾。
- `read_xiaohongshu_author_posts` 每次1..20个公开标题；cursor仅在已加载主页样本内移动。当前服务只取得主页首批公开帖子，不支持远端主页翻页，返回 remote_pagination=unsupported。

四个工具共用读取服务设置、限流、登录失败反馈与 API 调用观测。关闭主工具同时关闭续读工具。管理面工具页说明读取范围；无需新增客户端设置，统计直接进入角色可读的结果头部。

核查安装源码版本 v2.5.0、commit `6583124dfda92312b6bc19a042a6acfae63fe498`：[HTTP schema](https://github.com/xpzouying/xiaohongshu-mcp/blob/6583124dfda92312b6bc19a042a6acfae63fe498/types.go)、[主页实现](https://github.com/xpzouying/xiaohongshu-mcp/blob/6583124dfda92312b6bc19a042a6acfae63fe498/xiaohongshu/user_profile.go)。没有为缺失能力伪造接口。

验证：fixture覆盖重复ID、统计缩写、跨角色、过期、读取限额、后续图片原序号和主页样本分页。真实服务健康，但登录状态为 false；真实评论续读、图片识别、主页读取均未完成站点验收，需登录后复核。
