"""Venice native image generation/catalogue; credentials and provider bodies stay private."""
import base64
import time

import httpx

from core import api_call_log
from core.model_network import make_model_http_client


class VeniceError(ValueError):
    def __init__(self, category: str):
        self.error_category = category
        super().__init__("Venice 生图失败（" + category + "）")


def api_root(url: str) -> str:
    url = url.rstrip("/")
    return url[:-len("/image/generate")] if url.endswith("/image/generate") else url


async def discover(base_url: str, api_key: str) -> dict:
    client = make_model_http_client(None, httpx.Timeout(20, connect=8))
    try:
        response = await client.get(api_root(base_url) + "/models", params={"type": "image"},
                                    headers={"Authorization": "Bearer " + api_key}, follow_redirects=False)
        response.raise_for_status()
        rows = response.json().get("data", [])
        names = {row["id"]: str((row.get("model_spec") or {}).get("name") or row["id"])
                 for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)}
        return {"status": "ok" if names else "empty", "models": list(names), "model_names": names}
    except httpx.HTTPStatusError as exc:
        return {"status": "unauthorized" if exc.response.status_code in (401, 403) else "http_error", "models": []}
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return {"status": "connection_or_protocol_error", "models": []}
    finally:
        await client.aclose()


async def request_image(prompt: str, resolved: dict, *, image_bytes=None, width=None, height=None) -> bytes:
    from core.image_gen import MAX_IMAGE_BYTES
    if image_bytes is not None:
        raise ValueError("此 Venice 生图连接暂不支持参考图，请使用支持参考图的连接。")
    body = {**(resolved.get("extra_params") or {}), "model": resolved["model"], "prompt": prompt,
            "format": "png", "return_binary": False, "variants": 1}
    body["width"] = width or body.get("width", 1024)
    body["height"] = height or body.get("height", 1024)
    client = make_model_http_client(None, httpx.Timeout(120, connect=10))
    started = time.perf_counter()
    ok, size, category = False, 0, "protocol_incompatible"
    try:
        response = await client.post(api_root(resolved["base_url"]) + "/image/generate",
                                     headers={"Authorization": "Bearer " + resolved["api_key"]}, json=body, follow_redirects=False)
        response.raise_for_status()
        images = response.json().get("images")
        b64 = images[0] if isinstance(images, list) and images else None
        if not isinstance(b64, str) or len(b64) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
            raise ValueError("invalid image response")
        raw = base64.b64decode(b64, validate=True)
        if not raw or len(raw) > MAX_IMAGE_BYTES:
            raise ValueError("invalid image size")
        size, ok = len(raw), True
        return raw
    except Exception as exc:
        from core.llm_protocol import error_category_for_exception
        category = "insufficient_balance" if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 402 else error_category_for_exception(exc)
        raise VeniceError(category) from exc
    finally:
        await client.aclose()
        api_call_log.append(caller="image_gen", purpose="generate_image", provider="venice", model=resolved["model"],
                            duration_ms=int((time.perf_counter() - started) * 1000), ok=ok,
                            output_hint=f"{size} bytes" if ok else "", protocol="venice_image_generate",
                            error_category="" if ok else category)
