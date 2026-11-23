"""HTTP device-profile authorization and opaque producer identity."""
import hashlib
from fastapi import HTTPException, Request


def require_device_identity(request: Request, auth, device: str) -> str:
    if auth.profile != device and "admin" not in auth.scopes:
        raise HTTPException(403, "device profile mismatch")
    return hashlib.sha256(request.headers.get("authorization", "").encode()).hexdigest()
