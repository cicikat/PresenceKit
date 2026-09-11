import httpx
import pytest
from core.model_diagnostics import diagnose
from core.llm_protocol import UpstreamResponseFormatError


@pytest.mark.parametrize('status,body,category', [
    (403, 'INSUFFICIENT_BALANCE secret-fixture', 'quota'),
    (401, 'secret-fixture', 'authentication'),
    (429, 'secret-fixture', 'rate_limit'),
    (404, 'secret-fixture', 'endpoint_or_model'),
    (500, 'secret-fixture', 'upstream'),
    (400, 'secret-fixture', 'request_rejected'),
])
def test_status_and_quota_are_distinct_without_echo(status, body, category):
    error = RuntimeError(body)
    error.status_code = status
    result = diagnose(error)
    assert result['category'] == category
    assert result['http_status'] == status
    assert 'secret-fixture' not in str(result)


def test_nested_network_and_format_errors():
    error = RuntimeError('wrapper')
    error.__cause__ = httpx.ConnectError('DNS failed')
    assert diagnose(error)['category'] == 'network'
    assert diagnose(httpx.ReadTimeout('read'))['category'] == 'timeout'
    assert diagnose(UpstreamResponseFormatError('bad fields'))['category'] == 'response_format'
