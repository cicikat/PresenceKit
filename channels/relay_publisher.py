"""Signal-only publisher for waking mobile relay subscribers."""

import asyncio
import logging
from collections.abc import Mapping

import httpx

from core.config_loader import get_config

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_REQUEST_TIMEOUT_SECONDS = 10.0
_publish_tasks: set[asyncio.Task] = set()
_warned_unconfigured = False


def _on_publish_done(task: asyncio.Task) -> None:
    _publish_tasks.discard(task)
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.warning("[relay_publisher] background publish failed: %s", exc)


def _relay_config() -> tuple[str, str, str] | None:
    # token 可选：无鉴权的自建 ntfy 不需要（手机端订阅侧同样按可选处理）。
    config = get_config()
    base_url = str(config.get("relay_base_url") or "").strip().rstrip("/")
    topic = str(config.get("relay_topic") or "").strip().strip("/")
    token = str(config.get("relay_token") or "").strip()
    if not base_url or not topic:
        return None
    return base_url, topic, token


def schedule_signal_publish(queue_item: Mapping) -> None:
    """Publish after queue persistence without delaying the enqueue caller."""
    try:
        relay_config = _relay_config()
    except Exception as exc:
        logger.warning("[relay_publisher] relay config unavailable: %s", exc)
        return
    if relay_config is None:
        global _warned_unconfigured
        if not _warned_unconfigured:
            _warned_unconfigured = True
            logger.warning(
                "[relay_publisher] relay_base_url/relay_topic 未配置："
                "mobile 消息已入队但不会实时唤醒手机，后台推送只剩手机端周期补偿轮询。"
                "参见 config.example.yaml 的 relay_* 配置项。"
            )
        return

    task = asyncio.create_task(publish_signal(queue_item, relay_config=relay_config))
    _publish_tasks.add(task)
    task.add_done_callback(_on_publish_done)


async def publish_signal(
    queue_item: Mapping,
    *,
    relay_config: tuple[str, str, str] | None = None,
) -> None:
    """POST a wake signal. Message content and behavior never leave the backend."""
    try:
        config = relay_config or _relay_config()
    except Exception as exc:
        logger.warning("[relay_publisher] relay config unavailable: %s", exc)
        return
    if config is None:
        return

    base_url, topic, token = config
    signal = {
        "id": queue_item["id"],
        "seq": queue_item["seq"],
        "user_id": queue_item["user_id"],
        "timestamp": queue_item["timestamp"],
        "signal": "new_message",
    }
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    async with httpx.AsyncClient(
        trust_env=False,
        timeout=_REQUEST_TIMEOUT_SECONDS,
    ) as client:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = await client.post(
                    f"{base_url}/{topic}",
                    json=signal,
                    headers=headers,
                )
            except httpx.RequestError as exc:
                if attempt == _MAX_ATTEMPTS:
                    logger.warning(
                        "[relay_publisher] relay unreachable after %d attempts: %s",
                        attempt,
                        exc,
                    )
                    return
                await asyncio.sleep(2 ** (attempt - 1))
                continue
            except Exception as exc:
                logger.warning("[relay_publisher] unexpected publish failure: %s", exc)
                return

            if response.status_code in (401, 403):
                logger.warning(
                    "[relay_publisher] relay authorization failed: status=%d",
                    response.status_code,
                )
                return

            if 500 <= response.status_code < 600:
                if attempt == _MAX_ATTEMPTS:
                    logger.warning(
                        "[relay_publisher] relay failed after %d attempts: status=%d",
                        attempt,
                        response.status_code,
                    )
                    return
                await asyncio.sleep(2 ** (attempt - 1))
                continue

            if response.is_error:
                logger.warning(
                    "[relay_publisher] relay rejected signal: status=%d",
                    response.status_code,
                )
            return
