"""
tests/test_setup_auth.py — Brief 22 §5.2: scripts/setup_auth.py 首次配置 CLI

覆盖：空 config 全新跑（secret 生成 + 五 token + 密码本齐全）、重复跑幂等（含用户手工
追加的密码本条目不被覆盖）、--rotate-all 后密码本条目更新且旧值立即失效、
已有真实 secret_key 时不覆盖。
"""
import sys

import pytest
import yaml

import scripts.setup_auth as setup_auth
from admin import token_registry
from admin.auth import resolve_token


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    token_registry._records = None
    token_registry._mtime = None
    yield
    token_registry._records = None
    token_registry._mtime = None


@pytest.fixture()
def env(sandbox, monkeypatch):
    config_path = sandbox._base / "config.yaml"
    config_path.write_text(
        "admin:\n  enabled: true\n  secret_key: YOUR_ADMIN_SECRET\nother:\n  x: 1\n",
        encoding="utf-8",
    )
    secrets_local_path = sandbox._base / "secrets.local.yaml"
    monkeypatch.setattr(setup_auth, "CONFIG_PATH", config_path)
    monkeypatch.setattr(setup_auth, "SECRETS_LOCAL_PATH", secrets_local_path)
    return config_path, secrets_local_path


def test_first_run_generates_secret_and_five_tokens(env, monkeypatch):
    config_path, secrets_local_path = env
    monkeypatch.setattr(sys, "argv", ["setup_auth.py"])
    setup_auth.main()

    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    secret1 = cfg["admin"]["secret_key"]
    assert secret1 and secret1 != "YOUR_ADMIN_SECRET"
    assert cfg["other"]["x"] == 1  # 其余键不受影响

    doc = yaml.safe_load(secrets_local_path.read_text(encoding="utf-8"))
    assert doc["break_glass_secret"] == secret1
    assert set(doc["tokens"].keys()) == {
        "desktop-main", "mobile-main", "watch-main", "esp32-device", "admin-panel",
    }
    assert doc["tokens"]["desktop-main"]["token"].startswith("emt_")
    assert "adminToken" in doc["tokens"]["desktop-main"]["配置位置"]
    assert resolve_token(doc["tokens"]["desktop-main"]["token"]) is not None


def test_rerun_is_idempotent_and_preserves_user_entries(env, monkeypatch):
    config_path, secrets_local_path = env
    monkeypatch.setattr(sys, "argv", ["setup_auth.py"])
    setup_auth.main()
    doc1 = yaml.safe_load(secrets_local_path.read_text(encoding="utf-8"))
    token_v1 = doc1["tokens"]["desktop-main"]["token"]
    secret1 = doc1["break_glass_secret"]

    setup_auth.main()
    cfg2 = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    doc2 = yaml.safe_load(secrets_local_path.read_text(encoding="utf-8"))
    assert cfg2["admin"]["secret_key"] == secret1
    assert doc2["tokens"]["desktop-main"]["token"] == token_v1

    # 用户手工在密码本里加了一条自己的条目 —— 普通重跑不能覆盖它
    doc2["tokens"]["my-custom-device"] = {"token": "emt_custom", "配置位置": "手写"}
    secrets_local_path.write_text(
        yaml.safe_dump(doc2, allow_unicode=True, sort_keys=False), encoding="utf-8",
    )
    setup_auth.main()
    doc3 = yaml.safe_load(secrets_local_path.read_text(encoding="utf-8"))
    assert doc3["tokens"]["my-custom-device"]["token"] == "emt_custom"
    assert doc3["tokens"]["desktop-main"]["token"] == token_v1


def test_rotate_all_updates_password_book_and_invalidates_old_values(env, monkeypatch):
    config_path, secrets_local_path = env
    monkeypatch.setattr(sys, "argv", ["setup_auth.py"])
    setup_auth.main()
    doc1 = yaml.safe_load(secrets_local_path.read_text(encoding="utf-8"))
    token_v1 = doc1["tokens"]["desktop-main"]["token"]
    assert resolve_token(token_v1) is not None

    monkeypatch.setattr(sys, "argv", ["setup_auth.py", "--rotate-all"])
    setup_auth.main()
    doc2 = yaml.safe_load(secrets_local_path.read_text(encoding="utf-8"))
    token_v2 = doc2["tokens"]["desktop-main"]["token"]

    assert token_v2 != token_v1
    assert resolve_token(token_v1) is None
    assert resolve_token(token_v2) is not None


def test_existing_real_secret_is_not_overwritten(env, monkeypatch):
    config_path, secrets_local_path = env
    config_path.write_text(
        'admin:\n  enabled: true\n  secret_key: "already-real-secret-value"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["setup_auth.py"])
    setup_auth.main()
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert cfg["admin"]["secret_key"] == "already-real-secret-value"
