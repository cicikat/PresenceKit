# 公网客户端与私有管理面分离

手机和桌面可以直接连接公网 HTTPS，不要求开启 Tailscale。后端仅监听回环地址；公网 Nginx 只代理客户端接口，管理页面与管理员接口通过 SSH 本地转发或可选 Tailscale Serve 使用。数据库、NapCat OneBot/WebUI、小红书 reader 不发布公网端口。

## 按权限生成公网入口

不要按 `/settings/` 目录整段封禁：`GET /settings/prompt-assets`、`GET /settings/prompt-language` 等是客户端启动合同，拦截会造成角色不可用、头像或历史加载失败。管理写操作与客户端读操作可能共享同一路径，应按 HTTP 方法和真实 scope 区分。

在服务器安装目录、已有离线备份与可回滚 Nginx 配置的前提下运行：

```bash
./.venv/bin/python scripts/build_public_api_allowlist.py \
  --output /etc/nginx/conf.d/presencekit-public-routes.conf
nginx -t
```

公网 HTTPS server 的代理 location 使用该 map：

```nginx
location / {
    if ($presencekit_public_allowed = 0) { return 404; }
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 600s;
    proxy_buffering off;
}
```

生成器保留后端路由匹配顺序，并为管理员路由生成显式拒绝，避免宽泛参数路由意外放行它们。未知路径默认拒绝；管理页、OpenAPI、观测/日志/系统管理入口不开放。客户端接口仍需原有 Bearer scope、角色会话和设备授权，Nginx 放行不授予权限。

检查通过后 reload Nginx。每次升级后端路由时重新生成并检查，保留旧 map 以便恢复；不要更新 source 后继续使用过期 allowlist。

## 验收与故障定位

分别使用桌面和手机 scoped token 核对 `/auth/whoami`、角色列表、`POST /v1/sessions`、带 `X-Presence-Session` 的 `/chat-log/dates`，并验证桌面 WebSocket。管理端点即使携带管理员 Token，从公网也应被 Nginx 拒绝；私有管理入口仍可使用。

区分 Nginx HTML 404、应用 JSON 401/403/404 与 TLS/网络错误。规则修复后，客户端可能需要重新加载角色列表并重新绑定会话；不需要删除聊天数据或为了重新连接轮换 Token。

部署时主动轮换的服务器 `admin.secret_key` 不会自动写回维护机的旧 `config.yaml`。这是部署操作造成的配置差异，不是 `remote_server` 自动换密钥，也不是配置读取链路错误。管理员用服务器当前管理 Token；桌面/手机各自使用对应 scoped token。凭据仅写本地 ignored 交接文件，不记录在本文。
