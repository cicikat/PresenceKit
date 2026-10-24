# 手机可靠投递 R0/R1 施工

日期：2026-10-24。用户已授权联动施工；总单在手机 cc-tasks/42-reliable-mobile-delivery.md。

- [x] ACK写盘False/异常先失败测试，再修为明确失败；恢复后可重试，重复ACK幂等。
- [x] 有慢网络通道时，mobile持久化先完成；发送失败仍进入fanout_failures。
- [x] 定向测试、同步协议/三仓总账与已知问题，独立提交。

不改变seq/TTL/共享单消费者，不新增权限/设置/状态库，不部署。手机网络取消、连续收件、原生交接按总单施工；独立mobile WS后续依赖可靠补收，多设备游标不纳入本单。

证据：tests/test_mobile_queue_ack.py::test_ack_persistence_failure_does_not_confirm_and_can_retry；tests/test_turn_sink.py::test_mobile_mirror_is_persisted_before_slow_network_channel。3文件52 passed in 11.89s；新测试先复现2失败/1通过。修复channels/mobile.py与core/turn_sink.py，文档同步；本提交独立固化，无新设置/状态，桌面协议不变。
