"""
pytest 共享 fixture

- sandbox：将 DataPaths._base 重定向到 tmp_path，隔离文件 I/O
- reset_slow_queue（autouse）：每个测试前重置 slow_queue 模块状态，测试后清理 worker
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

# 将 Emerald-presence 根目录设为工作目录，保证 config.yaml 等相对路径可被正确读取
_ROOT = Path(__file__).parent.parent
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT))


TEST_CHAR_ID = "fixture_character"
TEST_OWNER_ID = "test_owner"


def _configure_public_test_environment() -> None:
    """Use a generated public-only config before importing test modules."""
    import yaml

    worker = os.environ.get("PYTEST_XDIST_WORKER", "controller")
    config_dir = _ROOT / ".tmp"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / f"pytest-public-config-{worker}.yaml"
    local_path = config_dir / f"pytest-public-config-{worker}.local.yaml"

    config = yaml.safe_load((_ROOT / "config.example.yaml").read_text(encoding="utf-8")) or {}
    config.setdefault("character", {})["default"] = TEST_CHAR_ID
    config["character"]["name"] = "Fixture Companion"
    config.setdefault("user", {})["display_name"] = ""
    config.setdefault("scheduler", {})["owner_id"] = TEST_OWNER_ID
    # These tests exercise signal production and queue observability.  Keep
    # scheduler sources available while leaving autonomy/LLM execution off.
    config["scheduler"]["enabled"] = True

    def replace_credentials(value):
        if isinstance(value, dict):
            return {
                key: "test-only-placeholder" if key == "api_key" else replace_credentials(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [replace_credentials(item) for item in value]
        return value

    config_path.write_text(
        yaml.safe_dump(replace_credentials(config), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    os.environ["PRESENCEKIT_CONFIG_PATH"] = str(config_path)


_configure_public_test_environment()


@pytest.fixture(autouse=True)
def reset_runtime_registries(monkeypatch):
    """Each test owns its runtime; registered test doubles must not leak."""
    from core import pipeline_registry, qq_service
    monkeypatch.setattr(pipeline_registry, "_pipeline", None)
    monkeypatch.setattr(qq_service, "_service", None)


def _worker_session(suffix: str) -> str:
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    return f"pytest_{worker}_{suffix}"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """将 DataPaths._base 替换为 tmp_path，使文件读写不污染生产数据。"""
    import core.sandbox as _sandbox
    paths = _sandbox.DataPaths(mode="test", test_session_id=_worker_session("unit"))
    paths._base = tmp_path
    # Authored assets live outside DataPaths._base, so anchor their test-only
    # project root as well.  This keeps public fixture cards out of the repo's
    # real userdata/ tree while preserving the production path layout.
    paths._project_root = tmp_path
    monkeypatch.setattr(_sandbox, "_instance", paths)
    return paths


@pytest.fixture(autouse=True)
def _default_sandbox_guard(tmp_path, monkeypatch):
    """默认安全重定向（Brief 50 · 工单B）。

    `sandbox` fixture 不是 autouse，漏挂它的 I/O 测试会写真实 data/。这里在每个
    测试开始时先把 core.sandbox._instance 重定向到 pytest 临时目录；显式使用
    `sandbox` fixture 的测试会在此之后运行（同 scope 内 autouse 先于非 autouse
    执行），其 monkeypatch.setattr 会覆盖这里设置的默认值，语义不变。
    """
    import core.sandbox as _sandbox
    guard_paths = _sandbox.DataPaths(mode="test", test_session_id=_worker_session("default_guard"))
    guard_paths._base = tmp_path / "_default_sandbox_guard"
    guard_paths._project_root = tmp_path
    monkeypatch.setattr(_sandbox, "_instance", guard_paths)


@pytest.fixture(autouse=True)
def _dream_scenario_examples(monkeypatch):
    """Use tracked, neutral scenario fixtures instead of private data/ content."""
    import core.dream.scenario_loader as scenario_loader

    fixture_dir = _ROOT / "tests" / "fixtures" / "dream_scenarios"
    monkeypatch.setattr(scenario_loader, "_SCRIPTS_BASE", fixture_dir)


@pytest.fixture(autouse=True)
def _public_dream_world_template(monkeypatch, request):
    """Point world-management writes at the tracked public seed package."""
    if request.node.path.name != "test_dream_world_management.py":
        return
    from core.data_paths import DataPaths
    from tests.fixtures.public_assets import PUBLIC_DREAM_WORLDS

    monkeypatch.setattr(
        DataPaths,
        "default_dream_world_template_dir",
        lambda _self: PUBLIC_DREAM_WORLDS / "_default",
    )


@pytest.fixture
def real_dream_worlds(tmp_path, monkeypatch):
    """Use tracked public dream worlds, never a developer's authored worlds."""
    from tests.fixtures.public_assets import install_public_dream_worlds

    public_base = install_public_dream_worlds(tmp_path / "dream_worlds")

    import core.dream.world_loader as _world_loader
    import core.dream.hud_label_loader as _hud_label_loader
    import core.dream.scene_label_loader as _scene_label_loader
    import core.dream.symbolic_loader as _symbolic_loader

    monkeypatch.setattr(_world_loader, "_worlds_base", lambda: public_base)
    monkeypatch.setattr(_hud_label_loader, "_worlds_base", lambda: public_base)
    monkeypatch.setattr(_scene_label_loader, "_worlds_base", lambda: public_base)
    monkeypatch.setattr(_symbolic_loader, "_worlds_base", lambda: public_base)
    return public_base


