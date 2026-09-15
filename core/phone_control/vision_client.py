"""手机自动化循环的视觉决策客户端。

跟 core/perception/vlm_client.py 用的是同一种 OpenAI-compatible chat/completions +
image_url 调用方式（GLM-4V 等视觉模型走这个协议）。config 读取顺序：

    phone_control_vision（专用，可选） > vision（通用视觉模型配置，Brief 56 已有）

用户自己在 config.yaml 接线：只要 `vision.base_url/model/api_key` 填好，这里不用改代码就能用；
想给手机自动化单独配一个更强/更贵的视觉模型，再加 `phone_control_vision` 段覆盖。

调用方（/phone_control/step）必须先过 sensitive_filter.check_observation()，只有通过了才
会走到这里——但这里的 system prompt 仍然要求模型自己也判断一次敏感页面，双重防线，不是因为
不信任 sensitive_filter，是因为截图里可能有关键词覆盖不到的视觉线索（比如银行卡实拍图）。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

VALID_STATUSES = frozenset({"continue", "done", "need_confirmation"})
VALID_ACTION_TYPES = frozenset({"tap", "type", "scroll"})
MAX_STEPS_DEFAULT = 20
STEP_TIMEOUT_SECONDS_DEFAULT = 180

_SYSTEM_PROMPT = """你在帮用户远程操作她自己的安卓手机，完成一个明确的多步任务。你每次只能看到当前这一屏（截图 + 可点击元素列表），不知道之前发生了什么，只能靠传入的任务描述和历史动作摘要判断进度。

安全铁律，比完成任务优先级更高：
1. 只要画面出现密码输入、支付确认、银行卡号、验证码、转账、银行 App 等任何和资金/账户凭证相关的迹象，立刻停止，返回 status="need_confirmation"，不要点任何东西，不要尝试"绕过"或"帮用户填写"。
2. 拿不准某个按钮会不会触发扣款/提交订单/发送消息给别人这类不可撤销的操作时，同样返回 need_confirmation，而不是自己赌一把。
3. 只输出 JSON，不要输出任何解释文字。

只输出这样的 JSON：
{"status": "continue|done|need_confirmation", "action": {"type": "tap|type|scroll", "target_node_id": "n1 或 null", "target_point": [0.5, 0.5] 或 null, "text": "仅 type 需要", "direction": "仅 scroll 需要，up/down/left/right"} 或 null, "reasoning": "不超过40字，简述这一步为什么这么做", "message": "仅 need_confirmation 时必填，给用户看的一句话说明"}

