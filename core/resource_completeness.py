"""
core/resource_completeness.py — 资源完整性/功能状态检查（2026-07-25，茶茶反馈）

给新用户/自查用的"体检报告"：哪个功能的开关没打开、哪个功能开了但没配素材、
哪个功能干脆还没写。不做修复，只读快照，全 fail-soft（单项检查异常不得拖垮整体）。

供 GET /observability/resource-completeness 使用，见 admin/routers/observability.py。
与 core/api_contract_check.py（item 10 · API 契约测试）是两块独立面板，都挂在
观测(observability) 分区下，互不依赖。
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# status 枚举：
#   ok             — 开关已开，素材/配置齐全
#   off            — 功能存在，开关关着（用户主动选择，非缺陷）
#   missing_asset  — 开关已开，但缺少必需素材/配置（真正的"漏"）
#   missing_dep    — 完整运行依赖未安装（Pillow / python-docx 等）
#   unknown        — 检查过程本身异常，无法判断（fail-soft 兜底，不冒充 ok）


@dataclass
class CheckResult:
    id: str
    label: str
    status: str
    detail: str = ""
    category: str = "switch"  # switch | asset | binding


@dataclass
class KnownGap:
    id: str
    label: str
    detail: str
    source: str = ""


def _safe_check(check_id: str, label: str, fn) -> CheckResult:
    try:
        return fn()
    except Exception as e:
        logger.warning("[resource_completeness] 检查 %s 失败: %s", check_id, e)
        return CheckResult(id=check_id, label=label, status="unknown", detail=f"检查异常: {e}")


def _check_runtime_deps() -> CheckResult:
    from core.runtime_deps import missing_runtime_imports

    missing = missing_runtime_imports()
    if missing:
        detail = "、".join(f"{item['name']}（{item['label']}）" for item in missing)
        return CheckResult(
            id="runtime_deps",
            label="完整运行依赖",
            status="missing_dep",
            detail=f"当前解释器缺少：{detail}。安装/更新应同步 requirements.lock。",
            category="asset",
        )
    return CheckResult(
        id="runtime_deps",
        label="完整运行依赖",
        status="ok",
        detail="Pillow / Word / PDF / 搜索 / 邮件 / MCP 等完整运行包可导入",
        category="asset",
    )


def _check_sticker() -> CheckResult:
    from core.config_loader import get_config
    cfg = get_config().get("sticker", {})
    if not cfg.get("enabled", False):
        return CheckResult(id="sticker", label="表情包", status="off", detail="sticker.enabled=false")
    from core.sandbox import get_paths
    folder = get_paths().stickers_dir()
    has_any = folder.exists() and any(
        f.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif")
        for sub in (folder.iterdir() if folder.exists() else [])
        if sub.is_dir()
        for f in sub.iterdir()
    )
    if not has_any:
        return CheckResult(
            id="sticker", label="表情包", status="missing_asset",
            detail=f"开关已开，但 {folder} 下未找到任何情绪子目录图片",
            category="asset",
        )
    return CheckResult(id="sticker", label="表情包", status="ok", detail=f"素材目录：{folder}")


def _tts_provider_status() -> tuple[bool, dict[str, Any] | None, str]:
    """Read provider readiness once per check without exposing provider secrets."""
    from core.output.voice_adapter import get_provider_status

    try:
        status = get_provider_status()
    except Exception as e:
        return False, None, f"provider status read failed: {e}"
    if not status.get("ready", False):
        return False, status, "provider is not ready"
    return True, status, ""


def _check_tts() -> CheckResult:
    from core.config_loader import get_config
    cfg = get_config().get("tts", {})
    if not cfg.get("enabled", False):
        return CheckResult(
            id="tts",
            label="TTS 合成服务",
            status="off",
            detail="tts.enabled=false；/tts/synthesize 不会合成音频",
        )
    ready, status, error = _tts_provider_status()
    if status is None:
        return CheckResult(id="tts", label="TTS 合成服务", status="unknown", detail=error)
    if not ready:
        return CheckResult(
            id="tts", label="TTS 合成服务", status="missing_asset",
            detail=f"tts.enabled=true，但当前 provider（{status.get('provider', '?')}）未就绪",
            category="asset",
        )
    return CheckResult(
        id="tts",
        label="TTS 合成服务",
        status="ok",
        detail=f"provider={status.get('provider')}；/tts/synthesize 可按需合成",
    )


def _check_desktop_voice_bar() -> CheckResult:
    """Report the desktop manual voice-bar contract separately from TTS itself."""
    from core.config_loader import get_config

    cfg = get_config().get("tts", {})
    if not cfg.get("desktop_enabled", False):
        return CheckResult(
            id="desktop_voice_bar",
            label="桌宠手动语音条",
            status="off",
            detail="tts.desktop_enabled=false；桌面端设置页不会显示可用语音条",
        )
    if not cfg.get("enabled", False):
        return CheckResult(
            id="desktop_voice_bar",
            label="桌宠手动语音条",
            status="missing_asset",
            detail="语音条已开启，但 tts.enabled=false；点击后无法合成音频",
            category="asset",
        )
    ready, status, error = _tts_provider_status()
    if status is None:
        return CheckResult(id="desktop_voice_bar", label="桌宠手动语音条", status="unknown", detail=error)
    if not ready:
        return CheckResult(
            id="desktop_voice_bar",
            label="桌宠手动语音条",
            status="missing_asset",
            detail=f"语音条已开启，但 provider（{status.get('provider', '?')}）未就绪",
            category="asset",
        )
    return CheckResult(
        id="desktop_voice_bar",
        label="桌宠手动语音条",
        status="ok",
        detail="桌面端可显示语音条，点击后按需调用 /tts/synthesize；自动播放由场景开关控制",
    )


def _check_vision() -> CheckResult:
    from core.config_loader import get_config
    cfg = get_config().get("vision", {})
    if not cfg.get("enabled", False):
        return CheckResult(id="vision", label="图像识别（通用视觉）", status="off", detail="vision.enabled=false")
    if not cfg.get("api_key"):
        return CheckResult(id="vision", label="图像识别（通用视觉）", status="missing_asset",
                            detail="开关已开，但 vision.api_key 未配置", category="asset")
    return CheckResult(id="vision", label="图像识别（通用视觉）", status="ok")


def _check_use_computer_vision() -> CheckResult:
    from core.config_loader import get_config
    from core.perception.vlm_client import get_use_computer_vision_config
    cfg = get_config()
    if not cfg.get("use_computer_vision") and not cfg.get("vision", {}).get("enabled"):
        return CheckResult(id="use_computer_vision", label="用电脑（桌面自动化视觉）", status="off",
                            detail="未配置 use_computer_vision 且通用 vision 亦未开，回落无源")
    resolved = get_use_computer_vision_config()
    if not resolved.get("api_key"):
        return CheckResult(id="use_computer_vision", label="用电脑（桌面自动化视觉）", status="missing_asset",
                            detail="回落解析后仍缺 api_key", category="asset")
    return CheckResult(id="use_computer_vision", label="用电脑（桌面自动化视觉）", status="ok")


def _check_tool_loop() -> CheckResult:
    from core.config_loader import get_config
    cfg = get_config().get("tool_loop", {})
    if not cfg.get("enabled", False):
        return CheckResult(id="tool_loop", label="工具多步执行(Path C)", status="off", detail="tool_loop.enabled=false")
    return CheckResult(id="tool_loop", label="工具多步执行(Path C)", status="ok")


def _check_screen_peek() -> CheckResult:
    from core.config_loader import get_config
    cfg = get_config().get("screen_peek", {})
    return CheckResult(id="screen_peek", label="屏幕内容自主查看", status="ok" if cfg.get("enabled") else "off",
                        detail="screen_peek.enabled")


def _check_visual_perception() -> CheckResult:
    from core.config_loader import get_config
    cfg = get_config().get("visual_perception", {})
    if not cfg.get("enabled", False):
        return CheckResult(id="visual_perception", label="视觉观测(shadow trace)", status="off",
                            detail="visual_perception.enabled=false")
    if not cfg.get("api_key") or not cfg.get("base_url"):
        return CheckResult(id="visual_perception", label="视觉观测(shadow trace)", status="missing_asset",
                            detail="开关已开，但 api_key/base_url 未齐全", category="asset")
    return CheckResult(id="visual_perception", label="视觉观测(shadow trace)", status="ok")


def _check_hardware() -> CheckResult:
    from core.config_loader import get_config
    cfg = get_config().get("hardware", {})
    return CheckResult(id="hardware", label="硬件联动(玩具)", status="ok" if cfg.get("enabled") else "off",
                        detail="hardware.enabled")


def _check_qq() -> CheckResult:
    from core.config_loader import get_config
    cfg = get_config().get("qq", {})
    return CheckResult(id="qq", label="QQ 接入", status="ok" if cfg.get("enabled") else "off", detail="qq.enabled")


def _check_active_character_assets() -> CheckResult:
    """当前活跃角色的资产绑定完整度（TTS预设/表情包/Live2D/3D），非必填但缺失时提示。"""
    from core import pipeline_registry
    pipeline = pipeline_registry.get()
    char = getattr(pipeline, "character", None) if pipeline is not None else None
    if char is None:
        return CheckResult(id="char_asset_bindings", label="当前角色资产绑定", status="unknown",
                            detail="pipeline 尚未注册，无法读取活跃角色", category="binding")
    ext = char.presence_ext or {}
    bound = [k for k in ("tts_preset", "sticker_pack", "live2d_model", "model_3d") if ext.get(k)]
    missing = [k for k in ("tts_preset", "sticker_pack", "live2d_model", "model_3d") if not ext.get(k)]
    if not bound:
        return CheckResult(
            id="char_asset_bindings", label="当前角色资产绑定", status="off",
            detail=f"角色 {char.name} 未声明任何专属资产绑定，全部回落通用池/默认值（不算缺陷，仅供参考）",
            category="binding",
        )
    return CheckResult(
        id="char_asset_bindings", label="当前角色资产绑定",
        status="ok" if not missing else "missing_asset",
        detail=f"已绑定: {bound}；未绑定(回落通用): {missing}",
        category="binding",
    )


_CHECKS: list[tuple[str, str, Any]] = [
    ("runtime_deps", "完整运行依赖", _check_runtime_deps),
    ("sticker", "表情包", _check_sticker),
    ("tts", "TTS 合成服务", _check_tts),
    ("desktop_voice_bar", "桌宠手动语音条", _check_desktop_voice_bar),
    ("vision", "图像识别（通用视觉）", _check_vision),
    ("use_computer_vision", "用电脑（桌面自动化视觉）", _check_use_computer_vision),
    ("tool_loop", "工具多步执行(Path C)", _check_tool_loop),
    ("screen_peek", "屏幕内容自主查看", _check_screen_peek),
    ("visual_perception", "视觉观测(shadow trace)", _check_visual_perception),
    ("hardware", "硬件联动(玩具)", _check_hardware),
    ("qq", "QQ 接入", _check_qq),
    ("char_asset_bindings", "当前角色资产绑定", _check_active_character_assets),
]

# 手工维护：本次会话（2026-07-25）实地确认过、代码里确实还没实现的功能缺口。
# 与上面的自动检查不同——这些不是"开关没开"，是"压根没这条路"，程序本身查不出来，
# 只能靠人工记录，随实现推进及时从这里摘除（否则会一直误报"还没做"）。
_KNOWN_GAPS: list[KnownGap] = [
    KnownGap(
        id="asset_binding_frontend_consumption",
        label="角色资产绑定(Live2D/3D)的前端消费",
        detail="后端 /character/{id}/asset-bindings 已提供 live2d_model/model_3d 透传字段，"
               "前端尚未接入据此切换模型文件的实际逻辑（后端只负责存/传，不解析）。",
        source="docs/tools.md §角色资产路由",
    ),
]


def run_all_checks() -> dict:
    results = [_safe_check(cid, label, fn) for cid, label, fn in _CHECKS]
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    return {
        "checks": [asdict(r) for r in results],
        "summary": by_status,
        "known_gaps": [asdict(g) for g in _KNOWN_GAPS],
    }
