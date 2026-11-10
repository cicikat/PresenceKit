"""Serve a pre-provisioned GPT-SoVITS resource manifest on a trusted network."""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from fastapi import FastAPI, HTTPException, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from core.output.gsv_node import GsvNodeError, MAX_WAV_BYTES, RESOURCE_ID_PATTERN, SpeechRequest, node_url_allowed, read_wav
from core.output.voice_adapter import _join_pcm_wavs

_RESOURCE_SUFFIXES = {"reference": {".wav", ".flac", ".mp3", ".ogg"}, "gpt": {".ckpt"}, "sovits": {".pth"}}


def load_resources(root: Path, manifest: Path) -> dict[str, dict[str, Path]]:
    root = root.resolve(strict=True)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != set(_RESOURCE_SUFFIXES):
        raise ValueError("Manifest must contain reference, gpt and sovits maps")
    resources = {}
    for group, suffixes in _RESOURCE_SUFFIXES.items():
        if not isinstance(data[group], dict) or not data[group]:
            raise ValueError(f"Missing resource map: {group}")
        resources[group] = {}
        for key, value in data[group].items():
            if not re.fullmatch(RESOURCE_ID_PATTERN, key) or not isinstance(value, str):
                raise ValueError(f"Invalid resource entry: {group}")
            relative = Path(value)
            if relative.is_absolute() or relative.drive or ".." in relative.parts:
                raise ValueError(f"Expected a path relative to resource root: {group}")
            path = (root / relative).resolve()
            if not path.is_relative_to(root) or path.suffix.lower() not in suffixes:
                raise ValueError(f"Resource escapes its root or has the wrong file type: {group}")
            resources[group][key] = path
    return resources


def create_app(*, api_url: str, key: str, resources: dict[str, dict[str, Path]]) -> FastAPI:
    if (not key or not node_url_allowed(api_url)
            or urlsplit(api_url).hostname not in {"localhost", "127.0.0.1", "::1"}):
        raise ValueError("A node token and loopback GPT-SoVITS API are required")
    base = api_url.rstrip("/")
    expected = f"Bearer {key}".encode()
    lock = asyncio.Lock()
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def admit(request, call_next):
        if not hmac.compare_digest(request.headers.get("authorization", "").encode(), expected):
            return JSONResponse(status_code=401, content={"detail": "node_auth_failed"})
        length = request.headers.get("content-length", "")
        if not length.isascii() or not length.isdecimal() or len(length) > 7 or int(length) > 1_100_000:
            return JSONResponse(status_code=413, content={"detail": "node_config_invalid"})
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        return JSONResponse(status_code=422, content={"detail": "node_config_invalid"})

    @app.post("/synthesize")
    async def synthesize(body: SpeechRequest):
        paths = {group: resources[group].get(getattr(body, group)) for group in _RESOURCE_SUFFIXES}
        if any(path is None or not path.is_file() or path.resolve() != path for path in paths.values()):
            raise HTTPException(404, "resource_missing")
        if lock.locked():
            raise HTTPException(429, "node_busy")
        # One worker owns a dedicated, single-process upstream. Serialize both
        # weight changes and all segments of this utterance as a single operation.
        async with lock:
            try:
                async with (asyncio.timeout(180), aiohttp.ClientSession(trust_env=False) as session):
                    async with asyncio.timeout(body.switch_timeout):
                        for group, action in (("sovits", "set_sovits_weights"), ("gpt", "set_gpt_weights")):
                            async with session.get(f"{base}/{action}", params={"weights_path": str(paths[group])},
                                                   allow_redirects=False) as response:
                                if response.status != 200:
                                    raise HTTPException(502, "model_unavailable")
                    wavs, total = [], 0
                    for text, language in body.segments:
                        payload = {"text": text, "text_lang": language, "ref_audio_path": str(paths["reference"]),
                                   "prompt_text": body.prompt_text, "prompt_lang": body.prompt_lang,
                                   "text_split_method": "cut0", "batch_size": 1, "media_type": "wav",
                                   "speed_factor": body.speed, "top_k": body.top_k, "top_p": body.top_p,
                                   "temperature": body.temperature, "sample_steps": body.sample_steps,
                                   "super_sampling": body.super_sampling, "fragment_interval": body.fragment_interval}
                        async with session.post(f"{base}/tts", json=payload, allow_redirects=False,
                                                timeout=aiohttp.ClientTimeout(total=body.segment_timeout)) as response:
                            if response.status != 200:
                                raise HTTPException(502, "node_service_error")
                            wav = await read_wav(response)
                        total += len(wav)
                        if total > MAX_WAV_BYTES:
                            raise GsvNodeError("invalid_audio")
                        wavs.append(wav)
                result = _join_pcm_wavs(wavs, body.pause_seconds)
                if not result or len(result) > MAX_WAV_BYTES:
                    raise GsvNodeError("invalid_audio")
                return Response(content=result, media_type="audio/wav")
            except TimeoutError:
                raise HTTPException(504, "node_timeout") from None
            except aiohttp.ClientConnectionError:
                raise HTTPException(503, "node_offline") from None
            except (GsvNodeError, aiohttp.ClientError):
                raise HTTPException(502, "invalid_audio") from None

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--gsv-url", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    host = f"[{args.host}]" if ":" in args.host else args.host
    if not node_url_allowed(f"http://{host}:{args.port}"):
        parser.error("Bind to a literal private IP or loopback")
    resources = load_resources(args.root, args.manifest)
    app = create_app(api_url=args.gsv_url, key=os.environ.get("PRESENCEKIT_GSV_NODE_TOKEN", ""), resources=resources)
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, workers=1, access_log=False)


if __name__ == "__main__":
    main()
