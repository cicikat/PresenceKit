"""
ActivitySession 通用类型声明。

activity_type 取值固定为 ALLOWED_ACTIVITY_TYPES 中列举的字面量；
store 层在入口处验证，非法值抛 ValueError。
"""
from typing import Literal

ActivityType = Literal["reading", "gomoku", "chess", "dream_seed"]
ActivityStatus = Literal["active", "closed"]

ALLOWED_ACTIVITY_TYPES: frozenset[str] = frozenset({"reading", "gomoku", "chess", "dream_seed"})
