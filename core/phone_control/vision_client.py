"""手机自动化循环的视觉决策客户端。

跟 core/perception/vlm_client.py 用的是同一种 OpenAI-compatible chat/completions +
image_url 调用方式（GLM-4V 等视觉模型走这个协议）。连接来自 image_presets 的 screen 路由
（主连接 + 可选备用连接），与影子观测、按需看屏幕共用同一条配置：

    image_presets.routes.screen → 主连接；image_presets.fallbacks.screen → 备用连接

主连接连不上（超时/连接失败/5xx 等，规则同文本模型 failover）时，由备用连接重答同一屏；
模型回了但内容不合格（invalid）不换连接重试——同一张截图不为没有新信息的重复付费。
旧的 `phone_control_vision` 专用覆盖已退役：只在首次把旧配置迁入命名连接时读取一次。

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


def phone_vision_chain() -> list[dict]:
    """Connections phone automation may use, primary first; ``[]`` when unrouted.

    Unlike shadow observation this is not behind ``visual_perception.enabled`` —
    phone control has its own consent (danger mode + per-task confirmation).
    """
    from core.config_loader import get_config
    from core.image_presets import screen_vision_chain

    return screen_vision_chain(get_config())


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

    依次尝试 screen 路由的主连接与备用连接：只有"连不上"类失败才换连接，
    模型已回复但内容不合格（invalid）直接终止。
    """
    from core.image_presets import should_try_fallback

    chain = phone_vision_chain()
    if not chain:
        logger.warning("[phone_control.vision] screen 路由未配置，phone_control_start 无法真正执行")
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

    parsed, reason = None, "error"
    for index, row in enumerate(chain):
        parsed, reason, category = await _decide_once(row["config"], user_content)
        if parsed is not None or reason == "invalid":
            return parsed, reason
        if index + 1 < len(chain) and should_try_fallback(category):
            logger.warning("[phone_control.vision] screen 主连接失败（%s），改用备用连接", category)
            continue
        break
    return parsed, reason


async def _decide_once(
    cfg: dict, user_content: list[dict],
) -> tuple[NextAction | None, str | None, str]:
    """One connection attempt. Returns (action, error_reason, error_category)."""
    base_url = str(cfg.get("base_url") or "").rstrip("/")
    model = str(cfg.get("model") or "")
    if not base_url or not model:
        logger.warning("[phone_control.vision] 连接缺 base_url/model，跳过")
        return None, "unconfigured", "unconfigured"

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
            return None, "invalid", "invalid_response"
        append(
            caller="phone_control", purpose="next_action",
            provider=str(cfg.get("provider") or "openai_compatible"), model=model,
            duration_ms=duration_ms, ok=True,
        )
        return parsed, None, ""
    except Exception as exc:
        from core.image_presets import aiohttp_error_category
        category = aiohttp_error_category(exc)
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        try:
            from core.api_call_log import append
            append(
                caller="phone_control", purpose="next_action",
                provider=str(cfg.get("provider") or "openai_compatible"), model=model,
                duration_ms=duration_ms, ok=False, output_hint=type(exc).__name__,
                error_category=category,
            )
        except Exception:
            pass
        logger.warning("[phone_control.vision] 调用失败 category=%s: %s", category, exc)
        return None, "error", category
