"""
配置加载模块
全局单例，读取 config.yaml，供所有模块使用
"""

import os
from copy import deepcopy
from pathlib import Path

import yaml

from core.config_yaml import ConfigMappingError, load_config_mapping

_config: dict | None = None
_base_config: dict | None = None
_CONFIG_PATH = Path(os.environ.get("PRESENCEKIT_CONFIG_PATH", "config.yaml"))
_config_mtime: float | None = None
_base_config_mtime: float | None = None
_DATA_PREFIX_ENV = "YEXUAN_DATA_PREFIX"


def get_config_path() -> Path:
    """Return the base config path used by the runtime and admin writers."""
    return _CONFIG_PATH


def _read_snapshot() -> tuple[dict, float]:
    """Read both bytes and revision from the same open file."""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as stream:
            revision = os.fstat(stream.fileno()).st_mtime
            return load_config_mapping(stream), revision
    except FileNotFoundError:
        raise RuntimeError(f"配置文件不存在：{_CONFIG_PATH.absolute()}")
    except yaml.YAMLError as exc:
        raise RuntimeError(f"配置文件格式错误：{exc}")
    except ConfigMappingError:
        raise RuntimeError(f"配置文件顶层必须是 mapping：{_CONFIG_PATH.absolute()}")


def get_config() -> dict:
    """
    返回配置字典（单例，带 mtime 热加载）。
    每次调用 stat() 配置文件；若 mtime 较上次加载时变化（或从未加载过），
    自动 reload_config()。stat() 开销可忽略，使手改 config.yaml 对运行中进程即时生效
    （此前 _config 是永久缓存单例，手改磁盘文件从不被运行中进程读取）。
    stat 失败（如文件被临时替换的极短窗口）时 fail-open：沿用内存缓存，不抛出。
    """
    global _config
    if _config is None:
        reload_config()
        return _config
    try:
        mtime = _CONFIG_PATH.stat().st_mtime
    except OSError:
        return _config
    if mtime != _config_mtime:
        reload_config()
    return _config


def get_base_config() -> dict:
    """Return config.yaml without environment-only overrides."""
    global _base_config, _base_config_mtime
    try:
        mtime = _CONFIG_PATH.stat().st_mtime
    except OSError:
        if _base_config is None:
            raise RuntimeError(f"配置文件不存在：{_CONFIG_PATH.absolute()}")
        return _base_config
    if _base_config is None or mtime != _base_config_mtime:
        _base_config, _base_config_mtime = _read_snapshot()
    return _base_config


def reload_config() -> dict:
    """重新从磁盘读取基础配置（磁盘 mtime 变化后调用）。

    读取顺序 env > config.yaml：`YEXUAN_DATA_PREFIX` 存在时覆盖配置里的
    `data_prefix` 字段（不改磁盘文件本身）。测试沙盒（run_test.py）借此声明
    自己的数据前缀，config.yaml 从此保持只读，不再被运行时脚本改写。
    """
    global _base_config, _base_config_mtime, _config, _config_mtime
    base, revision = _read_snapshot()
    runtime = deepcopy(base)
    env_prefix = os.environ.get(_DATA_PREFIX_ENV)
    if env_prefix:
        runtime["data_prefix"] = env_prefix
    _base_config, _base_config_mtime = base, revision
    _config, _config_mtime = runtime, revision
    return runtime


def _char_name() -> str:
    return get_config().get("character", {}).get("name", "他")


def get_user_display_name() -> str:
    """用户显示名（config.yaml → user.display_name），未配置时返回空串。

    调用方在空值时应回退到无名称写法（例如直接省略称呼、只用"你"），
    不得拼出"用户（）"这类怪句。"""
    return str(get_config().get("user", {}).get("display_name") or "").strip()