status=continue 时 action 必填；status=done 或 need_confirmation 时 action 必须为 null。
target_node_id 优先使用传入的节点列表里的 id；找不到匹配节点、只能靠截图定位图标类按钮时，
才用 target_point（归一化坐标，0~1，不是像素坐标）。"""


@dataclass(frozen=True)
class NextAction:
    status: str
    action: dict | None
    reasoning: str
    message: str | None = None


def get_phone_control_vision_config() -> dict:
    from core.config_loader import get_config
    from core.image_presets import resolve_purpose

    cfg = get_config()
    try:
        route = resolve_purpose("phone_automation", cfg)
        if route.get("kind") == "vision":
            merged = dict(route.get("config") or {})
            if route.get("synthesized") or route.get("name") in ("phone", "general"):
                dedicated = dict(cfg.get("phone_control_vision") or {})
                merged.update({k: v for k, v in dedicated.items() if v is not None and v != ""})
            return merged
    except KeyError:
        pass
    dedicated = dict(cfg.get("phone_control_vision") or {})
    general = dict(cfg.get("vision") or {})
    merged = dict(general)
    # 空字符串表示“继承通用 vision”；False 是合法的显式布尔覆盖，不能被当成空值丢掉。
    merged.update({k: v for k, v in dedicated.items() if v is not None and v != ""})
    return merged


def _parse_action_payload(raw: object) -> NextAction | None:
    if not isinstance(raw, dict):
        return None
    status = raw.get("status")
    if status not in VALID_STATUSES:
        return None
    action = raw.get("action")
    if status == "continue":
        if not isinstance(action, dict) or action.get("type") not in VALID_ACTION_TYPES:
            return None
        if action["type"] == "type" and not isinstance(action.get("text"), str):
            return None
        if action["type"] == "scroll" and action.get("direction") not in (
            "up", "down", "left", "right",
        ):
            return None
        has_node = isinstance(action.get("target_node_id"), str) and action["target_node_id"]
        point = action.get("target_point")
        has_point = (
            isinstance(point, list) and len(point) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in point)
        )
        if action["type"] in ("tap", "type") and not has_node and not has_point:
            return None
    else:
        action = None
    reasoning = raw.get("reasoning")
    if not isinstance(reasoning, str):
        reasoning = ""
    message = raw.get("message")
    if status == "need_confirmation" and not isinstance(message, str):
        message = "识别到需要人工确认的内容，已暂停自动操作"
    return NextAction(
        status=status,
        action=action,
        reasoning=reasoning[:120],
        message=message if isinstance(message, str) else None,
    )


async def decide_next_action(
    *,
    task: str,
    package_name: str,
    screen_title: str,
    nodes: list[dict],
    screenshot_base64: str | None,
    history_summary: str = "",
) -> tuple[NextAction | None, str | None]:
    """返回 (NextAction | None, error_reason | None)。

    error_reason 为 None 时表示成功；否则是 "disabled"/"unconfigured"/"invalid"/"error" 之一，
    调用方（/phone_control/step）在任一失败时都必须把 status 降级为 refused，不能假装继续。
    """
    cfg = get_phone_control_vision_config()
    base_url = str(cfg.get("base_url") or "").rstrip("/")
    model = str(cfg.get("model") or "")
    if not base_url or not model:
        logger.warning("[phone_control.vision] base_url/model 未配置，phone_control_start 无法真正执行")
        return None, "unconfigured"

    user_content: list[dict] = [
        {
            "type": "text",
            "text": (
                f"任务：{task}\n当前 App 包名：{package_name}\n当前页面标题：{screen_title}\n"
                f"可点击元素（node id / 文本 / 描述 / 坐标范围）：\n{json.dumps(nodes, ensure_ascii=False)}\n"
                f"此前动作摘要：{history_summary or '（第一步）'}"
            ),
        },
    ]
    if screenshot_base64:
        user_content.append({
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64," + screenshot_base64},
        })

    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }
    headers = {"Authorization": f"Bearer {cfg.get('api_key', '')}"} if cfg.get("api_key") else {}

    started_at = time.perf_counter()
    try:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=float(cfg.get("timeout_s", 25)))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(base_url + "/chat/completions", json=payload, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
        content = data["choices"][0]["message"]["content"]
        raw = json.loads(content) if isinstance(content, str) else content
        parsed = _parse_action_payload(raw)
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        from core.api_call_log import append
        if parsed is None:
            append(
                caller="phone_control", purpose="next_action",
                provider=str(cfg.get("provider") or "openai_compatible"), model=model,
                duration_ms=duration_ms, ok=False, output_hint="invalid_response",
            )
            logger.warning("[phone_control.vision] 响应解析失败 model=%s raw=%r", model, raw)
            return None, "invalid"
        append(
            caller="phone_control", purpose="next_action",
            provider=str(cfg.get("provider") or "openai_compatible"), model=model,
            duration_ms=duration_ms, ok=True,
        )
        return parsed, None
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        try:
            from core.api_call_log import append
            append(
                caller="phone_control", purpose="next_action",
                provider=str(cfg.get("provider") or "openai_compatible"), model=model,
                duration_ms=duration_ms, ok=False, output_hint=type(exc).__name__,
            )
        except Exception:
            pass
        logger.warning("[phone_control.vision] 调用失败: %s", exc)
        return None, "error"
