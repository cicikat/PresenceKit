"""
管理面板鉴权（SEC-AUTH-2：多 token + scope 分层，default-deny）
独立模块，避免 admin_server ↔ routers 的循环导入。
"""

import hmac
import os
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from core.config_loader import get_config
from admin.token_registry import hash_token, find_by_hash, PLACEHOLDER_ADMIN_SECRET
from admin import audit

security = HTTPBearer(auto_error=False)

_LEGACY_LABEL = "legacy-admin"

# ── §7 限速：进程内存态，按来源 IP 统计 401 失败次数 ─────────────────────────────
_RATE_WINDOW_SECONDS = 60
_RATE_FAILURE_THRESHOLD = 10
_RATE_BLOCK_SECONDS = 300

_failure_times: dict[str, list[float]] = {}
_blocked_until: dict[str, float] = {}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _is_rate_blocked(ip: str) -> bool:
    until = _blocked_until.get(ip)
    if until is None:
        return False
    if time.time() >= until:
        del _blocked_until[ip]
        return False
    return True


def _note_auth_failure(ip: str) -> None:
    now = time.time()
    times = _failure_times.setdefault(ip, [])
    times.append(now)
    cutoff = now - _RATE_WINDOW_SECONDS
    while times and times[0] < cutoff:
        times.pop(0)
    if len(times) >= _RATE_FAILURE_THRESHOLD:
        _blocked_until[ip] = now + _RATE_BLOCK_SECONDS


def reset_rate_limit_state_for_test() -> None:
    """测试隔离用：清空限速内存态（重启进程即清零，测试同样需要手动清零）。"""
    _failure_times.clear()
    _blocked_until.clear()


def get_admin_secret() -> str:
    """获取管理面板 secret：env YEXUAN_ADMIN_SECRET 优先，否则读 config.admin.secret_key。

    这个值永远等价于一条虚拟 admin token（label=legacy-admin），是 bootstrap 锚点。
    占位符（PLACEHOLDER_ADMIN_SECRET）与空值一律视为"未配置"，返回 ""——
    否则 config.example.yaml 里的占位符本身就是一个能用的 admin 全权 token（Brief 33 §1.1）。
    """
    env_val = os.environ.get("YEXUAN_ADMIN_SECRET", "").strip()
    secret = env_val if env_val else str(get_config().get("admin", {}).get("secret_key", "")).strip()
    if secret in ("", PLACEHOLDER_ADMIN_SECRET):
        return ""
    return secret


@dataclass(frozen=True)
class TokenInfo:
    label: str
    scopes: frozenset[str]   # profile 已展开


def resolve_token(raw: str) -> "TokenInfo | None":
    """校验一个 Bearer token 明文，返回其 TokenInfo；无效返回 None。

    legacy secret（env / config.admin.secret_key）永远等价 admin scope，
    优先于 registry 查表（否则没有 token 就无法调建 token 的 API）。
    """
    if not raw:
        return None
    secret = get_admin_secret()
    if secret and hmac.compare_digest(raw, secret):
        return TokenInfo(label=_LEGACY_LABEL, scopes=frozenset({"admin"}))
    record = find_by_hash(hash_token(raw))
    if record is None:
        return None
    return TokenInfo(label=record.label, scopes=record.scopes)


def _scopes_ok(have: frozenset[str], need: tuple[str, ...]) -> bool:
    if "admin" in have:
        return True
    return set(need) <= have


def require_scopes(*scopes: str):
    """FastAPI dependency 工厂：无效/缺失 token → 401；scope 不足 → 403。

    401 失败按来源 IP 计入限速窗口（60s 内 ≥10 次 → 429，持续 300s）；
    401/403 均写审计（data/runtime/auth/audit.jsonl，fail-open，不记 token 值）。
    """
    async def _dep(
        request: Request,
        credentials: HTTPAuthorizationCredentials = Depends(security),
    ) -> TokenInfo:
        ip = _client_ip(request)
        if _is_rate_blocked(ip):
            raise HTTPException(status_code=429, detail="too many failed auth attempts")
        if not credentials:
            _note_auth_failure(ip)
            audit.log_event("auth_failed", path=request.url.path, ip=ip)
            raise HTTPException(status_code=401, detail="Unauthorized")
        info = resolve_token(credentials.credentials)
        if info is None:
            _note_auth_failure(ip)
            audit.log_event("auth_failed", path=request.url.path, ip=ip)
            raise HTTPException(status_code=401, detail="Unauthorized")
        if not _scopes_ok(info.scopes, scopes):
            audit.log_event("scope_denied", label=info.label, path=request.url.path, ip=ip)
            raise HTTPException(
                status_code=403,
                detail=f"insufficient scope, need: {' '.join(scopes)}",
            )
        return info

    _dep._required_scopes = scopes  # 守卫测试标记：见 tests/test_sec_auth2_scopes.py
    return _dep


# 迁移别名：P2 映射完成前，任何还没迁移到 require_scopes(...) 的端点自动收敛为
# admin-only —— fail-closed，漏改不产生安全洞（legacy secret 仍是 admin）。
verify_token = require_scopes("admin")


# ── WebSocket auth helpers ─────────────────────────────────────────────────────

def extract_ws_token(websocket) -> str | None:
    """Extract a Bearer token from a WebSocket upgrade request."""
    auth = websocket.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def authenticate_ws(websocket, required_scope: str) -> "TokenInfo | None":
    """Authenticate a WebSocket upgrade. Returns the TokenInfo if authorized, else None.

    Only Authorization: Bearer is accepted. Token values are never logged.
    """
    token = extract_ws_token(websocket)
    if token is None:
        return None
    info = resolve_token(token)
    if info is None:
        return None
    if not _scopes_ok(info.scopes, (required_scope,)):
        return None
    return info
