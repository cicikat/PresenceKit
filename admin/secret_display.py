"""Redacted credential presentation for admin settings responses."""


def looks_placeholder(value) -> bool:
    """config.example.yaml 里的占位符统一 YOUR_ / YOUR- 前缀（见 PLACEHOLDER_ADMIN_SECRET 同一约定）。"""
    v = str(value or "").strip()
    if not v:
        return True
    upper = v.upper()
    return upper.startswith("YOUR_") or upper.startswith("YOUR-")


def mask_key(key: str) -> str:
    if not key or len(key) <= 8:
        return "***"
    return key[:4] + "***" + key[-4:]
