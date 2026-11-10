"""Private GPT-SoVITS node wire contract and client."""
from __future__ import annotations

import asyncio
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

import aiohttp
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from core.model_network import private_node_url

RESOURCE_ID_PATTERN = r"^[\w-]{1,96}$"
ResourceId = Annotated[str, StringConstraints(min_length=1, max_length=96, pattern=RESOURCE_ID_PATTERN)]
SpeechText = Annotated[str, StringConstraints(min_length=1, max_length=4000)]
Language = Literal["zh", "en", "ja", "ko", "yue", "auto", "auto_yue", "all_zh", "all_ja", "all_ko", "all_yue"]
MAX_WAV_BYTES = 32 * 1024 * 1024
NODE_ERRORS = frozenset({"node_offline", "node_busy", "node_timeout", "resource_missing", "model_unavailable",
                         "node_config_invalid", "node_auth_failed", "invalid_audio", "node_service_error",
                         "unsupported_if_freeze", "reference_text_required", "unsupported_super_sampling"})


class GsvNodeError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: ResourceId
    gpt: ResourceId
    sovits: ResourceId
    segments: list[tuple[SpeechText, Language]] = Field(min_length=1, max_length=64)
    prompt_text: str = Field(default="", max_length=500)
    prompt_lang: Language = "zh"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    pause_seconds: float = Field(default=0.25, ge=0, le=2.0)
    segment_timeout: float = Field(default=30, ge=1, le=120)
    switch_timeout: float = Field(default=60, ge=1, le=120)
    top_k: int = Field(default=15, ge=1, le=100)
    top_p: float = Field(default=1.0, ge=0, le=1)
    temperature: float = Field(default=1.0, ge=0, le=2)
    sample_steps: int = Field(default=8, ge=1, le=128)
    super_sampling: bool = False
    fragment_interval: float = Field(default=0.3, ge=0, le=2.0)


def validate_options(cfg: dict, prompt_text: str) -> None:
    if cfg.get("if_freeze"):
        raise GsvNodeError("unsupported_if_freeze")
    version = cfg.get("gsv_version") or "auto"
    if version == "v3" and (cfg.get("ref_free") or not prompt_text):
        raise GsvNodeError("reference_text_required")
    if cfg.get("if_sr") and version != "v3":
        raise GsvNodeError("unsupported_super_sampling")


def node_url_allowed(url: str) -> bool:
    if private_node_url(url):
        return True
    try:
        parsed = urlsplit(url)
        return (parsed.scheme in {"http", "https"} and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                and parsed.port != 0 and not any(ord(c) <= 32 or c in "\\%?#@" for c in url))
    except ValueError:
        return False


def resource_ids(cfg: dict, reference: str, models: tuple[str, str] | None) -> dict:
    mapping = cfg.get("node_resources")
    if not models or not isinstance(mapping, dict):
        raise GsvNodeError("node_config_invalid")
    result = {}
    for group, selector in zip(("reference", "gpt", "sovits"), (reference, *models)):
        entries = mapping.get(group)
        resource = entries.get(selector) if isinstance(entries, dict) else None
        if not isinstance(resource, str) or not re.fullmatch(RESOURCE_ID_PATTERN, resource):
            raise GsvNodeError("resource_missing")
        result[group] = resource
    return result


async def read_wav(response: aiohttp.ClientResponse) -> bytes:
    body = bytearray()
    async for chunk in response.content.iter_chunked(65536):
        body.extend(chunk)
        if len(body) > MAX_WAV_BYTES:
            raise GsvNodeError("invalid_audio")
    if len(body) <= 44 or body[:4] != b"RIFF" or body[8:12] != b"WAVE":
        raise GsvNodeError("invalid_audio")
    return bytes(body)


async def synthesize(url: str, key: str, speech: SpeechRequest) -> bytes:
    if not node_url_allowed(url) or not key:
        raise GsvNodeError("node_config_invalid")
    timeout = aiohttp.ClientTimeout(total=min(180, speech.switch_timeout + len(speech.segments) * speech.segment_timeout) + 2,
                                    connect=5)
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            async with session.post(url.rstrip("/") + "/synthesize", json=speech.model_dump(mode="json"),
                                    headers={"Authorization": "Bearer " + key}, allow_redirects=False) as response:
                if response.status == 200:
                    return await read_wav(response)
                if response.status in {401, 403}:
                    raise GsvNodeError("node_auth_failed")
                # The node only emits a small fixed vocabulary. Treat all other replies as opaque.
                if response.content_length is not None and response.content_length <= 256:
                    try:
                        detail = (await response.json()).get("detail")
                    except (ValueError, AttributeError, aiohttp.ContentTypeError):
                        detail = None
                    if isinstance(detail, str) and detail in NODE_ERRORS:
                        raise GsvNodeError(detail)
                raise GsvNodeError("node_service_error")
    except asyncio.TimeoutError as error:
        raise GsvNodeError("node_timeout") from error
    except aiohttp.ClientConnectionError as error:
        raise GsvNodeError("node_offline") from error
    except aiohttp.ClientError as error:
        raise GsvNodeError("node_service_error") from error


async def wait_for_recovery(url: str, key: str, *, budget: float = 30) -> None:
    """Keep a timed-out node's shared slot reserved while its engine drains."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget
    async with aiohttp.ClientSession(trust_env=False) as session:
        while (remaining := deadline - loop.time()) > 0:
            try:
                async with session.get(url.rstrip("/") + "/ready", headers={"Authorization": "Bearer " + key},
                                       allow_redirects=False, timeout=aiohttp.ClientTimeout(total=min(3, remaining))) as response:
                    if response.status == 204:
                        return
            except (TimeoutError, aiohttp.ClientError):
                pass
            await asyncio.sleep(min(1, max(0, deadline - loop.time())))
