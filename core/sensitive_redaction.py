"""Unified sensitive-redaction for every model-facing file/tool export.

Redact before truncate, paging, cache, or prompt injection. Failure is
fail-closed: callers must not return the original text.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

REDACTION_VERSION = "sensitive-redaction.v1"
REDACTED = "[REDACTED]"

_LOCK = threading.Lock()
_COUNTS: dict[str, int] = {
    "values": 0,
    "urls": 0,
    "blocks": 0,
    "high_risk_denied": 0,
    "failed": 0,
}

# Exact JSON/YAML keys whose *values* are credentials. Identifiers such as
# tokenizer / max_tokens / model stay visible.
_SENSITIVE_KEYS = frozenset({
    "api_key", "apikey", "api-key", "access_token", "refresh_token", "id_token",
    "client_secret", "client_secret_basic", "private_key", "privatekey",
    "password", "passwd", "secret", "authorization", "auth_token", "auth-token",
    "cookie", "set-cookie", "session", "session_id", "sessionid", "bearer",
    "token", "access-token", "secret_key", "secret-key", "credentials",
})
_SENSITIVE_SUFFIXES = (
    "_secret", "_password", "_passwd", "_token", "_api_key", "_apikey",
    "_private_key", "_authorization",
)
_SAFE_KEYS = frozenset({
    "max_tokens", "token_limit", "token_count", "tokenizer", "tokens",
    "encoding", "model", "model_name", "model_id", "token_budget",
    "prompt_tokens", "completion_tokens", "total_tokens",
})

_ASSIGN_RE = re.compile(
    r"(?i)(?P<key>\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"client[_-]?secret|private[_-]?key|password|passwd|secret_key|secret|"
    r"auth[_-]?token|session(?:id|_id)?|"
    r"token))\b(?P<sep>\s*[:=]\s*)(?P<quote>['\"]?)(?P<value>[^\s'\"#&]+)(?P=quote)"
)
_BEARER_RE = re.compile(
    r"(?i)(\b(?:authorization|proxy-authorization)\s*:\s*bearer\s+)(\S+)"
)
_HEADER_AUTH_RE = re.compile(
    r"(?i)(\b(?:authorization|proxy-authorization)\s*:\s*(?!bearer\b))(\S+)"
)
_COOKIE_HEADER_RE = re.compile(
    r"(?i)(\b(?:set-cookie|cookie)\s*:\s*)([^\r\n]+)"
)
_PEM_PRIVATE_RE = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]+)?PRIVATE KEY-----.*?-----END (?:[A-Z0-9 ]+)?PRIVATE KEY-----",
    re.DOTALL,
)
_OPENSSH_RE = re.compile(
    r"-----BEGIN OPENSSH PRIVATE KEY-----.*?-----END OPENSSH PRIVATE KEY-----",
    re.DOTALL,
)
_PGP_PRIVATE_RE = re.compile(
    r"-----BEGIN PGP PRIVATE KEY BLOCK-----.*?-----END PGP PRIVATE KEY BLOCK-----",
    re.DOTALL,
)
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
)
_AWS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_GITHUB_RE = re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")
_GITHUB_PAT_RE = re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")
_SLACK_RE = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")
_SK_RE = re.compile(r"\bsk-(?:live|test|proj)?-?[A-Za-z0-9]{16,}\b")
_SENSITIVE_QUERY_KEYS = frozenset({
    "api_key", "apikey", "auth", "authorization", "credential", "key",
    "password", "secret", "signature", "sig", "token", "access_token",
})
_CREDENTIAL_FILENAMES = frozenset({
    "login data", "cookies", "web data", "logins.json", "key4.db",
    "signons.sqlite", "tokens.yaml", "credentials", "credentials.json",
    "cert8.db", "key3.db", ".env", ".env.local", ".env.production",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
})
_CREDENTIAL_DIRNAMES = frozenset({
    "secrets", ".ssh", "pkcs11",
})
_KDBX_MAGIC = b"\x03\xd9\xa2\x9a"
_JKS_MAGIC = b"\xfe\xed\xfe\xed"
_SQLITE_MAGIC = b"SQLite format 3"


class RedactionError(RuntimeError):
    def __init__(self, code: str = "sensitive_redaction_failed"):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class HighRiskDecision:
    denied: bool
    code: str = ""


def _count(key: str, n: int = 1) -> None:
    with _LOCK:
        _COUNTS[key] = int(_COUNTS.get(key, 0)) + n


def counters() -> dict[str, int]:
    with _LOCK:
        return dict(_COUNTS)


def reset_counters_for_tests() -> None:
    with _LOCK:
        for key in list(_COUNTS):
            _COUNTS[key] = 0


def _is_sensitive_key(key: str) -> bool:
    lowered = str(key).strip().lower().replace("-", "_")
    if lowered in _SAFE_KEYS:
        return False
    if lowered in _SENSITIVE_KEYS:
        return True
    return any(lowered.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def _redact_url(raw: str) -> str:
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return REDACTED
    changed = False
    query_parts: list[str] = []
    if parsed.query:
        for item in parsed.query.split("&"):
            key, separator, _value = item.partition("=")
            if key.lower() in _SENSITIVE_QUERY_KEYS:
                query_parts.append(f"{key}{separator}{REDACTED}" if separator else f"{key}={REDACTED}")
                changed = True
            else:
                query_parts.append(item)
    netloc = parsed.netloc
    if "@" in netloc:
        netloc = REDACTED + "@" + netloc.rsplit("@", 1)[1]
        changed = True
    if not changed:
        return raw
    _count("urls")
    return urlunsplit((parsed.scheme, netloc, parsed.path, "&".join(query_parts), parsed.fragment))


def _redact_mapping(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return REDACTED
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if _is_sensitive_key(str(key)):
                out[key] = REDACTED
                _count("values")
            else:
                out[key] = _redact_mapping(item, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [_redact_mapping(item, depth=depth + 1) for item in value[:500]]
    if isinstance(value, tuple):
        return [_redact_mapping(item, depth=depth + 1) for item in list(value)[:500]]
    if isinstance(value, str):
        return _redact_text_body(value)
    return value


def _redact_text_body(text: str) -> str:
    if not text:
        return text

    def _pem(match: re.Match[str]) -> str:
        _count("blocks")
        return REDACTED

    text = _PEM_PRIVATE_RE.sub(_pem, text)
    text = _OPENSSH_RE.sub(_pem, text)
    text = _PGP_PRIVATE_RE.sub(_pem, text)

    def _url(match: re.Match[str]) -> str:
        raw = match.group(0)
        suffix = ""
        while raw and raw[-1] in ".,;:)":
            suffix = raw[-1] + suffix
            raw = raw[:-1]
        return _redact_url(raw) + suffix

    text = _URL_RE.sub(_url, text)

    def _bearer(match: re.Match[str]) -> str:
        _count("values")
        return f"{match.group(1)}{REDACTED}"

    text = _BEARER_RE.sub(_bearer, text)
    text = _HEADER_AUTH_RE.sub(_bearer, text)

    def _cookie(match: re.Match[str]) -> str:
        _count("values")
        parts = []
        for item in match.group(2).split(";"):
            name, separator, _value = item.partition("=")
            if separator:
                parts.append(f"{name}{separator}{REDACTED}")
            else:
                parts.append(item)
        return f"{match.group(1)}{';'.join(parts)}"

    text = _COOKIE_HEADER_RE.sub(_cookie, text)

    def _assign(match: re.Match[str]) -> str:
        key = match.group("key")
        value = match.group("value")
        if not _is_sensitive_key(key):
            return match.group(0)
        lowered = key.lower()
        if lowered in {"token", "session"} and (
            len(value) < 8 or value.isdigit() or value.lower() in {"true", "false", "none", "null"}
        ):
            return match.group(0)
        _count("values")
        quote = match.group("quote") or ""
        return f"{key}{match.group('sep')}{quote}{REDACTED}{quote}"

    text = _ASSIGN_RE.sub(_assign, text)

    def _literal(match: re.Match[str]) -> str:
        _count("values")
        return REDACTED

    text = _JWT_RE.sub(_literal, text)
    text = _AWS_KEY_RE.sub(_literal, text)
    text = _GITHUB_PAT_RE.sub(_literal, text)
    text = _GITHUB_RE.sub(_literal, text)
    text = _SLACK_RE.sub(_literal, text)
    text = _SK_RE.sub(_literal, text)
    return text


def _try_structured(text: str) -> str | None:
    stripped = text.lstrip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    redacted = _redact_mapping(parsed)
    return json.dumps(redacted, ensure_ascii=False, indent=2 if "\n" in text else None)


def redact_value(value: Any) -> Any:
    """Redact nested dict/list/text. Raises RedactionError on failure."""
    try:
        return _redact_mapping(value)
    except RedactionError:
        raise
    except Exception as exc:
        _count("failed")
        raise RedactionError("sensitive_redaction_failed") from exc


def redact_for_export(text: str) -> str:
    """Return model-safe text. Never returns the original on failure."""
    if not isinstance(text, str):
        text = str(text)
    try:
        structured = _try_structured(text)
        if structured is not None:
            return structured
        return _redact_text_body(text)
    except RedactionError:
        raise
    except Exception as exc:
        _count("failed")
        raise RedactionError("sensitive_redaction_failed") from exc


def inspect_high_risk(*, name: str, data: bytes | None = None, parts: tuple[str, ...] = ()) -> HighRiskDecision:
    """Type/content high-risk check. Not a substring filename deny."""
    lowered_parts = tuple(str(part).lower() for part in parts)
    filename = str(name or "").lower()
    if any(part in _CREDENTIAL_DIRNAMES for part in lowered_parts[:-1] if part):
        _count("high_risk_denied")
        return HighRiskDecision(True, "credential_store_denied")
    if filename in _CREDENTIAL_FILENAMES or filename in _CREDENTIAL_DIRNAMES:
        _count("high_risk_denied")
        return HighRiskDecision(True, "credential_store_denied")
    if data is None:
        return HighRiskDecision(False)
    if data.startswith(_KDBX_MAGIC) or data.startswith(_JKS_MAGIC):
        _count("high_risk_denied")
        return HighRiskDecision(True, "high_risk_secret_denied")
    head = data[:8000]
    try:
        preview = head.decode("utf-8")
    except UnicodeDecodeError:
        try:
            preview = head.decode("ascii", errors="ignore")
        except Exception:
            preview = ""
    stripped = preview.lstrip()
    key_primary = (
        stripped.startswith("-----BEGIN")
        and "PRIVATE KEY" in stripped[:96]
    ) or stripped.startswith("-----BEGIN PGP PRIVATE KEY BLOCK-----")
    if key_primary:
        _count("high_risk_denied")
        return HighRiskDecision(True, "high_risk_secret_denied")
    if data.startswith(_SQLITE_MAGIC) and filename in {
        "login data", "cookies", "web data", "key4.db", "signons.sqlite",
    }:
        _count("high_risk_denied")
        return HighRiskDecision(True, "credential_store_denied")
    suffix = ""
    if "." in filename:
        suffix = filename[filename.rfind("."):]
    if suffix in {".p12", ".pfx", ".jks", ".keystore"} and b"\x00" in data[:256]:
        _count("high_risk_denied")
        return HighRiskDecision(True, "high_risk_secret_denied")
    return HighRiskDecision(False)


def observability_snapshot() -> dict[str, Any]:
    return {
        "version": REDACTION_VERSION,
        "counts": counters(),
        "note": "counts only; no bodies, secrets, or paths",
    }
