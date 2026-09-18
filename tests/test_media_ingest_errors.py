"""Typed media ingest failures must not collapse into empty success."""
import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from core.media_processor import (
    MediaIngestError,
    ingest_file_bytes,
    ingest_image_bytes,
    parse_file_bytes,
)


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _png_bytes() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(out, format="PNG")
    return out.getvalue()


def test_word_missing_docx_raises_dependency_unavailable(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "docx" or name.startswith("docx."):
            raise ImportError("No module named 'docx'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    with pytest.raises(MediaIngestError) as exc:
        parse_file_bytes(b"not-a-docx", "note.docx")
    assert exc.value.code == "dependency_unavailable"
    assert "python-docx" in exc.value.message


def test_word_parse_failure_is_typed_not_empty_success():
    with pytest.raises(MediaIngestError) as exc:
        parse_file_bytes(b"not-a-real-docx", "broken.docx")
    assert exc.value.code == "file_parse_failed"


@pytest.mark.asyncio
async def test_ingest_file_bytes_does_not_return_empty_success_on_parse_fail(sandbox):
    with pytest.raises(MediaIngestError) as exc:
        await ingest_file_bytes(b"not-a-real-docx", "broken.docx")
    assert exc.value.code == "file_parse_failed"


@pytest.mark.asyncio
async def test_ingest_image_bytes_unsupported_suffix_is_typed():
    with pytest.raises(MediaIngestError) as exc:
        await ingest_image_bytes([(b"data", "note.tiff")])
    assert exc.value.code == "unsupported_image"


@pytest.mark.asyncio
async def test_ingest_image_bytes_vision_empty_is_typed(monkeypatch):
    from core import config_loader, llm_client, media_processor

    monkeypatch.setattr(config_loader, "get_config", lambda: {"image_recognition": {"mode": "vision"}})
    monkeypatch.setattr(media_processor, "_build_vision_prompt", lambda: "describe")
    monkeypatch.setattr(llm_client, "chat", AsyncMock(return_value=None))
    monkeypatch.setattr("core.image_presets.resolve_purpose", MagicMock(side_effect=KeyError("chat_upload")))
    with pytest.raises(MediaIngestError) as exc:
        await ingest_image_bytes([(_png_bytes(), "photo.png")])
    assert exc.value.code == "vision_failed"
