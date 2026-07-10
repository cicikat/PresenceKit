"""
core/coplay/actor.py — 【空壳】"真一起玩"接口，只有 Protocol + 红线注释。

本 brief（38）只写接口，不写任何实现。见 docs/coplay-design-and-briefs-20260710.md
§一 D4：

  红线（不得实现，除非另立 brief 且明确处理以下风险）：
  - 反作弊：任何形式的游戏进程内存读取 —— VAC/EAC 等反作弊系统上有封号风险。
    observer（core/coplay/observer.py）只读文件（存档/日志/appmanifest），
    actor 的 act() 一旦涉及"读游戏内部状态"就必须走同样的只读文件路径，
    禁止 hook/inject/读进程内存。
  - 联机 ToS：多人游戏里的"代打/外挂协作"通常直接违反游戏服务条款，
    输入注入（act()）在联机场景下风险远高于单机，需要另外的 ToS 尽调。
  - Mod API 切入：即便是"合规"的输入注入，也应优先寻找游戏官方 mod/API
    通道，而不是模拟键鼠或注入内存/驱动层输入。

capabilities() 用于让上层（session/watcher）在真正实现出现前，安全地探测
"这个游戏支持几友好互动"而不必 import 具体实现——空壳阶段所有游戏的
capabilities() 都应返回空能力集。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ActorCapabilities:
    """一个 actor 实现声明自己支持哪些互动方式。空壳阶段恒为全 False。"""
    can_observe: bool = False
    can_act: bool = False
    supported_inputs: tuple[str, ...] = field(default_factory=tuple)


class CoplayActor(Protocol):
    """未来"一起玩"接口的形状。当前没有任何实现类满足这个 Protocol。"""

    def observe(self) -> dict:
        """读取当前可安全获得的游戏状态（只读文件路径，见模块红线）。

        空壳阶段：不得实现。真正实现前必须先过反作弊/ToS 尽调（见模块 docstring）。
        """
        ...

    def act(self, command: dict) -> dict:
        """代表用户/角色执行一次游戏内输入。

        空壳阶段：不得实现。这是全接口里风险最高的一半——任何实现都必须先确认
        目标游戏的反作弊系统与联机 ToS 不禁止外部输入注入。
        """
        ...

    def capabilities(self) -> ActorCapabilities:
        """声明本 actor 支持的互动能力。空壳阶段恒返回全 False 的 ActorCapabilities()。"""
        ...
