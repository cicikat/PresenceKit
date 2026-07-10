# 工单：部署 ntfy 中继 + 打通手机后台推送 + 提交已完成的代码修改

> 由 Claude Desktop 诊断产出，Claude Code 执行。与 Emerald-client / yexuan_memery 的工单**无依赖，可并行**。
> 背景：手机后台弹窗从未触发。诊断结论见 `yexuan_memery/docs/known-issues.md` 的 P0 条目——
> 入队路径正常（mobile_queue_seq 已到 608），断掉的一环是 **relay（ntfy）从未配置**，
> 后端从不发唤醒信号。代码侧缺口已由 Desktop 修好但未提交。

## 任务 0：review + commit 已完成的代码修改（前置：无）

工作区已有两个未提交的改动（Desktop 修改，测试已过：test_relay_publisher.py 3 例 +
test_mobile_queue_ack.py 7 例 + test_channel_char_id_p0.py 7 例）：

1. `channels/relay_publisher.py`：
   - `_relay_config()` 的 token 改为**可选**（原来强制三项必填——无鉴权自建 ntfy 会被误判"未配置"）；
   - 无 token 时不带 `Authorization` 头；
   - 中继未配置时打一次 warning（`_warned_unconfigured` 单次闸），不再静默 return。
2. `config.example.yaml`：`notify:` 段后新增 `relay_base_url/relay_topic/relay_token` 三键的文档化示例。

⚠ 工作区可能有大量换行符幻影 diff（另一环境碰过这个仓库）。commit 前用 `git diff` 确认只提交
上述两个文件的实质改动；其他文件若只有 EOL 差异不要提交。

按测试规则回归：`pytest tests/test_relay_publisher.py tests/test_mobile_queue_ack.py -n auto`。
commit 建议拆两个：
- `fix(relay): make relay_token optional, warn once when relay unconfigured`
- `docs(config): document relay_* keys in config.example.yaml`

**注意**：改的是 channels 层，按 Doc Sync Hook 要求若被拦，说明"文档更新在 config.example.yaml
注释中完成"即可。

## 任务 1：起一个 ntfy 服务（前置：无）

优先级从简到繁，选一个：

- **方案 A（最简，无需自建）**：直接用公网 `https://ntfy.sh`，topic 用长随机串当密码
  （例：`yexuan-wake-` + `python -c "import secrets;print(secrets.token_urlsafe(12))"`）。
  不需要 token。缺点：信号经公网第三方（信号本身不含消息正文，只有 id/seq/时间戳，可接受）。
- **方案 B（自建 docker）**：参考 `yexuan_memery/spike/push_relay_ntfy/server/docker-compose.yml`，
  或最简：`docker run -d --restart unless-stopped -p 8090:80 binwiederhier/ntfy serve`。
  中继地址即 `http://<本机局域网IP>:8090`。Windows 防火墙放行 8090 入站。
  注意：手机端 origin 信任策略要求私网 HTTP 必须是 RFC1918 精确 IPv4（App 内确认时填 IP 不填主机名）。

## 任务 2：后端配置（前置：任务 1）

`config.yaml`（不是 example）加：

```yaml
relay_base_url: "<任务1的地址>"   # 例 https://ntfy.sh 或 http://192.168.x.x:8090
relay_topic: "<随机topic>"
relay_token: ""                   # 方案 A/B 默认无鉴权，留空
```

重启后端。启动后日志不应再出现 `[relay_publisher] relay_base_url/relay_topic 未配置` 的 warning。

## 任务 3：端到端验证（前置：任务 2）

终端 1 订阅：

```powershell
curl -N "<relay_base_url>/<topic>/sse"
```

终端 2 触发一条入队（会真实写入 mobile_queue，手机端会收到一条测试消息，无害）：

```python
# tmp_relay_e2e.py （验证后删除）
import asyncio
from channels.mobile import MobileChannel
from channels import relay_publisher

async def main():
    await MobileChannel().send("relay-e2e-test", "test-user")
    if relay_publisher._publish_tasks:
        await asyncio.gather(*relay_publisher._publish_tasks)

asyncio.run(main())
```

验收：终端 1 的 SSE 流里出现含 `"signal": "new_message"` 的事件（只有 id/seq/user_id/timestamp，
无正文——这是契约，见 `yexuan_memery/docs/protocols/relay-publish-contract.md`）。

## 任务 4：手机侧配置（用户手动，CC 不做，列给用户照做）

1. App 设置 → 填**同一组**中继地址 / topic（token 留空）。
2. 后端节点改为局域网 IP（如 `http://192.168.x.x:8080`）或 HTTPS——**别用 127.0.0.1**，
   脱线后台不可达；后端需监听 0.0.0.0:8080，防火墙放行。
3. 打开 App 后台通知开关、系统通知权限。
4. 测试期间打开 `notificationTestMode`（能力检查页）绕过 23:30-06:30 静音和 30 分钟冷却。
5. 验收：能力检查页显示"中继已连接"+ 最近信号时间；App 切后台 >2 分钟后从后端触发一条主动消息，
   手机应弹通知。

## 依赖关系总览

任务 0 独立可先做；任务 1 → 2 → 3 串行；任务 4 用户手动，在 3 之后。
