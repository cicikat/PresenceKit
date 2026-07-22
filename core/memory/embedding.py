"""
core/memory/embedding.py
========================
Single boundary for all text→vector encoding. No other module may call embedding
HTTP endpoints directly — import embed() from here instead.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class EmbeddingUnavailable(Exception):
    """Raised when the provider is unreachable, misconfigured, or unimplemented."""


async def embed(texts: list[str]) -> list[list[float]]:
    """Encode texts into embedding vectors. Raises EmbeddingUnavailable on failure."""
    cfg = _load_config()
    kind = cfg.get("provider_kind", "")
    if kind == "openai_compat":
        return await _embed_openai_compat(texts, cfg)
    if kind == "self_hosted":
        raise EmbeddingUnavailable("self_hosted provider not implemented in stage A")
    raise EmbeddingUnavailable(
        f"embedding.provider_kind={kind!r} not configured or unknown"
    )


def _load_config() -> dict:
    try:
        from core.config_loader import get_config
        cfg = get_config().get("embedding", {})
        if not cfg:
            raise EmbeddingUnavailable("embedding block missing in config.yaml")
        return cfg
    except EmbeddingUnavailable:
        raise
    except Exception as e:
        raise EmbeddingUnavailable(f"cannot load embedding config: {e}") from e


async def _embed_openai_compat(texts: list[str], cfg: dict) -> list[list[float]]:
    try:
        from openai import AsyncOpenAI
    except ImportError as e:
        raise EmbeddingUnavailable("openai package not installed") from e

    base_url: str = cfg.get("base_url", "")
    api_key: str = cfg.get("api_key", "")
    model: str = cfg.get("model", "")
    batch_size: int = int(cfg.get("batch_size", 32))

    if not base_url or not api_key or not model:
        raise EmbeddingUnavailable(
            "embedding config requires base_url, api_key, and model"
        )

    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    results: list[list[float]] = []
    started_at = time.perf_counter()
    try:
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            resp = await client.embeddings.create(model=model, input=batch)
            results.extend(item.embedding for item in resp.data)
    except EmbeddingUnavailable:
        raise
    except Exception as e:
        from core.api_call_log import append
        append(caller="embedding", purpose="encode", provider="openai_compat", model=model, duration_ms=int((time.perf_counter() - started_at) * 1000), ok=False, output_hint=type(e).__name__)
        raise EmbeddingUnavailable(f"embedding API call failed: {e}") from e
    from core.api_call_log import append
    append(caller="embedding", purpose="encode", provider="openai_compat", model=model, duration_ms=int((time.perf_counter() - started_at) * 1000), ok=True, output_hint=f"{len(texts)}_texts")
    return results
