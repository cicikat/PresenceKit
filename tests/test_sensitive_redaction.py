"""Unified sensitive-redaction contracts (work order 256 B)."""

from core.sensitive_redaction import (
    REDACTED,
    REDACTION_VERSION,
    RedactionError,
    inspect_high_risk,
    redact_for_export,
    redact_value,
    reset_counters_for_tests,
)


def setup_function():
    reset_counters_for_tests()


def test_json_and_text_keep_structure_and_hide_secrets():
    text = (
        '{\n'
        '  "model": "example-model",\n'
        '  "max_tokens": 128,\n'
        '  "api_key": "sk-live-example-secret-value",\n'
        '  "nested": {"Authorization": "Bearer abc.def.ghi"}\n'
        '}\n'
    )
    out = redact_for_export(text)
    assert "example-model" in out
    assert "128" in out
    assert "sk-live-example-secret-value" not in out
    assert "abc.def.ghi" not in out
    assert REDACTED in out
    assert "api_key" in out


def test_code_and_log_literals_redact_without_killing_tokenizer():
    source = (
        "tokenizer = 'cl100k_base'\n"
        "token_count = 42\n"
        "api_key = 'sk-test-should-hide'\n"
        "api_key: sk-live-yaml-secret-value\n"
        "Authorization: Bearer super-secret-token-value\n"
        "https://example.invalid/callback?access_token=leak&x=1\n"
    )
    out = redact_for_export(source)
    assert "tokenizer = 'cl100k_base'" in out
    assert "token_count = 42" in out
    assert "sk-test-should-hide" not in out
    assert "sk-live-yaml-secret-value" not in out
    assert "super-secret-token-value" not in out
    assert "access_token=[REDACTED]" in out
    assert "x=1" in out
    assert "Authorization: Bearer [REDACTED]" in out


def test_pem_block_redacted_across_pages():
    prefix = "readme\n"
    pem = "-----BEGIN PRIVATE KEY-----\nMIIHideMeNow\n-----END PRIVATE KEY-----\n"
    suffix = "ok\n"
    redacted = redact_for_export(prefix + pem + suffix)
    assert "MIIHideMeNow" not in redacted
    assert "BEGIN PRIVATE KEY" not in redacted
    page1 = redacted[:8]
    page2 = redacted[8:]
    assert "MIIHideMeNow" not in page1 + page2
    assert "readme" in redacted
    assert "ok" in redacted


def test_high_risk_pem_and_password_db_denied_by_content():
    pem = b"-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n"
    assert inspect_high_risk(name="notes.md", data=pem).denied is True
    assert inspect_high_risk(name="notes.md", data=pem).code == "high_risk_secret_denied"
    assert inspect_high_risk(name="keepass.kdbx", data=b"\x03\xd9\xa2\x9a" + b"\x00" * 8).denied
    login = inspect_high_risk(name="Login Data", data=b"SQLite format 3\x00rest")
    assert login.denied is True
    assert login.code == "credential_store_denied"


def test_token_substring_does_not_deny_source_filename():
    decision = inspect_high_risk(name="tokenizer.py", parts=("core", "tokenizer.py"))
    assert decision.denied is False
    assert "token" in "tokenizer.py"


def test_redaction_failure_is_fail_closed(monkeypatch):
    def boom(_text):
        raise RuntimeError("broken")

    monkeypatch.setattr("core.sensitive_redaction._redact_text_body", boom)
    try:
        redact_for_export("api_key = still-secret")
    except RedactionError as exc:
        assert exc.code == "sensitive_redaction_failed"
    else:
        raise AssertionError("expected RedactionError")


def test_cookie_session_and_url_userinfo_are_redacted():
    text = (
        "Set-Cookie: sessionid=abcdefghij\n"
        "https://user:hunter2@example.invalid/path?x=1\n"
        "password = hunter2-long\n"
    )
    out = redact_for_export(text)
    assert "abcdefghij" not in out
    assert "hunter2" not in out
    assert "example.invalid" in out
    assert "x=1" in out
    assert "password" in out


def test_nested_array_and_version_stable():
    payload = {
        "items": [{"password": "p", "name": "ok"}, {"token": "abcdefghij"}],
        "model_name": "demo",
    }
    out = redact_value(payload)
    assert out["items"][0]["password"] == REDACTED
    assert out["items"][1]["token"] == REDACTED
    assert out["items"][0]["name"] == "ok"
    assert out["model_name"] == "demo"
    from core.sensitive_redaction import observability_snapshot
    snap = observability_snapshot()
    assert snap["version"] == REDACTION_VERSION
    assert snap["note"] == "counts only; no bodies, secrets, or paths"
    assert "abcdefghij" not in str(snap)
