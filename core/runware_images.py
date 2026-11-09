"""Runware native Models API adapter. No provider bodies or credentials in errors."""
import base64
import re
import time
import uuid

import httpx

from core import api_call_log
from core.model_network import make_model_http_client


class RunwareError(ValueError):
    def __init__(self, code, *, status=None, task_id=""):
        self.status = status
        self.task_id = task_id
        code = str(code or (f"http_{status}" if status else "invalid_response"))
        safe_code = code if re.fullmatch(r"[A-Za-z0-9_]{1,55}", code) else "request_failed"
        self.error_category = "insufficient_balance" if status == 402 else "runware_" + safe_code
        detail = "账户余额不足" if status == 402 else self.error_category
        if status is not None:
            detail += f"，HTTP {status}，{safe_code}" if status == 402 else f"，HTTP {status}"
        super().__init__("Runware 请求失败（" + detail + "）")


async def post_task(base_url: str, api_key: str, task: dict) -> dict:
    task = {**task, "taskUUID": str(uuid.uuid4())}
    client = make_model_http_client(None, httpx.Timeout(120, connect=10))
    try:
        response = await client.post(base_url.rstrip("/"), headers={"Authorization": "Bearer " + api_key},
                                     json=[task], follow_redirects=False)
        status = response.status_code if not response.is_success else None
        try:
            payload = response.json()
        except ValueError:
            if status is not None:
                raise RunwareError(None, status=status, task_id=task["taskUUID"]) from None
            raise
        errors = payload.get("errors") or payload.get("error") if isinstance(payload, dict) else None
        if errors:
            error = errors[0] if isinstance(errors, list) else errors
            code = error.get("code") if isinstance(error, dict) else None
            raise RunwareError(code, status=status, task_id=task["taskUUID"])
        if status is not None:
            raise RunwareError(None, status=status, task_id=task["taskUUID"])
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            raise RunwareError("invalid_response")
        result = next((row for row in rows if isinstance(row, dict) and row.get("taskUUID") == task["taskUUID"]), None)
        if result is None:
            raise RunwareError("missing_task_result")
        return result
    finally:
        await client.aclose()


async def discover(base_url: str, api_key: str, search: str = "FLUX") -> dict:
    try:
        result = await post_task(base_url, api_key, {"taskType": "modelSearch", "search": search or "FLUX",
                                "category": "checkpoint", "limit": 100, "offset": 0})
        rows = result.get("results", [])
        models = list(dict.fromkeys(row["air"] for row in rows if isinstance(row, dict) and isinstance(row.get("air"), str)))
        return {"status": "ok" if models else "empty", "models": models,
                "has_more": int(result.get("totalResults") or 0) > len(models)}
    except RunwareError as exc:
        if exc.status in (401, 403):
            return {"status": "unauthorized", "models": []}
        if exc.status is not None:
            return {"status": "http_error", "models": []}
        return {"status": exc.error_category, "models": []}
    except httpx.TimeoutException:
        return {"status": "timeout", "models": []}
    except httpx.HTTPStatusError as exc:
        return {"status": "unauthorized" if exc.response.status_code in (401, 403) else "http_error", "models": []}
    except (httpx.HTTPError, ValueError, TypeError):
        return {"status": "connection_or_protocol_error", "models": []}


async def request_image(prompt: str, resolved: dict, *, image_bytes=None, width=None, height=None) -> bytes:
    from core.image_gen import MAX_IMAGE_BYTES
    extra = dict(resolved.get("extra_params") or {})
    reference_input = extra.pop("reference_input", "seedImage")
    # Connection, prompt, delivery and output count are controlled by this adapter.
    task = {**extra, "taskType": "imageInference", "model": resolved["model"], "positivePrompt": prompt,
            "numberResults": 1, "width": width or extra.get("width", 1024), "height": height or extra.get("height", 1024),
            "outputType": "base64Data", "outputFormat": "PNG", "deliveryMethod": "sync", "includeCost": True}
    if image_bytes is not None:
        raw, mime, _ = image_bytes
        uri = "data:" + mime + ";base64," + base64.b64encode(raw).decode()
        if reference_input == "referenceImages":
            task["inputs"] = {**(extra.get("inputs") or {}), "referenceImages": [uri]}
        else:
            task["inputs"] = {**(extra.get("inputs") or {}), "seedImage": uri}
            task.setdefault("strength", 0.65)
    started = time.perf_counter()
    ok = False
    category = "protocol_incompatible"
    request_id = ""
    size = 0
    try:
        result = await post_task(resolved["base_url"], resolved["api_key"], task)
        b64 = result.get("imageBase64Data")
        if not isinstance(b64, str) or len(b64) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
            raise RunwareError("invalid_image_data")
        raw = base64.b64decode(b64, validate=True)
        if not raw or len(raw) > MAX_IMAGE_BYTES:
            raise RunwareError("image_too_large")
        size = len(raw)
        ok = True
        return raw
    except RunwareError as exc:
        category = exc.error_category
        request_id = exc.task_id
        raise
    except Exception as exc:
        from core.llm_protocol import error_category_for_exception
        category = error_category_for_exception(exc)
        raise ValueError("Runware 生图失败（" + category + "）") from exc
    finally:
        api_call_log.append(caller="image_gen", purpose="generate_image", provider="runware", model=resolved["model"],
                            duration_ms=int((time.perf_counter() - started) * 1000), ok=ok,
                            output_hint=f"{size} bytes" if ok else "", protocol="runware_image_inference",
                            request_id=request_id, error_category="" if ok else category)
