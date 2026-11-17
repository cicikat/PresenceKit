# Round 1 联合上线与回滚

只覆盖本轮 P0/P1、原生 Gemini、双端 Markdown、手机环境候选。生产数据不做故障注入。
当前实际证据见 [交付记录](round1-delivery.md)，状态不能由本手册代替。

## 固定组合与兼容边界

| 端 | 功能提交 | 兼容行为 |
|---|---|---|
| Backend | `1f0deb0bb1e18eed5fa35e38e2d9870c5a1e7f6d` | 原收发字段与手机poll/ACK成功响应不变；ACK落盘失败503，保留队列 |
| Desktop | `5efbababaf69bc032328d1e91678ba5d88f4817f` | Markdown仅展示层；旧后端正文仍可展示，无新增凭据 |
| Mobile | `f4092dc2f2b6e62df4b5a9f232e90a92077a20d2` | 新policy接口由后端先提供；旧后端读取失败应显示错误/重试，不虚构已授权 |

Gemini协议显式选择 `gemini_generate_content` / `provider_kind=gemini`，推荐官方
`https://generativelanguage.googleapis.com/v1beta`；不改已有OpenAI-compatible路由。
手机环境主动行为默认关闭，不能代替上传授权或Android Usage Access。
桌面接受发送不等于收件；手机ACK只证明持久收件，不证明已读。

## 上线顺序

1. 核对三仓当前PR head的检查和固定SHA合同均成功；保留run URL、完整SHA及产物哈希。
2. 只读核对服务器将覆盖的程序文件hash、现有overlay、保护inventory与剩余空间。
   在独立stage三方合并overlay，逐文件记录旧/新hash；冲突停止处理，不覆盖私人配置。
3. 已完成隔离合成restore演练后，生产短暂停服，用现有 `backup-state create`、
   `verify`、`coverage`创建并核验保护状态快照。任何失败先恢复旧服务；禁止整盘/整data打包替代。
   停服窗口只用于一致性快照与程序切换，恢复演练/下载不延长停服。
4. 安装已核验的程序文件；先后端后客户端。仅备份会覆盖的程序文件，记录新增文件清单。
   不修改data/userdata/真实历史，不批量重放消息或副作用工具。
5. 启动并检查systemd、鉴权health、新观测路由、程序hash和ImportError等启动异常。
   生产恢复演练只允许离线no-outbound初始化，不启动第二个服务或消费队列。
6. 提供Windows安装包、同历史证书签名的prod APK与哈希。真实Android/桌面验收必须独立记账；
   无连接设备时记not verified，不能用widget/离屏图/包构建代替。

## 程序回滚

对本轮stage保留 `manifest.json`、`rollback/`及部署receipt；运行前核对路径属于本次stage。
以下均为服务器上的占位路径，替换为本次receipt里的实际stage和安装目录：

```sh
sudo systemctl stop presencekit.service
# 逐项读取manifest：原先存在的文件从rollback恢复；原先不存在的仅移走本轮新增程序文件。
# 复制目标仅限manifest列出的程序路径，保持旧mode/owner；禁止递归覆盖data或userdata。
sudo python3 <本轮stage>/rollback.py --manifest <本轮stage>/manifest.json --installation <安装目录>
sudo systemctl start presencekit.service
systemctl is-active presencekit.service
```

回滚脚本必须先校验当前文件等于本轮部署hash，发现其他更新即拒绝，避免覆盖后续工作。
完成后核对旧程序hash与鉴权health。配置本轮默认不变；若运营之后主动修改了路由/授权，
先导出当前配置，再通过管理面切回部署前preset与关闭phone ambient policy，不能直接覆盖整份配置。

新增恢复DB保留待审计，不删除或交给旧版开发代码消费；特别是早期位置INSERT实现
不兼容新增progress列。基线程序不消费此DB，程序回滚可能留下未完成慢任务；回到本轮代码后
按receipt人工对账，未知投递不自动重发。程序回滚与状态恢复是两件独立操作。

## 私有状态恢复

仅当状态本身损坏且明确决定回到快照时：保持服务停止，执行
`python main.py backup-state verify <快照>`，再
`python main.py backup-state restore <快照> --target <新的空恢复目录>`。
核对recovery-report中的hash/config/auth/character/pipeline/state均ok、外发尝试0；
人工比较快照后新增的真实数据，决定切换范围。禁止自动原地恢复或把更新后真实记忆覆盖掉。
详细操作见 [备份升级手册](../user-teach/server-backup-and-upgrade-runbook.md)。
