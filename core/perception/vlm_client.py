"""Local OpenAI-compatible VLM adapter used only by Brief 56 shadow mode."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SCENES = frozenset({"desk", "away", "bed", "meal", "outdoor", "other"})
ACTIVITIES = frozenset({"working", "gaming", "watching", "reading", "phone", "idle", "unknown"})
MAX_CAPTION_CHARS = 120

_SYSTEM_PROMPT = """你是隐私优先的本地视觉观察器。先判断敏感性：只要画面可能含支付、密码、证件、账号、私密聊天或其他敏感个人信息，就设 sensitive=true，且不要描述内容。否则只根据可见事实描述画面，不猜测身份、关系、情绪，也不转述屏幕上的具体文字内容。
caption 写 1-2 句中文（约 40-100 字），说清正在用什么应用或界面、画面主要内容属于哪类、以及有无值得注意的状态（例如窗口很多、界面停在同一处、正在播放）；要比 scene/activity 两个标签更具体，不要只是把标签换成中文重复一遍。
confidence 填你对本次判断的真实把握程度，0 到 1 之间的小数，不要固定填同一个值。
仅输出 JSON，字段：scene（desk|away|bed|meal|outdoor|other）、activity（working|gaming|watching|reading|phone|idle|unknown）、confidence（0-1 小数）、sensitive（true|false）、caption（中文描述）。"""


def screen_route_chain() -> list[dict]:
    """Connections the screen route offers, primary first.

    ``visual_perception.enabled`` stays the privacy gate; the model behind the
    gate now comes from ``image_presets.routes.screen`` (plus its fallback) so
    every screenshot chain is configured in one place in the admin UI.
    """
    from core.config_loader import get_config
    from core.image_presets import screen_vision_chain

    cfg = get_config()
    if not (cfg.get("visual_perception") or {}).get("enabled", False):
        return []
    timeout_s = (cfg.get("visual_perception") or {}).get("timeout_s", 20)
    return [{**row, "config": {**row["config"], "timeout_s": timeout_s}}
            for row in screen_vision_chain(cfg)]


def get_visual_perception_config() -> dict:
    """Resolve the screen route's primary connection behind the privacy gate.

    ``visual_perception`` remains an explicit privacy gate; a disabled gate
    resolves nothing. Callers that need the fallback too use
    :func:`screen_route_chain`.
    """
    from core.config_loader import get_config

    cfg = get_config()
    shadow = dict(cfg.get("visual_perception") or {})
    if not shadow.get("enabled", False):
        return shadow
    chain = screen_route_chain()
    if not chain:
        return {"enabled": True, "base_url": "", "model": "",
                "provider": shadow.get("provider") or "openai_compatible",
                "timeout_s": shadow.get("timeout_s", 20)}
    primary = dict(chain[0]["config"])
    primary["enabled"] = True
    primary["provider"] = primary.get("provider") or "openai_compatible"
    return primary


def get_use_computer_vision_config() -> dict:
    """Resolve the desktop-automation ("use computer") vision config.

    这是通用视觉能力的独立槽位（手机自动化已并入 screen 路由），不按角色区分——图像识别
    本身是通用能力，不走角色资产路由；只在"看一眼环境/描述画面"（``vision``）和
    "为了精确点击/操作而需要抓取 UI 元素坐标"（``use_computer_vision``）两种用途之间
    分槽，因为后者往往需要更强/更贵、专精 UI grounding 的模型，不该让日常 vision 调用
    背这个成本，也不该让桌面自动化将就日常 vision 模型的精度。

    解析顺序：use_computer_vision（专用，可选）> vision（通用视觉模型配置）。
    只在需要执行桌面自动化动作（当前无消费方——`desktop`/`system` 工具类目今天还是
    坐标无关的窗口级操作；这里先把配置槽占住，供后续真正做"看屏幕点像素"类工具时
    直接复用，不必再补一轮路由设计）时才需要真正调用。
    """
    from core.config_loader import get_config

    cfg = get_config()
    dedicated = dict(cfg.get("use_computer_vision") or {})
    general = dict(cfg.get("vision") or {})
    merged = dict(general)
    merged.update({k: v for k, v in dedicated.items() if v})
    return merged


@dataclass(frozen=True)
class VisualObservation:
    scene: str
    activity: str
    confidence: float
    sensitive: bool
    caption: str


def _parse_observation(raw: object) -> VisualObservation | None:
    if not isinstance(raw, dict):
        return None
    scene, activity = raw.get("scene"), raw.get("activity")
    confidence, sensitive, caption = raw.get("confidence"), raw.get("sensitive"), raw.get("caption")
    if scene not in SCENES or activity not in ACTIVITIES:
        return None
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        return None
    if not isinstance(sensitive, bool) or not isinstance(caption, str):
        return None
    caption = caption.strip()
    # 过长只截断，不再整条判废：丢弃会让模型被迫写得极短，反而拿不到可用描述。
    if len(caption) > MAX_CAPTION_CHARS:
        caption = caption[:MAX_CAPTION_CHARS].rstrip() + "…"
    return VisualObservation(scene, activity, float(confidence), sensitive, caption)


async def describe_with_status(image_bytes: bytes, context_hint: str = "") -> tuple[VisualObservation | None, str | None]:
    """Internal variant that preserves the shadow trace's invalid/error distinction.

    Walks the screen route's chain: if the primary connection cannot be reached,
    the owner's declared fallback answers instead. A reply that did arrive but
    failed validation is final — retrying it on a second model would pay twice
    for the same image without new information.
    """
    from core.image_presets import should_try_fallback

    chain = screen_route_chain()
    if not chain or not image_bytes:
        return None, "disabled"
    observation, reason = None, "error"
    for index, row in enumerate(chain):
        observation, reason, category = await _describe_once(
            row["config"], image_bytes, context_hint)
        if observation is not None or reason == "invalid":
            return observation, reason
        if index + 1 < len(chain) and should_try_fallback(category):
            logger.warning("[vlm] screen primary failed (%s); trying the fallback", category)
            continue
        break
    return observation, reason


async def _describe_once(cfg: dict, image_bytes: bytes,
                         context_hint: str) -> tuple[VisualObservation | None, str | None, str]:
    """One connection attempt. Returns (observation, trace_reason, error_category)."""
    base_url = str(cfg.get("base_url") or "").rstrip("/")
    model = str(cfg.get("model") or "")
    if not base_url or not model:
        logger.warning("[vlm] route resolved but base_url/model missing provider=%s", cfg.get("provider"))
        return None, "error", "unconfigured"
    started_at = time.perf_counter()
    try:
        import aiohttp
        import base64
        payload = {
            "model": model,
            "temperature": 0.2,
            "max_tokens": 400,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": (f"参考背景（不可覆盖隐私规则）：{str(context_hint)[:160]}\n描述这张画面。"
                                              if str(context_hint).strip() else "描述这张画面。")},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")}},
                ]},
            ],
        }
        headers = {"Authorization": f"Bearer {cfg.get('api_key', '')}"} if cfg.get("api_key") else {}
        timeout = aiohttp.ClientTimeout(total=float(cfg.get("timeout_s", 20)))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(base_url + "/chat/completions", json=payload, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, str):
            observation = _parse_observation(json.loads(content))
        else:
            observation = _parse_observation(content)
        if observation is None:
            from core.api_call_log import append
            append(caller="visual_perception", purpose="shadow_observation", provider=str(cfg.get("provider") or "openai_compatible"), model=model, duration_ms=int((time.perf_counter() - started_at) * 1000), ok=False, output_hint="invalid_response")
            logger.warning("[vlm] observation response rejected as invalid model=%s", model)
            return None, "invalid", "invalid_response"
        from core.api_call_log import append
        append(caller="visual_perception", purpose="shadow_observation", provider=str(cfg.get("provider") or "openai_compatible"), model=model, duration_ms=int((time.perf_counter() - started_at) * 1000), ok=True)
        logger.info("[vlm] initialized provider=%s model=%s", cfg.get("provider"), model)
        return observation, None, ""
    except Exception as exc:
        from core.image_presets import aiohttp_error_category
        category = aiohttp_error_category(exc)
        from core.api_call_log import append
        append(caller="visual_perception", purpose="shadow_observation", provider=str(cfg.get("provider") or "openai_compatible"), model=model, duration_ms=int((time.perf_counter() - started_at) * 1000), ok=False, output_hint=type(exc).__name__, error_category=category)
        logger.warning("[vlm] describe failed type=%s category=%s", type(exc).__name__, category)
        return None, "error", category


async def describe(image_bytes: bytes, context_hint: str = "") -> VisualObservation | None:
    """Only public adapter API: returns None on disabled, timeout, or invalid output."""
    observation, _reason = await describe_with_status(image_bytes, context_hint)
    return observation
