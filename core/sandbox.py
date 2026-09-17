"""
测试沙盒隔离 — DataPaths 单例（胶水层）
实现层：core/data_paths.py；迁移辅助：core/migration.py
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

from core.data_paths import (  # noqa: E402
    DataPaths, safe_user_id,
    _LAYOUT_CHARACTER_INNER, _LAYOUT_REALITY, _LAYOUT_DREAM,
)
from core.migration import (  # noqa: E402
    for_read, get_fallback_stats, reset_fallback_hit_count,
    _FALLBACK_RECENT_MAX,
)

_instance: DataPaths | None = None


def get_paths() -> DataPaths:
    global _instance
    if _instance is None:
        _instance = DataPaths()
    return _instance


def init_paths(mode: str | None = None, test_session_id: str | None = None) -> DataPaths:
    """项目启动时调用一次（run_test.py 用），之后所有模块调用 get_paths()。"""
    global _instance
    _instance = DataPaths(mode=mode, test_session_id=test_session_id)
    if _instance.mode == "test":
        prefix = str(_instance._base).replace("\\", "/")
        os.environ["YEXUAN_DATA_PREFIX"] = prefix
        logger.info(
            f"[sandbox] TEST 模式已激活 session={_instance.test_session_id} "
            f"数据根目录={_instance._base}"
        )
    return _instance


def paths_for_installation(installation: str | Path) -> DataPaths:
    """Return production paths anchored to an explicit offline installation."""
    return DataPaths(mode="production", project_root=installation)