@pytest.fixture(autouse=True)
def public_character_assets(sandbox, request):
    """Install public role cards in every test sandbox."""
    from tests.fixtures.public_assets import install_public_character_cards
    from core.sandbox import get_paths

    paths = get_paths()
    custom_asset_fixtures = {
        "chars_dir",
        "chars_tree",
        "fake_characters",
        "registry",
        "registry_from",
    }
    custom_asset_root = bool(custom_asset_fixtures.intersection(request.fixturenames))
    self_managed_asset_root = request.node.path.name == "test_user_asset_paths.py"
    original_project_root = getattr(paths, "_project_root", None)
    if self_managed_asset_root and hasattr(paths, "_project_root"):
        # This module deliberately verifies relative production-path
        # compatibility after changing cwd; do not let the shared test anchor
        # turn its AssetEntry paths into absolute paths.
        paths._project_root = None
    # A small number of legacy module-local fixtures construct DataPaths via
    # __new__ and intentionally expose only the runtime sandbox API.  They do
    # not consume character cards; do not make the shared asset fixture depend
    # on their incomplete test double.
    if hasattr(paths, "_project_root") and not custom_asset_root and not self_managed_asset_root:
        install_public_character_cards(paths)
    import core.asset_registry as asset_registry
    import core.character_loader as character_loader

    asset_registry._registry = None
    character_loader._character_cache.clear()
    yield
    if self_managed_asset_root and hasattr(paths, "_project_root"):
        paths._project_root = original_project_root
    asset_registry._registry = None
    character_loader._character_cache.clear()


@pytest.fixture(autouse=True)
def reset_perceive_event_registry():
    """Reset perceive_event dedup registry before each test (prevents cross-test leakage)."""
    from core.perceive_event import clear_dedup_registry_for_test
    clear_dedup_registry_for_test()
    yield
    clear_dedup_registry_for_test()


@pytest.fixture(autouse=True)
def reset_auth_rate_limit():
    """Reset admin.auth's in-process 401 rate-limit state before/after each test.

    admin/auth.py:require_scopes() tracks 401 failures per source IP in a module-level
    dict (SEC-AUTH-2 §7, 60s window / 10 failures -> 429 for 300s). TestClient requests
    all share IP "testclient", so without a reset, unrelated test files that each send a
    few no-token/wrong-token requests (e.g. tests/test_sec_auth1.py) accumulate across the
    whole pytest session and eventually trip the 429 block, breaking later "correct token"
    assertions that have nothing to do with rate limiting.
    """
    from admin import auth as _auth
    _auth.reset_rate_limit_state_for_test()
    yield
    _auth.reset_rate_limit_state_for_test()


@pytest.fixture(autouse=True)
def reset_proactive_ledger():
    """Reset ProactiveLedger module state before each test (CC 任务 19 · B).

    core/scheduler/proactive_ledger.py holds module-level next_allowed_ts /
    daily_count / recent state that persists across tests in the same process
    (mirrors loop._last_trigger, which individual tests already reset ad hoc).
    Without this, a test that calls execute_prompt()/record_send() successfully
    can leave next_allowed_ts in the future, causing an unrelated later test's
    gating._decide() to spuriously fail with global_gap_filtered.
    """
    from core.scheduler import proactive_ledger as _ledger
    from core.sandbox import get_paths
    _ledger._state = {
        "next_allowed_ts": 0.0,
        "daily_count": 0,
        "daily_logical_day": "",
        "recent": [],
        "continuity_by_uid": {},
    }
    _ledger._loaded = True  # skip disk load; state above is authoritative for the test
    _ledger._loaded_path_token = str(get_paths().root_dir().resolve())
    yield


@pytest.fixture
def character_b_registered(tmp_path, monkeypatch):
    """Register a test card in the canonical userdata read layer.

    The asset resolver reads userdata first, then bundled assets and finally the
    legacy characters/ directory.  Keep this fixture in its isolated userdata
    root so it proves the current contract without recreating a legacy root.
    """
    import core.data_paths as data_paths
    from core.sandbox import get_paths

    monkeypatch.setattr(data_paths, "_USERDATA_ROOT", tmp_path / "userdata")
    p = get_paths().character_card_write_path("character_b")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"name": "Character B"}', encoding="utf-8")
    yield


@pytest.fixture(autouse=True)
def reset_asset_registry():
    """Reset core.asset_registry's module-level singleton before/after each test.

    Several tests replace `_registry` directly (bypassing get_registry()/reload_registry())
    without restoring it, so a fixture-scoped registry (e.g. missing hongcha) leaks into
    unrelated later tests run in the same process.
    """
    import core.asset_registry as _reg
    _reg._registry = None
    yield
    _reg._registry = None


@pytest.fixture(autouse=True)
async def reset_slow_queue():
    """每个测试前重置 slow_queue 模块状态（队列/handler/worker），测试后清理 worker。"""
    import core.post_process.slow_queue as sq

    # 取消上一个测试遗留的 worker（若有）
    if sq._worker_task is not None and not sq._worker_task.done():
        sq._worker_task.cancel()
        try:
            await sq._worker_task
        except asyncio.CancelledError:
            pass

    # 用绑定当前 event loop 的新 Queue 替换旧实例，清空 handler 注册表
    sq._queue = asyncio.Queue()
    sq._handlers = {}
    sq._worker_task = None

    yield

    # 测试结束后清理 worker
    if sq._worker_task is not None and not sq._worker_task.done():
        sq._worker_task.cancel()
        try:
            await sq._worker_task
        except asyncio.CancelledError:
            pass
    sq._worker_task = None
