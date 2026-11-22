"""CC 任务 19 · A1 — config.yaml 热加载单测。

覆盖 get_config() 的 mtime 检查：磁盘文件变化后下一次 get_config() 调用
应读到新值，无需显式调用 reload_config() / 重启进程。
"""

import os
import time

import pytest


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """把 config_loader 的 _CONFIG_PATH 重定向到临时文件，隔离真实 config.yaml。"""
    import core.config_loader as cl

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("scheduler:\n  global_proactive_min_gap_seconds: 100\n", encoding="utf-8")

    monkeypatch.setattr(cl, "_CONFIG_PATH", cfg_path)
    monkeypatch.setattr(cl, "_config", None)
    monkeypatch.setattr(cl, "_base_config", None)
    monkeypatch.setattr(cl, "_config_mtime", None)
    monkeypatch.setattr(cl, "_base_config_mtime", None)
    return cl, cfg_path


def test_first_call_loads_from_disk(_isolated_config):
    cl, _ = _isolated_config
    cfg = cl.get_config()
    assert cfg["scheduler"]["global_proactive_min_gap_seconds"] == 100


def test_get_config_picks_up_disk_change_without_explicit_reload(_isolated_config):
    cl, cfg_path = _isolated_config
    assert cl.get_config()["scheduler"]["global_proactive_min_gap_seconds"] == 100

    # 手改磁盘文件（模拟用户直接编辑 config.yaml），不调用 reload_config()。
    time.sleep(0.01)  # ensure a distinguishable mtime on filesystems with coarse resolution
    cfg_path.write_text("scheduler:\n  global_proactive_min_gap_seconds: 35100\n", encoding="utf-8")
    # Force a fresh mtime distinct from the original write (some filesystems round to 1s).
    new_mtime = os.path.getmtime(cfg_path) + 5
    os.utime(cfg_path, (new_mtime, new_mtime))

    cfg = cl.get_config()
    assert cfg["scheduler"]["global_proactive_min_gap_seconds"] == 35100, (
        "get_config() must observe disk mtime changes without an explicit reload_config() call"
    )


def test_get_config_does_not_reread_when_mtime_unchanged(_isolated_config, monkeypatch):
    cl, _ = _isolated_config
    cl.get_config()  # first load

    reload_calls = []
    original_reload = cl.reload_config

    def _spy_reload():
        reload_calls.append(1)
        return original_reload()

    monkeypatch.setattr(cl, "reload_config", _spy_reload)

    cl.get_config()
    cl.get_config()

    assert reload_calls == [], "unchanged mtime must not trigger a redundant reload_config() call"


def test_env_data_prefix_overrides_config_yaml(_isolated_config, monkeypatch):
    """Brief 34 §3：YEXUAN_DATA_PREFIX 存在时覆盖 config.yaml 里的 data_prefix 字段，
    读取顺序 env > config，且不改磁盘文件内容。"""
    cl, cfg_path = _isolated_config
    original_text = cfg_path.read_text(encoding="utf-8")

    monkeypatch.setenv("YEXUAN_DATA_PREFIX", "data/test_sandbox/session_env_test")
    cfg = cl.get_config()

    assert cfg["data_prefix"] == "data/test_sandbox/session_env_test"
    assert "data_prefix" not in cl.get_base_config()
    assert cfg_path.read_text(encoding="utf-8") == original_text, (
        "env 覆盖不应改写磁盘上的 config.yaml"
    )


def test_no_env_data_prefix_falls_back_to_config(_isolated_config, monkeypatch):
    """未设置 YEXUAN_DATA_PREFIX 时，data_prefix 缺省（config.yaml 本就没写这个字段）。"""
    cl, _ = _isolated_config
    monkeypatch.delenv("YEXUAN_DATA_PREFIX", raising=False)

    cfg = cl.get_config()
    assert "data_prefix" not in cfg


def test_get_config_fail_open_when_stat_raises(_isolated_config, monkeypatch):
    """stat() 失败（如文件被临时替换的极短窗口）时应 fail-open，沿用内存缓存而非抛出。"""
    cl, cfg_path = _isolated_config
    cfg = cl.get_config()
    assert cfg["scheduler"]["global_proactive_min_gap_seconds"] == 100

    def _raise_stat(*a, **kw):
        raise OSError("simulated transient stat failure")

    monkeypatch.setattr(type(cfg_path), "stat", _raise_stat)

    cfg2 = cl.get_config()
    assert cfg2["scheduler"]["global_proactive_min_gap_seconds"] == 100


def test_invalid_replacement_does_not_poison_the_previous_base_snapshot(_isolated_config):
    cl, path = _isolated_config
    previous = cl.get_base_config().copy()
    cl.get_config()
    path.write_text("[invalid-root]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="mapping"):
        cl.reload_config()

    path.unlink()
    assert cl.get_base_config() == previous
    assert cl.get_config()["scheduler"] == previous["scheduler"]


def test_reload_uses_the_revision_of_the_bytes_it_parsed(_isolated_config, monkeypatch):
    from core import config_yaml

    cl, path = _isolated_config
    original_load = config_yaml.yaml.load
    original_mtime = path.stat().st_mtime
    replaced = False

    def replace_after_parse(stream, **options):
        nonlocal replaced
        value = original_load(stream, **options)
        if not replaced:
            replaced = True
            path.write_text("scheduler: {global_proactive_min_gap_seconds: 205}\n", encoding="utf-8")
            os.utime(path, (original_mtime + 5, original_mtime + 5))
        return value

    monkeypatch.setattr(config_yaml.yaml, "load", replace_after_parse)
    assert cl.reload_config()["scheduler"]["global_proactive_min_gap_seconds"] == 100
    assert cl.get_config()["scheduler"]["global_proactive_min_gap_seconds"] == 205

