"""
tests/test_auth_p0.py — Brief 33 · 安全 P0: admin 鉴权与绑定修复

覆盖范围：
  1. 占位/空 secret 永不作为合法 token（admin.auth.get_admin_secret / resolve_token）
  2. env YEXUAN_ADMIN_SECRET 同样过占位检查，真值优先级不变
  3. 启动阻断（main._check_admin_auth_startup）：占位+空 registry → SystemExit；
     占位+有 token → 正常启动 + warning
  4. 弱口令横幅（admin.admin_server._weak_password_warning）：host 非本地 + 短 secret → error 日志
"""

import logging

import pytest

from admin.token_registry import PLACEHOLDER_ADMIN_SECRET
from admin.auth import get_admin_secret, resolve_token


REAL_SECRET = "a-real-strong-secret-value-123456"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 占位/空 secret 永不作为合法 token
# ═══════════════════════════════════════════════════════════════════════════════

class TestPlaceholderSecretNeverValid:
    def test_config_placeholder_filtered_to_empty(self, monkeypatch):
        monkeypatch.delenv("YEXUAN_ADMIN_SECRET", raising=False)
        monkeypatch.setattr(
            "admin.auth.get_config",
            lambda: {"admin": {"secret_key": PLACEHOLDER_ADMIN_SECRET}},
        )
        assert get_admin_secret() == ""

    def test_config_empty_filtered_to_empty(self, monkeypatch):
        monkeypatch.delenv("YEXUAN_ADMIN_SECRET", raising=False)
        monkeypatch.setattr("admin.auth.get_config", lambda: {"admin": {"secret_key": ""}})
        assert get_admin_secret() == ""

    def test_config_real_secret_passes_through(self, monkeypatch):
        monkeypatch.delenv("YEXUAN_ADMIN_SECRET", raising=False)
        monkeypatch.setattr(
            "admin.auth.get_config",
            lambda: {"admin": {"secret_key": REAL_SECRET}},
        )
        assert get_admin_secret() == REAL_SECRET

    def test_resolve_token_placeholder_returns_none(self, monkeypatch):
        """secret 被 get_admin_secret 过滤为 ""，占位符明文不再能当 token 用。"""
        monkeypatch.setattr("admin.auth.get_admin_secret", lambda: "")
        assert resolve_token(PLACEHOLDER_ADMIN_SECRET) is None

    def test_resolve_token_real_secret_unchanged_regression(self, monkeypatch):
        """回归：真实 secret 的比对行为不受本次改动影响。"""
        monkeypatch.setattr("admin.auth.get_admin_secret", lambda: REAL_SECRET)
        info = resolve_token(REAL_SECRET)
        assert info is not None
        assert info.label == "legacy-admin"
        assert "admin" in info.scopes

    def test_resolve_token_empty_secret_rejects_everything(self, monkeypatch):
        monkeypatch.setattr("admin.auth.get_admin_secret", lambda: "")
        assert resolve_token(REAL_SECRET) is None


# ═══════════════════════════════════════════════════════════════════════════════
# 2. env YEXUAN_ADMIN_SECRET 同样过占位检查
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnvPlaceholderCheck:
    def test_env_placeholder_filtered_to_empty(self, monkeypatch):
        monkeypatch.setenv("YEXUAN_ADMIN_SECRET", PLACEHOLDER_ADMIN_SECRET)
        monkeypatch.setattr(
            "admin.auth.get_config",
            lambda: {"admin": {"secret_key": REAL_SECRET}},
        )
        assert get_admin_secret() == ""

    def test_env_real_value_takes_priority_over_config(self, monkeypatch):
        env_secret = "env-real-secret-value"
        monkeypatch.setenv("YEXUAN_ADMIN_SECRET", env_secret)
        monkeypatch.setattr(
            "admin.auth.get_config",
            lambda: {"admin": {"secret_key": REAL_SECRET}},
        )
        assert get_admin_secret() == env_secret

    def test_no_env_falls_back_to_config(self, monkeypatch):
        monkeypatch.delenv("YEXUAN_ADMIN_SECRET", raising=False)
        monkeypatch.setattr(
            "admin.auth.get_config",
            lambda: {"admin": {"secret_key": REAL_SECRET}},
        )
        assert get_admin_secret() == REAL_SECRET


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 启动阻断：main._check_admin_auth_startup
# ═══════════════════════════════════════════════════════════════════════════════

class TestStartupGate:
    def test_placeholder_and_no_tokens_exits(self):
        import main
        with pytest.raises(SystemExit) as exc_info:
            main._check_admin_auth_startup("", False)
        assert exc_info.value.code == 1

    def test_placeholder_but_has_tokens_starts_with_warning(self, caplog):
        import main
        with caplog.at_level(logging.WARNING, logger="main"):
            main._check_admin_auth_startup("", True)
        assert any(
            "secret_key" in r.getMessage() and r.levelno == logging.WARNING
            for r in caplog.records
        )

    def test_real_secret_starts_silently(self, caplog):
        import main
        with caplog.at_level(logging.WARNING, logger="main"):
            main._check_admin_auth_startup(REAL_SECRET, False)
        assert not any(r.levelno >= logging.WARNING for r in caplog.records)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 弱口令横幅：admin.admin_server._weak_password_warning
# ═══════════════════════════════════════════════════════════════════════════════

class TestWeakPasswordBanner:
    def test_nonlocal_host_short_secret_logs_error(self, caplog):
        from admin.admin_server import _weak_password_warning
        with caplog.at_level(logging.ERROR, logger="admin.admin_server"):
            _weak_password_warning("0.0.0.0", "short")
        assert any(r.levelno == logging.ERROR for r in caplog.records)

    def test_local_host_short_secret_no_banner(self, caplog):
        from admin.admin_server import _weak_password_warning
        with caplog.at_level(logging.ERROR, logger="admin.admin_server"):
            _weak_password_warning("127.0.0.1", "short")
        assert not any(r.levelno == logging.ERROR for r in caplog.records)

    def test_nonlocal_host_strong_secret_no_banner(self, caplog):
        from admin.admin_server import _weak_password_warning
        with caplog.at_level(logging.ERROR, logger="admin.admin_server"):
            _weak_password_warning("0.0.0.0", REAL_SECRET)
        assert not any(r.levelno == logging.ERROR for r in caplog.records)
