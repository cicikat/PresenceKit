# 317 Android FCM 真机可行性

状态 **observe**。施工环境的 ADB 未枚举到 Android 手机，也没有已配置的虚拟设备。开发与正式包名从 Gradle 核对为 `com.presencekit.mobile.dev` 与 `com.presencekit.mobile`。未发现两个 flavor 的 Firebase 配置，也未发现已配置的 Google 应用默认凭据。GMS、Token 签发、实际到达、Doze 与网络切换结果均待实测。

## 准备

1. 在用户持有的 Firebase 项目启用 FCM HTTP v1。添加两个 Android 应用，把各自下载的配置文件放在手机仓的 `android/app/src/dev/google-services.json` 和 `android/app/src/prod/google-services.json`。每个文件对应自己的包名。项目和部署凭据由维护者保管。
2. 在后端运行环境提供 `PRESENCEKIT_FCM_PROJECT`、`PRESENCEKIT_FCM_REGISTRY_KEY` 和 Google Application Default Credentials。登记表密钥为 Fernet 密钥，可用 `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` 在受控终端生成。凭据和密钥使用服务环境或受保护的密钥文件。
3. 连接已启用 USB 调试的设备。用 `adb devices -l` 和 `adb shell dumpsys package com.google.android.gms` 核对设备及 GMS。安装对应 dev 调试包，使用独立 mobile profile 令牌登录，确认选中角色，再在能力检查中启用系统消息提醒并授予通知权限。
4. 用实际主动消息测试前台、后台亮屏、息屏 10 分钟、Doze、切网、离线重连、划走和进程回收。用 `adb shell dumpsys deviceidle force-idle` 进入 Doze，测试后用 `adb shell dumpsys deviceidle unforce` 恢复。强行停止另列记录。
5. 对照 `GET /observability/mobile-delivery` 和 `adb logcat -s PresenceDelivery` 的哈希，记录通知是否实际出现、点击后是否恢复消息，以及有无常驻服务通知。时钟对齐后按设备状态各记录至少 30 条，计算成功率、p50/p95，并保留失败和迟到样本。

如果系统推送不可用，可以在能力检查显式启用兼容常驻接收。该模式会显示常驻通知。Google 接受发布请求只说明 FCM 收到了发送请求。服务端也支持以隔离测试验证注册和传输行为。

参考：[Android FCM 接入](https://firebase.google.com/docs/cloud-messaging/android/get-started)、[消息优先级与 Doze 测试](https://firebase.google.com/docs/cloud-messaging/android-message-priority)、[HTTP v1 与 ADC](https://firebase.google.com/docs/cloud-messaging/send/v1-api)。
