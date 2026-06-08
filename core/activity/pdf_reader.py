"""
PDF 文本提取工具（P0 — 仅支持文本型 PDF，不支持 OCR）。

这是一个 Tool 能力层，负责从 PDF 字节流中提取逐页文本。
它不关心阅读 session，不写任何持久化状态。
"""
import io
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024   # 50 MB
MAX_PAGE_TEXT_CHARS = 8000                # 单页超长时截断

# 模块级导入以便测试可 patch；不安装时设为 None，运行时明确报错
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None  # type: ignore[assignment,misc]


class PDFReadError(Exception):
    """PDF 解析错误基类。"""


class PDFOCRRequired(PDFReadError):
    """PDF 无可提取文本（扫描版），P0 不支持 OCR。"""


class PDFFileTooLarge(PDFReadError):
    """文件超过大小限制。"""


@dataclass(frozen=True)
class PDFInfo:
    total_pages: int
    filename: str


def extract_pages(file_bytes: bytes, filename: str) -> tuple[PDFInfo, list[str]]:
    """
    解析 PDF bytes，返回 (PDFInfo, pages)。
    pages[i] 是第 i+1 页的文本（外部接口 1-indexed，内部 0-indexed list）。

    Raises:
        PDFFileTooLarge: 超过 MAX_FILE_SIZE_BYTES
        PDFOCRRequired:  全书无可提取文本（扫描版）
        PDFReadError:    其他解析失败
    """
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise PDFFileTooLarge(
            f"文件 {filename!r} 大小 {len(file_bytes) // 1024} KB 超过限制 "
            f"{MAX_FILE_SIZE_BYTES // 1024 // 1024} MB"
        )

    if PdfReader is None:
        raise PDFReadError("pypdf 未安装，请运行 `pip install pypdf`")

    try:
        reader = PdfReader(io.BytesIO(file_bytes))
    except Exception as e:
        raise PDFReadError(f"PDF 解析失败: {e}") from e

    total_pages = len(reader.pages)
    if total_pages == 0:
        raise PDFReadError("PDF 没有页面")

    pages: list[str] = []
    total_chars = 0
    for page in reader.pages:
        try:
            raw = page.extract_text() or ""
        except Exception:
            raw = ""
        text = raw.strip()
        if len(text) > MAX_PAGE_TEXT_CHARS:
            text = text[:MAX_PAGE_TEXT_CHARS]
        pages.append(text)
        total_chars += len(text)

    if total_chars == 0:
        raise PDFOCRRequired(
            "该 PDF 可能是扫描版，P0 暂不支持 OCR。"
        )

    logger.info(
        "[pdf_reader] 解析完成: %r  %d 页  总字符=%d", filename, total_pages, total_chars
    )
    return PDFInfo(total_pages=total_pages, filename=filename), pages
