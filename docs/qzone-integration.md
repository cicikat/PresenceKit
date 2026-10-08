# QQ 空间接入（272 B）

上游：[Gu-Heping/onebot-qzone](https://github.com/Gu-Heping/onebot-qzone)，本轮核对 revision `2d016d2b60130923e060f8bc469d271d397879e0`。独立 REST 服务负责空间，NapCat 负责现有 QQ 私聊及提供 `get_cookies(domain=qzone.qq.com)` 登录凭据。上游 NapCat 插件仅代理独立服务，并不代替登录或服务；后端可直接连桥，无需改 NapCat 插件。

## 功能与权限

已接入 `qzone_get_feeds`、`qzone_get_posts`、`qzone_get_comments`、`qzone_publish`、`qzone_comment`、`qzone_set_like`（含取消赞）、`qzone_delete_post`。本轮发布只接受文字，按 text segment 发送，正文 CQ 码不会触发上传；图片、相册、转发、自动订阅/回评不在本轮。

所有工具属于 qzone，走既有分类发现与 portable schema；info 旧分类配置兼容展开 qzone，独立分类避免工具目录折叠。只有已配置 owner 的私聊、`user_live/assistant_loop/assistant_loop_relay` origin 与角色白名单交集可以执行；autonomy、群聊不准入。读写还受各工具开关、Self Capability 与模型暴露权限控制，写侧必须开启 qzone.write_enabled。每次实际调用先校验空间登录账号，避免换桥/换登录后误用另一账号。

业务工具没有桥地址、token、cookie 或可变 principal 参数。author_id 是说说作者，post_id 是原始字符串 tid，不是 owner uid 或数值 message_id。读列表默认 5、最多 20 条、一页，不附带 base64；游标原样续页。保留真实 tid 作为后续写调用依据。外部正文走标准不可信工具结果边界，不当作指令。

写请求不自动重试；网络超时、服务端 5xx/无效响应视为 outcome_unknown，不得宣称发布成功或盲目重发。上游接口自身可能采用降级路线；QQ 空间逆向接口的可用性不由本后端保证。仅收到 status=ok、retcode=0 且内部 code/ret 不报错时确认成功；调用结果与失败状态进入原 action trace，正文不回流 event_log。

## 管理与观测

管理面「消息分发与客户端设置 → QQ · 空间说说与互动」：启用、允许写入、REST 根地址、桥 Token、预期登录账号、允许角色 ID。默认两个开关关闭，角色白名单空即不授权；关闭只影响空间，保存热更新。Token 留空保留且不回显。角色 ID 使用角色资产 registry ID，不是显示名。

`GET/PUT /settings/qzone`、`POST /settings/qzone/probe`、`POST /settings/qzone/login-cookie`、`POST /settings/qzone/sync-napcat-cookie` 需要 admin。probe 仅查登录与 Cookie 网络探针；Cookie 同步读取已配置 NapCat 的登录号并复核预期账号，再交给桥缓存。手动 Cookie 同样校验账号。Cookie 不写后端配置、响应或台账；失效时在此卡片续期。

`GET /observability/qzone` 需要 state.read，仅返回启用状态、binding_missing/not_checked/ready/账号不匹配等 effective state、最后检查时间、进程内调用/失败计数及凭据是否已配置，不返回账号、Token、Cookie、说说正文。计数为当前进程，重启归零；无新增持久状态。

## 本地部署

把上游 checkout 放在独立集成目录，`npm ci` / `npm run build`。桥监听独立端口，Docker 映射仅绑定 loopback；配置 ONEBOT_ACCESS_TOKEN，并将相同值通过管理面写入后端。QZONE_COOKIE_STRING 来源可复用已登录 NapCat；QZONE_CACHE_PATH 用持久卷。Docker 内监听 0.0.0.0，后端填写宿主机映射地址，不能填另一个容器的 127.0.0.1。

本轮部署的服务名 `presencekit-qzone`，容器按 unless-stopped 重启；关闭桥事件推送与图片 base64，仅按需调用，保留已有 NapCat 容器配置。上游 `.env`、缓存、凭据/部署文件只留本地 ignored 或部署目录，不进入 Git。正常后端运行后新增模块首次加载仍需重启进程；之后设置、Cookie 续期热生效。桌面/手机协议不变。

验收：登录 Cookie 网络探针与只读说说查询成功；写功能用协议映射与失败测试验证，未真实发帖/评论/点赞/删除。管理面浏览器验收结果记录于工单。
