"""
管理面板鉴权
独立模块，避免 admin_server ↔ routers 的循环导入。
"""

import os

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from core.config_loader import get_config

security = HTTPBearer(auto_error=False)


def get_admin_secret() -> str:
    """获取管理面板 secret：env YEXUAN_ADMIN_SECRET 优先，否则读 config.admin.secret_key"""
    env_val = os.environ.get("YEXUAN_ADMIN_SECRET", "").strip()
    if env_val:
        return env_val
    return get_config().get("admin", {}).get("secret_key", "")


def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """简单的 Bearer Token 校验"""
    secret = get_admin_secret()
    if not secret:
        raise HTTPException(status_code=403, detail="admin secret not configured")
    if not credentials or credentials.credentials != secret:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True
