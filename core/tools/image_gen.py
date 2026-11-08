"""generate_image tool: OpenAI-compatible image generation, artifacts delivery."""
from __future__ import annotations

import base64
import json
import logging
import time

import httpx

from core import image_gen, api_call_log
from core.model_network import make_model_http_client
from core.tools.chat_artifacts import ArtifactError, store_generated_image
from core.tools.tool_result import ToolResult

logger = logging.getLogger(__name__)

_TIMEOUT_S = 120.0


async def generate_image(
    prompt: str,
    preset: str | None = None,
    width: int | None = None,
    height: int | None = None,
    reference_sha256: str | None = None,
    reference_artifact_id: str | None = None,
    *,
    user_id: str | None = None,
    char_id: str | None = None,
) -> ToolResult:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt 不能为空。")
    if len(prompt) > image_gen.MAX_PROMPT_CHARS:
        raise ValueError(f"prompt 不能超过 {image_gen.MAX_PROMPT_CHARS} 个字符。")
    has_ref = reference_sha256 is not None or reference_artifact_id is not None
    resolved = image_gen.resolve_preset(preset, char_id)
    if has_ref and resolved.get("supports_edits", True) is False:
        raise ValueError(f"图像生成预设 {resolved['name']} 不支持参考图。")
    image_bytes: tuple[bytes, str, str] | None = None
    if has_ref:
        image_bytes = image_gen.image_reference_bytes(
            user_id=user_id,
            char_id=char_id,
            sha256=reference_sha256,
            artifact_id=reference_artifact_id,
        )
        if len(image_bytes[0]) > 20 * 1024 * 1024:
            raise ValueError("参考图总尺寸超过 20MB 上限。")
    raw = await request_image(prompt, resolved, image_bytes=image_bytes, width=width, height=height)
    stored = store_generated_image(raw, ext=".png", user_id=user_id, char_id=char_id)
    raw = json.dumps({
            "status": "generated",
            "id": stored["id"],
            "filename": stored["filename"],
            "mime": stored["mime"],
            "size": stored["size"],
            "note": "图片已生成，用户会看到图片卡片；不要说出工具名，也不要编造本机路径。",
        }, ensure_ascii=False)
    return ToolResult(
        raw_data=raw,
        safe_summary=raw,
        meta={"validity": "current_turn"},
    )


async def request_image(prompt: str, resolved: dict, *, image_bytes=None, width=None, height=None) -> bytes:
    """Shared production/probe request; never stores an artifact or touches chat state."""
    prompt = image_gen.constrained_prompt(prompt.strip())
    if resolved.get("provider_kind") == "venice":
        from core.venice_images import request_image as venice_request
        return await venice_request(prompt, resolved, image_bytes=image_bytes, width=width, height=height)
    if resolved.get("provider_kind") == "runware":
        from core.runware_images import request_image as runware_request
        return await runware_request(prompt, resolved, image_bytes=image_bytes, width=width, height=height)
    if image_bytes is not None:
        url = resolved["base_url"].rstrip("/") + "/images/edits"
        headers = {"User-Agent": "PresenceKit/1.0"}
        if resolved["api_key"]:
            headers["Authorization"] = f"Bearer {resolved['api_key']}"
        started = time.perf_counter()
        client = make_model_http_client(None, httpx.Timeout(timeout=_TIMEOUT_S, connect=10.0))
        try:
            ref_data, ref_mime, _src = image_bytes
            ext = ref_mime.split("/")[-1].replace("jpeg", "jpg")
            response = await client.post(
                url,
                headers=headers,
                files={"image": (f"reference.{ext}", ref_data, ref_mime)},
                data={"model": resolved["model"], "prompt": prompt.strip(), "n": "1", "response_format": "b64_json"},
            )
            response.raise_for_status()
        except Exception as exc:
            _log_failure(resolved, started, exc)
            raise ValueError(f"生成图失败（预设 {resolved['name']}，{image_gen_httpx_error_category(exc)}）。") from exc
        finally:
            try:
                await client.aclose()
            except Exception:
                pass
    else:
        body: dict = {
            "model": resolved["model"],
            "prompt": prompt.strip(),
            "n": 1,
            "response_format": "b64_json",
        }
        extra = resolved.get("extra_params") or {}
        body.update(extra)
        body["prompt"] = prompt
        if width or height:
            body.setdefault("size", f"{int(width) if width else 1024}x{int(height) if height else 1024}")
        url = resolved["base_url"].rstrip("/") + "/images/generations"
        headers = {"Content-Type": "application/json", "User-Agent": "PresenceKit/1.0"}
        if resolved["api_key"]:
            headers["Authorization"] = f"Bearer {resolved['api_key']}"
        started = time.perf_counter()
        client = make_model_http_client(None, httpx.Timeout(timeout=_TIMEOUT_S, connect=10.0))
        try:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
        except Exception as exc:
            _log_failure(resolved, started, exc)
            raise ValueError(f"生成图失败（预设 {resolved['name']}，{image_gen_httpx_error_category(exc)}）。") from exc
        finally:
            try:
                await client.aclose()
            except Exception:
                pass
    try:
        payload = response.json()
    except Exception as exc:
        api_call_log.append(
            caller="image_gen",
            purpose="generate_image",
            provider=resolved["provider_kind"],
            model=resolved["model"],
            duration_ms=int((time.perf_counter() - started) * 1000),
            ok=False,
            output_hint="",
            protocol="images_generations",
            error_category="protocol_incompatible",
        )
        raise ValueError("生成图返回的不是有效 JSON。") from exc
    data = (payload.get("data") or [{}])[0] if isinstance(payload, dict) else {}
    b64 = data.get("b64_json") if isinstance(data, dict) else None
    if not b64:
        api_call_log.append(
            caller="image_gen",
            purpose="generate_image",
            provider=resolved["provider_kind"],
            model=resolved["model"],
            duration_ms=int((time.perf_counter() - started) * 1000),
            ok=False,
            output_hint="",
            protocol="images_generations",
            error_category="protocol_incompatible",
        )
        raise ValueError("生成图返回缺少图片数据。")
    try:
        raw = base64.b64decode(str(b64), validate=True)
    except Exception as exc:
        api_call_log.append(
            caller="image_gen",
            purpose="generate_image",
            provider=resolved["provider_kind"],
            model=resolved["model"],
            duration_ms=int((time.perf_counter() - started) * 1000),
            ok=False,
            output_hint="",
            protocol="images_generations",
            error_category="protocol_incompatible",
        )
        raise ValueError("生成图返回的数据不是有效 base64。") from exc
    if len(raw) > image_gen.MAX_IMAGE_BYTES:
        raise ValueError("生成的图片超过 25MB 上限。")
    api_call_log.append(
        caller="image_gen",
        purpose="generate_image",
        provider=resolved["provider_kind"],
        model=resolved["model"],
        duration_ms=int((time.perf_counter() - started) * 1000),
        ok=True,
        output_hint=f"{len(raw)} bytes",
        protocol="images_generations",
    )
    return raw


