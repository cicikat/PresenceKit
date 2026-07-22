from types import SimpleNamespace

from core import api_call_log


def test_api_call_log_is_fail_open_and_returns_newest_filtered_rows(tmp_path, monkeypatch):
    ledger = tmp_path / "api_calls.jsonl"
    monkeypatch.setattr(
        api_call_log,
        "get_paths",
        lambda: SimpleNamespace(api_call_log=lambda: ledger),
    )

    api_call_log.append(
        caller="llm_client",
        purpose="chat",
        provider="openai",
        model="test-model",
        duration_ms=15,
        ok=True,
    )
    api_call_log.append(
        caller="web_search",
        purpose="search",
        provider="ddgs",
        model="text",
        duration_ms=20,
        ok=False,
        output_hint="TimeoutError",
    )

    rows, grouped = api_call_log.query(provider="ddgs")

    assert len(rows) == 1
    assert rows[0]["caller"] == "web_search"
    assert rows[0]["duration_ms"] == 20
    assert rows[0]["output_hint"] == "TimeoutError"
    assert grouped == {"ddgs": 1}


def test_api_call_log_never_persists_long_output_hint(tmp_path, monkeypatch):
    ledger = tmp_path / "api_calls.jsonl"
    monkeypatch.setattr(
        api_call_log,
        "get_paths",
        lambda: SimpleNamespace(api_call_log=lambda: ledger),
    )

    api_call_log.append(
        caller="embedding",
        purpose="encode",
        provider="openai_compat",
        model="embedding-model",
        duration_ms=-1,
        ok=False,
        output_hint="x" * 300,
    )

    rows, _ = api_call_log.query()

    assert rows[0]["duration_ms"] == 0
    assert len(rows[0]["output_hint"]) == 120
