# VirtualBox / Hyper-V VT-x 冲突排查记录

## 背景

为了测试本项目（Emerald-presence）相关功能，需要在本机用 VirtualBox 跑一个 Windows 11 虚拟机（`test1`）。启动时黑屏，日志关键报错：

```
HM: HMR3Init: Attempting fall back to NEM: VT-x is not available
```

BIOS 里 Intel VT-x 已确认 Enabled。排查方向：Windows 的 Hyper-V / VBS（基于虚拟化的安全）独占了 VT-x，导致 VirtualBox 拿不到硬件加速。

**当前状态：问题尚未解决，仍然黑屏，排查中。**

## 已执行的命令（按时间顺序）

以下命令均在管理员 PowerShell 中执行，每次之后都重启过电脑：

1. ```
   bcdedit /set hypervisorschedulertype classic
   ```
   目的：把 Hyper-V 调度器从默认的 Core Scheduler 换成 Classic Scheduler。
   结果：无效，VirtualBox 日志仍出现 `NEM: NEMR3Init: Snail execution mode is active!`（极慢的单步执行兼容模式）。

2. ```
   bcdedit /set hypervisorlaunchtype off
   ```
   目的：彻底关闭 Hyper-V 主 hypervisor，不让它在开机时加载。
   结果：确认生效（`bcdedit /enum` 显示 `hypervisorlaunchtype Off`），但 VirtualBox 日志显示 `WHvCapabilityCodeHypervisorPresent` 依然为 TRUE，Snail 模式依然存在——说明还有另一层 hypervisor 在跑。

3. ```
   bcdedit /set vsmlaunchtype off
   ```
   目的：关闭 VBS（基于虚拟化的安全）自己独立的一层轻量 hypervisor（这个不受 `hypervisorlaunchtype` 控制，是 Windows 11 24H2+ 对符合条件的硬件默认自动开启的基线安全层，不是通过组策略/注册表手动开的）。
   结果：重启后依然黑屏，还在排查中。

## 目前的系统状态（截至最后一次确认）

```
hypervisorlaunchtype    Off
hypervisorschedulertype Classic
vsmlaunchtype           Off   (已设置，效果待验证)
```

Windows 功能 `HypervisorPlatform`（Windows 虚拟机监控程序平台）本身仍是 Enabled 状态（功能包还在，没卸载，只是运行时不启动）。

## 已知副作用

- **WSL2 / Docker Desktop 的 Hyper-V 后端会失效**，因为它们依赖 `hypervisorlaunchtype`。系统里有一个 `docker-desktop` 的 WSL2 发行版，目前应该起不来了。
- VBS 关闭后，系统会失去基于虚拟化的内核防护（Credential Guard / HVCI 类保护），但这些在这台机器上本来就没被显式开启，所以额外风险较小。

## 恢复指南（虚拟机测试结束后，改回默认状态）

按顺序在管理员 PowerShell 中执行，每条之间不需要重启，全部改完后重启一次即可：

```powershell
bcdedit /set hypervisorlaunchtype auto
bcdedit /set vsmlaunchtype auto
bcdedit /deletevalue hypervisorschedulertype
```

（第三条是删除该设置，恢复系统默认调度器行为，等效于改回 `auto`。也可以显式设置 `bcdedit /set hypervisorschedulertype auto`，效果一样。）

改完后重启电脑，然后验证是否恢复：

```powershell
bcdedit /enum | findstr /i "hypervisorlaunchtype hypervisorschedulertype vsmlaunchtype"
wsl -l -v
```

确认 `hypervisorlaunchtype` 变回 `Auto`，且 `wsl -l -v` 里的 `docker-desktop` 能正常启动（`wsl --distribution docker-desktop` 测试一下），说明已完全恢复。

## 后续排查方向（待续）

- 关闭 vsmlaunchtype 之后仍黑屏，下一步需要确认这次是否真的拿到了原生 VT-x（看新日志里是否还有 `NEM:` / `Snail` / `GIM: HyperV` 相关行）。
- 如果 VT-x 已经正常但仍黑屏，问题可能转移到 VirtualBox 本身的显卡驱动（`vboxsvga` + EFI + SecureBoot=on 的组合）或者 Windows 11 guest 的 UEFI 启动阶段，需要换个角度排查（比如换成 VMSVGA 之外的显卡控制器、关闭虚拟机的 SecureBoot、或检查 test1 虚拟机本身的 EFI/Secure Boot NVRAM 状态）。
