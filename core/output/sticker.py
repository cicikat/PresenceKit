"""
表情包发送模块
LLM判断情绪类别，随机抽取对应文件夹的图片发送
概率极低，角色偶尔才会发
"""

import logging
import random
from base64 import b64encode
from pathlib import Path

from core.error_handler import log_error

logger = logging.getLogger(__name__)

_EMOTION_LABELS = ["无奈", "心疼", "开心", "委屈", "害羞", "沉默"]

# 触发概率，角色不常发表情包
_TRIGGER_PROB = 0.06


def _pick_sticker(emotion: str) -> str | None:
    """从对应情绪文件夹随机抽一张图片，返回绝对路径"""
    from core.sandbox import get_paths

    folder = get_paths().stickers_dir() / emotion
    if not folder.exists():
        return None
    files = [f for f in folder.iterdir() if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif")]
    if not files:
        return None
    return str(random.choice(files).resolve())


def _build_sticker_payload(path: str, emotion: str) -> dict | None:
    """构造可跨通道转发的贴纸 payload，不向客户端暴露本机文件路径。"""
    suffix = Path(path).suffix.lower()
    media_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
    }.get(suffix)
    if media_type is None:
        return None
    try:
        encoded = b64encode(Path(path).read_bytes()).decode("ascii")
    except OSError:
        return None
    return {
        "kind": "sticker",
        "emotion": emotion,
        "data_url": f"data:{media_type};base64,{encoded}",
    }


async def maybe_send_sticker(reply: str, target_id: str, is_group: bool = False, emotion: str = ""):
    """
    根据情绪小概率发一张表情包。
    在post_process里调用，失败静默。
    """
    try:
        # neutral或无情绪直接跳过
        if not emotion or emotion == "neutral":
            return
        if random.random() > _TRIGGER_PROB:
            return
        # 把detect_emotion的标签映射到表情包文件夹
        _EMOTION_MAP = {
            "happy": "开心",
            "sad": "委屈",
            "gentle": "心疼",
            "surprised": "害羞",
            "angry": "无奈",
        }
        folder_emotion = _EMOTION_MAP.get(emotion, "沉默")

        path = _pick_sticker(folder_emotion)
        if not path:
            return

        from core.qq_adapter import send_image
        await send_image(target_id, path, is_group)

        payload = _build_sticker_payload(path, folder_emotion)
        if payload is not None:
            # QQ 保持原有 OneBot 图片发送；桌宠和手机经既有通道注册表收到同一份
            # 自包含 payload，客户端可在独立工单中按 sticker 字段渲染。
            from channels import registry
            await registry.broadcast(
                "", target_id, sticker=payload, exclude_channels={"qq"},
            )
        logger.info("[sticker] 发送表情包: %s", folder_emotion)

    except Exception as e:
        log_error("sticker.maybe_send_sticker", e)
