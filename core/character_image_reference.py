"""Owner-approved portrait used only when image generation opts in."""
from io import BytesIO
from threading import Lock

from PIL import Image, ImageOps, UnidentifiedImageError

from core.safe_write import safe_write_bytes
from core.sandbox import get_paths

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_WRITE_LOCK = Lock()


class ReferenceError(ValueError):
    """Safe, actionable reference error for the tool caller."""


def _path(user_id: str, char_id: str):
    return get_paths().character_image_reference(user_id, char_id=char_id)


def read(user_id: str, char_id: str) -> bytes | None:
    try:
        return _path(user_id, char_id).read_bytes()
    except FileNotFoundError:
        return None


def exists(user_id: str, char_id: str) -> bool:
    return _path(user_id, char_id).is_file()


def remove(user_id: str, char_id: str) -> None:
    with _WRITE_LOCK:
        _path(user_id, char_id).unlink(missing_ok=True)


def save(user_id: str, char_id: str, raw: bytes) -> None:
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ReferenceError("参考图须小于或等于 10 MB。")
    try:
        with Image.open(BytesIO(raw), formats=["PNG", "JPEG", "WEBP"]) as image:
            if image.width * image.height > 32_000_000:
                raise ReferenceError("参考图不能超过 3200 万像素。")
            portrait = ImageOps.exif_transpose(image).convert("RGB")
            portrait.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            output = BytesIO()
            portrait.save(output, "JPEG", quality=94)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ReferenceError("无法读取参考图，请使用有效的 PNG、JPEG 或 WebP。") from exc
    with _WRITE_LOCK:
        if not safe_write_bytes(_path(user_id, char_id), output.getvalue()):
            raise RuntimeError("参考图写入失败。")
