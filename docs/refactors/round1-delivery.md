# 第一轮可靠性与功能联合交付

状态：施工中；合成验证与真实服务、打包、生产验收分别记录。
审计基线：backend `69769380c262ec3c4e3f1fad5cedb3eda4054e3e`；
desktop `c69b522b1f198e183723c178ee3858a17e5489e0`；
mobile `09809d44fea621a0b329ca6e5faac490b466752f`。

## R2：event_log 幂等

- 基线复现：新增回归中跨回合重复短回复、多天 full_log 重复正文、改名重试共四个业务断言失败。
- 修复：以相邻正文和 metadata 的完整 turn_id、speaker、正文摘要联合判定；角色显示名称不参与身份。
  每个投影独立检查，失败重试仅补缺。读取错误返回失败，不冒充未写入。
- 性能：八文件 LRU 缓存流式构建的指纹集合，正常追加增量更新；外部修改、轮转、截断使缓存失效。
  缓存可重建、不存正文，不新增持久真相源。
- 验证：Python 3.12.13，`pytest -q tests/test_round1_event_log_idempotency.py
  tests/test_event_log_source_tag.py tests/test_event_log_union.py tests/test_p1_memory_fields.py`：33 passed。
- 范围：后端记忆投影内部修复，客户端字段和设置不变；真实进程强杀的半条写入留待 R3 联合验收。
- 状态：partially verified（合成 verified；真实模型、打包客户端、生产端到端 not verified）。

## 待完成

- [ ] R1：回合驻留、工具回执与断流矩阵
- [ ] R3：多存储补偿、关键慢任务恢复与观测
- [ ] R4：主动投递状态与每通道回执
- [ ] R5：权限文档、调度结果与固定 SHA 合同
- [ ] F1：原生 Gemini 适配、控制面与真实服务
- [ ] F2：双端安全 Markdown 与可见验收
- [ ] F3：手机环境候选、授权、设置和真机验收
- [ ] S5：联合固定 SHA CI、恢复演练、部署与回滚

## 删除 brief 候选（未授权，不执行）

后续单独评估退役 `/system/meta-mode` 与危险模式的过期文档/守卫；
仅清除确已退役的条目，保留现行 owner/grant/scope/device control。
