"""Full-runtime optional packages declared by requirements-full.txt.

These are try-imported at call sites and must not crash process startup when
missing, but image/Word/search/mail/MCP features are unusable without them.
Install and upgrade flows sync requirements.lock (compiled from
requirements-full.txt); this module is the shared inventory for import checks
and admin observability.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

# name: PyPI / lock distribution name. import_name: the module to import.
RUNTIME_OPTIONAL_DEPS: tuple[dict[str, str], ...] = (
    {"name": "pillow", "import_name": "PIL", "label": "图片处理（聊天图 / 生活记录）"},
    {"name": "pillow-heif", "import_name": "pillow_heif", "label": "HEIC/HEIF 图片"},
    {"name": "python-docx", "import_name": "docx", "label": "Word 文档解析"},
    {"name": "pypdf", "import_name": "pypdf", "label": "PDF 解析"},
    {"name": "ddgs", "import_name": "ddgs", "label": "网页搜索"},
    {"name": "aiosmtplib", "import_name": "aiosmtplib", "label": "邮件发送"},
    {"name": "mcp", "import_name": "mcp", "label": "MCP 外部工具"},
    {"name": "gradio-client", "import_name": "gradio_client", "label": "部分语音客户端"},
    {"name": "mss", "import_name": "mss", "label": "屏幕截图"},
    {"name": "lunar-python", "import_name": "lunar_python", "label": "农历"},
    {"name": "psutil", "import_name": "psutil", "label": "进程与资源信息"},
    {"name": "python-chess", "import_name": "chess", "label": "棋类陪玩"},
    {"name": "rapidocr-onnxruntime", "import_name": "rapidocr_onnxruntime", "label": "本地 OCR"},
    {"name": "python-socks", "import_name": "python_socks", "label": "代理 SOCKS 支持"},
    {"name": "numpy", "import_name": "numpy", "label": "音频分析（语音/音乐特征）"},
)

_REQ_LINE = re.compile(r"^([A-Za-z0-9_.-]+)")


def missing_runtime_imports() -> list[dict[str, str]]:
    missing: list[dict[str, str]] = []
    for item in RUNTIME_OPTIONAL_DEPS:
        try:
            importlib.import_module(item["import_name"])
        except Exception:
            missing.append(item)
    return missing


def _normalize_dist_name(name: str) -> str:
    return name.replace("_", "-").lower()


def declared_full_requirement_names(full_requirements: Path) -> list[str]:
    names: list[str] = []
    for raw in full_requirements.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-r "):
            continue
        match = _REQ_LINE.match(line)
        if match:
            names.append(_normalize_dist_name(match.group(1)))
    return names


def lock_distribution_names(lock_path: Path) -> set[str]:
    names: set[str] = set()
    for raw in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        if "==" in line:
            names.add(_normalize_dist_name(line.split("==", 1)[0].strip()))
    return names
