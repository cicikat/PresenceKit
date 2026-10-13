import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.memory import embedding


@pytest.mark.parametrize("field", ["base_url", "api_key", "model"])
@pytest.mark.parametrize("value", ["", "  YOUR_KEY  ", "your-key"])
def test_unconfigured_embedding_never_opens_client(monkeypatch, field, value):
    import openai
    cfg = {"base_url": "https://example.invalid", "api_key": "fixture-key", "model": "fixture"}
    cfg[field] = value
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: pytest.fail("unconfigured provider contacted"))
    with pytest.raises(embedding.EmbeddingUnavailable):
        asyncio.run(embedding._embed_openai_compat(["hello"], cfg))


@pytest.mark.parametrize("failure", [None, RuntimeError("offline"), asyncio.CancelledError()])
def test_embedding_client_closes_on_success_failure_and_cancellation(monkeypatch, failure):
    import openai
    from core import api_call_log
    response = SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 2.0])])
    create = AsyncMock(side_effect=failure) if failure else AsyncMock(return_value=response)
    client = SimpleNamespace(embeddings=SimpleNamespace(create=create), close=AsyncMock())
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: client)
    monkeypatch.setattr(api_call_log, "append", lambda **kwargs: None)
    cfg = {"base_url": "https://example.invalid", "api_key": "fixture-key", "model": "fixture", "batch_size": 1}
    if failure:
        expected = asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else embedding.EmbeddingUnavailable
        with pytest.raises(expected):
            asyncio.run(embedding._embed_openai_compat(["a", "b"], cfg))
    else:
        assert asyncio.run(embedding._embed_openai_compat(["a", "b"], cfg)) == [[1.0, 2.0], [1.0, 2.0]]
        assert create.await_count == 2
    client.close.assert_awaited_once()