def _log_failure(resolved: dict, started: float, exc: BaseException) -> None:
    status = getattr(exc, "response", None)
    error_category = image_gen_httpx_error_category(exc)
    api_call_log.append(
        caller="image_gen",
        purpose="generate_image",
        provider=resolved["provider_kind"],
        model=resolved["model"],
        duration_ms=int((time.perf_counter() - started) * 1000),
        ok=False,
        output_hint="",
        protocol="images_generations",
        error_category=error_category,
    )


def image_gen_httpx_error_category(exc: BaseException) -> str:
    from core.llm_protocol import error_category_for_exception

    return error_category_for_exception(exc) if not isinstance(exc, (TimeoutError,)) else "timeout"


def register_tools(registry: dict) -> None:
    async def _generate(prompt, preset=None, width=None, height=None, reference_sha256=None, reference_artifact_id=None, *, user_id, char_id):
        return await generate_image(prompt, preset=preset, width=width, height=height,
                                    reference_sha256=reference_sha256,
                                    reference_artifact_id=reference_artifact_id,
                                    user_id=user_id, char_id=char_id)

    registry["generate_image"] = {
        "func": _generate,
        "description": "按提示词生成图片。先加载 media 分类，再用本工具；preset 选你想用的图像预设（省略用默认），不要编造本机路径。传 reference_sha256 或 reference_artifact_id 时走图生图（edits），prompt 是改图指令。",
        "category": "media",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "画面描述（英文常用），写清主体、氛围、风格。参考图模式时是改图指令（保留什么、改成什么）。"},
                "preset": {"type": "string", "description": "可选：要用的图像预设名；省略用默认预设。"},
                "width": {"type": "integer", "description": "可选：宽度，默认由预设决定。"},
                "height": {"type": "integer", "description": "可选：高度，默认由预设决定。"},
                "reference_sha256": {"type": "string", "description": "可选：参考图 sha256（本轮图片消息或资料库）。与 reference_artifact_id 二选一。"},
                "reference_artifact_id": {"type": "string", "description": "可选：之前生成的图 id。与 reference_sha256 二选一。"},
            },
            "required": ["prompt"],
        },
        "dangerous": False,
        "effect": "write",
        "examples": ["画一张图", "帮我生成图片"],
        "keywords": ["生成图片", "画图", "生成图", "画一张"],
        "trace_args": ["preset"],
        "trace_result": False,
        "echo_event_log": False,
        "image_gen": True,
    }
