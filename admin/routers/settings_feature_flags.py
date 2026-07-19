"""Allowlisted runtime feature flags for the admin panel.

Only boolean switches with an implemented config consumer are exposed. Secrets and
deployment paths deliberately stay out of this generic endpoint.
"""
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from admin.auth import require_scopes
from core.config_loader import get_config

router = APIRouter()
CONFIG_FILE = Path("config.yaml")

FLAGS = {
    "qq":   ("qq",   "enabled", "QQ 通道"),
    "mail": ("mail", "enabled", "邮件通道"),
    "visual_perception": ("visual_perception", "enabled", "视觉感知"),
    "spend": ("spend", "enabled", "支出意向"),
    "practice": ("practice", "enabled", "自主练习"),
    "action_trace": ("action_trace", "enabled", "行为痕迹"),
    "intent_reflex": ("intent_reflex", "enabled", "意图反射（降级路径）"),
    "mcp_servers": ("mcp_servers", "enabled", "MCP 外部工具"),
    "fs_access": ("fs_access", "enabled", "文件只读访问"),
    "anti_collapse": ("anti_collapse", "enabled", "输出防坍缩"),
    "coplay": ("coplay", "enabled", "陪玩部署"),
    "toy_autogrow": ("toy_autogrow", "enabled", "玩具自主生长"),
    "web_autosearch": ("web_autosearch", "enabled", "自主联网搜索"),
    "performance_mapping": ("performance_mapping", "enabled", "表演标注映射"),
    "private_exchange": ("private_exchange", "enabled", "角色私下往来"),
}


class FeatureFlagsUpdate(BaseModel):
    flags: dict[str, bool]


@router.get("/settings/feature-flags", summary="读取功能开关白名单")
async def get_feature_flags(auth=Depends(require_scopes("admin"))):
    cfg = get_config()
    return {"flags": {name: {"enabled": bool(cfg.get(section, {}).get(key, False)), "label": label}
                      for name, (section, key, label) in FLAGS.items()}}


@router.put("/settings/feature-flags", summary="批量更新功能开关并热重载")
async def update_feature_flags(body: FeatureFlagsUpdate, auth=Depends(require_scopes("admin"))):
    unknown = sorted(set(body.flags) - set(FLAGS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"未知功能开关: {unknown}")
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
        for name, enabled in body.flags.items():
            section, key, _ = FLAGS[name]
            full_cfg.setdefault(section, {})[key] = enabled
        with CONFIG_FILE.open("w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"保存功能开关失败: {exc}") from exc
    from core import config_loader
    config_loader.reload_config()
    return await get_feature_flags(auth)
