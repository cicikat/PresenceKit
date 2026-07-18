"""
杂项设置接口：工具开关、上下文轮数、破限预设、TTS 配置
"""

import base64
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from admin.auth import require_scopes
from core.config_loader import get_config

router = APIRouter()
CONFIG_FILE = Path("config.yaml")

# ─── 工具注册表（只读诊断） ─────────────────────────────────────────────────────

@router.get("/tools/registry", summary="获取已注册工具列表（来自 _TOOL_REGISTRY，非 config 列表）")
async def get_tool_registry(auth=Depends(require_scopes("admin"))):
    from core.tool_dispatcher import _TOOL_REGISTRY
    tools = [
        {"name": name, "description": (info.get("description") or info.get("desc") or "").strip()}
        for name, info in _TOOL_REGISTRY.items()
    ]
    return {"tools": tools}


# ─── 上下文轮数 ────────────────────────────────────────────────────────────────

class ContextConfigUpdate(BaseModel):
    max_turns: int


@router.get("/context-config", summary="获取上下文轮数配置")
async def get_context_config(auth=Depends(require_scopes("admin"))):
    cfg = get_config()
    # owner: memory.short_term_rounds；context.max_turns 是 deprecated alias
    max_turns = (
        cfg.get("memory", {}).get("short_term_rounds")
        or cfg.get("context", {}).get("max_turns")  # deprecated alias
        or 20
    )
    return {"max_turns": max_turns}


@router.put("/context-config", summary="修改上下文轮数并热重载")
async def update_context_config(body: ContextConfigUpdate, auth=Depends(require_scopes("admin"))):
    if not (1 <= body.max_turns <= 200):
        raise HTTPException(status_code=422, detail="max_turns 必须在 1~200 之间")

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    # 写 memory.short_term_rounds（唯一真值 owner）
    full_cfg.setdefault("memory", {})["short_term_rounds"] = body.max_turns

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")

    from core import config_loader
    config_loader.reload_config()
    return {"message": "上下文轮数已更新", "max_turns": body.max_turns}


# ─── TTS 配置 ──────────────────────────────────────────────────────────────────

class TtsConfigUpdate(BaseModel):
    enabled:         Optional[bool]  = None
    desktop_enabled: Optional[bool]  = None
    api_url:         Optional[str]   = None
    ref_audio:       Optional[str]   = None
    prompt_text:     Optional[str]   = None
    speed:           Optional[float] = None
    emotion_enabled: Optional[bool]  = None
    emotions:        Optional[dict]  = None


@router.get("/tts-config", summary="获取 TTS 配置")
async def get_tts_config(auth=Depends(require_scopes("admin"))):
    cfg = get_config().get("tts", {})
    return {
        "enabled":         cfg.get("enabled",         False),
        "desktop_enabled": cfg.get("desktop_enabled", False),
        "api_url":         cfg.get("api_url",         "http://127.0.0.1:9880"),
        "ref_audio":       cfg.get("ref_audio",       ""),
        "prompt_text":     cfg.get("prompt_text",     ""),
        "speed":           float(cfg.get("speed",     1.0)),
        "emotion_enabled": cfg.get("emotion_enabled", False),
        "emotions":        cfg.get("emotions",        {}),
    }


@router.put("/tts-config", summary="修改 TTS 配置并热重载")
async def update_tts_config(body: TtsConfigUpdate, auth=Depends(require_scopes("admin"))):
    if body.speed is not None and not (0.5 <= body.speed <= 2.0):
        raise HTTPException(status_code=422, detail="speed 必须在 0.5~2.0 之间")

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    tts_cfg = full_cfg.setdefault("tts", {})
    if body.enabled is not None:
        tts_cfg["enabled"] = body.enabled
    if body.desktop_enabled is not None:
        tts_cfg["desktop_enabled"] = body.desktop_enabled
    if body.api_url is not None:
        tts_cfg["api_url"] = body.api_url
    if body.ref_audio is not None:
        tts_cfg["ref_audio"] = body.ref_audio
    if body.prompt_text is not None:
        tts_cfg["prompt_text"] = body.prompt_text
    if body.speed is not None:
        tts_cfg["speed"] = body.speed
    if body.emotion_enabled is not None:
        tts_cfg["emotion_enabled"] = body.emotion_enabled
    if body.emotions is not None:
        tts_cfg["emotions"] = body.emotions

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")

    from core import config_loader
    config_loader.reload_config()
    return {"message": "TTS 配置已更新", "tts": tts_cfg}


class DesktopTtsUpdate(BaseModel):
    enabled: bool


class DesktopTtsSynthesize(BaseModel):
    text: str
    emotion: str = "neutral"


@router.get("/settings/tts-desktop", summary="读取桌面语音播放开关")
async def get_desktop_tts(auth=Depends(require_scopes("persona"))):
    cfg = get_config().get("tts", {})
    return {"enabled": bool(cfg.get("desktop_enabled", False))}


@router.post("/settings/tts-desktop", summary="切换桌面语音播放")
async def update_desktop_tts(body: DesktopTtsUpdate, auth=Depends(require_scopes("persona"))):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")
    full_cfg.setdefault("tts", {})["desktop_enabled"] = body.enabled
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")
    from core import config_loader
    config_loader.reload_config()
    return {"message": "桌面语音播放开关已更新", "enabled": body.enabled}


@router.post("/tts/synthesize", summary="为桌面消息按需合成语音")
async def synthesize_desktop_tts(body: DesktopTtsSynthesize, auth=Depends(require_scopes("persona"))):
    cfg = get_config().get("tts", {})
    if not cfg.get("desktop_enabled", False):
        raise HTTPException(status_code=409, detail="桌面语音播放未开启")
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="text 不能为空")
    if len(text) > 4000:
        raise HTTPException(status_code=422, detail="单条语音文本不能超过 4000 字")
    from core.output.voice_adapter import synthesize
    audio = await synthesize(text, body.emotion)
    if not audio:
        raise HTTPException(status_code=502, detail="TTS 未返回音频，请检查 API 与参考音频配置")
    return {"audio_b64": base64.b64encode(audio).decode("ascii"), "mime": "audio/wav"}


# ─── 聊天模式 ──────────────────────────────────────────────────────────────────

_VALID_MODES = {"chat", "roleplay"}


class ChatModeUpdate(BaseModel):
    mode: str


@router.get("/chat-mode", summary="获取当前聊天模式")
async def get_chat_mode(auth=Depends(require_scopes("persona"))):
    mode = get_config().get("chat", {}).get("mode", "chat")
    return {"mode": mode}


@router.put("/chat-mode", summary="切换聊天模式（chat / roleplay）")
async def update_chat_mode(body: ChatModeUpdate, auth=Depends(require_scopes("persona"))):
    if body.mode not in _VALID_MODES:
        raise HTTPException(status_code=422, detail="mode 只接受 'chat' 或 'roleplay'")

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    full_cfg.setdefault("chat", {})["mode"] = body.mode

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")

    from core import config_loader
    config_loader.reload_config()
    return {"message": f"聊天模式已切换为 {body.mode}", "mode": body.mode}


# ─── 对话风格（chat.style）────────────────────────────────────────────────────

_VALID_STYLES = {"chat", "roleplay"}


class ChatStyleUpdate(BaseModel):
    style: str


@router.get("/chat-style", summary="获取当前对话风格")
async def get_chat_style(auth=Depends(require_scopes("persona"))):
    style = get_config().get("chat", {}).get("style", "roleplay")
    return {"style": style}


@router.put("/chat-style", summary="切换对话风格（chat=沉浸式对话 / roleplay=沉浸式角色扮演）")
async def update_chat_style(body: ChatStyleUpdate, auth=Depends(require_scopes("persona"))):
    if body.style not in _VALID_STYLES:
        raise HTTPException(status_code=422, detail="style 只接受 'chat' 或 'roleplay'")

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    full_cfg.setdefault("chat", {})["style"] = body.style

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")

    from core import config_loader
    config_loader.reload_config()
    return {"message": f"对话风格已切换为 {body.style}", "style": body.style}


# ─── 分条发送开关 ──────────────────────────────────────────────────────────────

@router.get("/chat-multi-message", summary="获取分条发送开关状态")
async def get_multi_message(auth=Depends(require_scopes("persona"))):
    enabled = get_config().get("chat", {}).get("multi_message", False)
    return {"multi_message": enabled}


@router.put("/chat-multi-message", summary="切换分条发送开关")
async def update_multi_message(body: dict, auth=Depends(require_scopes("persona"))):
    enabled = bool(body.get("enabled", False))
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    full_cfg.setdefault("chat", {})["multi_message"] = enabled

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")

    from core import config_loader
    config_loader.reload_config()
    return {"message": f"分条发送已{'启用' if enabled else '禁用'}", "multi_message": enabled}


# ─── 生成后段落兜底（Brief 72） ───────────────────────────────────────────────

class OutputSegmentEnforceUpdate(BaseModel):
    enabled: bool
    min_len: Optional[int] = None


@router.get("/output-segment-enforce", summary="获取生成后段落兜底配置")
async def get_output_segment_enforce(auth=Depends(require_scopes("persona"))):
    from core.output.segment_enforcer import get_segment_enforce_settings

    enabled, min_len = get_segment_enforce_settings()
    return {"enabled": enabled, "min_len": min_len}


@router.put("/output-segment-enforce", summary="修改生成后段落兜底配置（热生效）")
async def update_output_segment_enforce(
    body: OutputSegmentEnforceUpdate,
    auth=Depends(require_scopes("persona")),
):
    if body.min_len is not None and not (1 <= body.min_len <= 5000):
        raise HTTPException(status_code=422, detail="min_len 必须在 1~5000 之间")

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    segment_cfg = full_cfg.setdefault("output", {}).setdefault("segment_enforce", {})
    segment_cfg["enabled"] = body.enabled
    if body.min_len is not None:
        segment_cfg["min_len"] = body.min_len

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")

    from core import config_loader
    config_loader.reload_config()
    from core.output.segment_enforcer import get_segment_enforce_settings

    enabled, min_len = get_segment_enforce_settings()
    return {
        "message": "生成后段落兜底配置已更新，下一轮对话起作用",
        "enabled": enabled,
        "min_len": min_len,
    }


# ─── Prompt 层级消融开关（CC 任务 23 · B6） ─────────────────────────────────────

class PromptAblationUpdate(BaseModel):
    disabled_layers: list[str]
    perception_block_disabled: bool


@router.get("/prompt-ablation", summary="获取 Prompt 层级消融开关状态")
async def get_prompt_ablation(auth=Depends(require_scopes("admin"))):
    from core.prompt_ablation import get_state, ALWAYS_ON
    from core.prompt_builder import KNOWN_LAYERS

    state = get_state()
    return {
        "known_layers": [{"layer": name, "desc": desc} for name, desc in KNOWN_LAYERS],
        "always_on": sorted(ALWAYS_ON),
        "disabled_layers": sorted(state["disabled_layers"]),
        "perception_block_disabled": state["perception_block_disabled"],
    }


@router.put("/prompt-ablation", summary="修改 Prompt 层级消融开关（进程内热生效）")
async def update_prompt_ablation(body: PromptAblationUpdate, auth=Depends(require_scopes("admin"))):
    from core.prompt_ablation import set_state, ALWAYS_ON
    from core.prompt_builder import KNOWN_LAYERS

    known_names = {name for name, _ in KNOWN_LAYERS}
    unknown = [l for l in body.disabled_layers if l not in known_names]
    if unknown:
        raise HTTPException(status_code=422, detail=f"未知层名: {unknown}")

    always_on_hit = set(body.disabled_layers) & ALWAYS_ON
    if always_on_hit:
        raise HTTPException(status_code=422, detail=f"不可消融层: {sorted(always_on_hit)}")

    try:
        state = set_state(body.disabled_layers, body.perception_block_disabled)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {
        "message": "层级消融开关已更新，下一轮对话起作用",
        "disabled_layers": sorted(state["disabled_layers"]),
        "perception_block_disabled": state["perception_block_disabled"],
    }


# ---------------------------------------------------------------------------
# /settings/mail — 配置中心「可选」层：邮件通道完整配置（用户反馈补遗）
# enabled 总开关已经过 /settings/feature-flags 暴露；这里补齐 smtp 明细字段，
# 不然用户只能手改 config.yaml。占位符/掩码处理复用 settings_llm 的既有约定。
# ---------------------------------------------------------------------------

class MailSettingsUpdate(BaseModel):
    enabled:        Optional[bool] = None
    smtp_host:      Optional[str]  = None
    smtp_port:      Optional[int]  = None
    proxy_url:      Optional[str]  = None
    smtp_user:      Optional[str]  = None
    smtp_password:  Optional[str]  = None
    from_addr:      Optional[str]  = None
    from_name:      Optional[str]  = None
    to_addr:        Optional[str]  = None
    subject_prefix: Optional[str]  = None


def _mail_view(cfg: dict) -> dict:
    from admin.routers.settings_llm import _looks_placeholder, _mask_key

    target = cfg.get("mail", {})
    password = str(target.get("smtp_password") or "")
    required_filled = not any(
        _looks_placeholder(target.get(k, ""))
        for k in ("smtp_host", "smtp_user", "smtp_password", "to_addr")
    )
    return {
        "enabled":         bool(target.get("enabled", False)),
        "smtp_host":       "" if _looks_placeholder(target.get("smtp_host", "")) else target.get("smtp_host", ""),
        "smtp_port":       int(target.get("smtp_port", 587) or 587),
        "proxy_url":       "" if _looks_placeholder(target.get("proxy_url", "")) else target.get("proxy_url", ""),
        "smtp_user":       "" if _looks_placeholder(target.get("smtp_user", "")) else target.get("smtp_user", ""),
        "smtp_password_masked": _mask_key(password) if password and not _looks_placeholder(password) else "",
        "smtp_password_set":    bool(password) and not _looks_placeholder(password),
        "from_addr":       "" if _looks_placeholder(target.get("from_addr", "")) else target.get("from_addr", ""),
        "from_name":       target.get("from_name", ""),
        "to_addr":         "" if _looks_placeholder(target.get("to_addr", "")) else target.get("to_addr", ""),
        "subject_prefix":  target.get("subject_prefix", ""),
        "configured":      required_filled,
    }


@router.get("/settings/mail", summary="读取邮件通道完整配置（配置中心可选层）")
async def get_mail_settings(auth=Depends(require_scopes("admin"))):
    return _mail_view(get_config())


@router.put("/settings/mail", summary="写入邮件通道完整配置并热重载（配置中心可选层）")
async def update_mail_settings(body: MailSettingsUpdate, auth=Depends(require_scopes("admin"))):
    if body.smtp_port is not None and not (1 <= body.smtp_port <= 65535):
        raise HTTPException(status_code=422, detail="smtp_port 必须在 1~65535 之间")

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    mail_cfg = full_cfg.setdefault("mail", {})
    updates: dict = {}
    for key, value in body.model_dump(exclude_none=True).items():
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                continue  # 留空 = 不修改已有值，与 base-model/embedding 约定一致
        updates[key] = value
    if not updates:
        raise HTTPException(status_code=422, detail="至少提供一个要修改的字段")
    mail_cfg.update(updates)

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")

    from core import config_loader
    config_loader.reload_config()
    return _mail_view(get_config())


# ---------------------------------------------------------------------------
# /settings/anniversaries — 配置中心「可选」层：自定义纪念日 CRUD（用户反馈补遗）
# 顶层 anniversaries: 列表此前只有 letter_writer/festival 消费，config.example.yaml
# 完全未文档化，面板也没有入口——只能手改 yaml。整体替换语义与 /scheduler/config 的
# signatures 字段一致。
# ---------------------------------------------------------------------------

class AnniversaryItem(BaseModel):
    key:          str
    month:        int
    day:          int
    year_start:   Optional[int] = None  # 起算年份；留空 = 每年都当作"当年"（不计年数，只年年重复 prompt_zero）
    prompt_zero:  str = ""              # years==0 时使用（year_start 留空时恒定使用这条）
    prompt_years: str = ""              # years>0 时使用，可用 {char}/{years} 占位符


class AnniversariesUpdate(BaseModel):
    anniversaries: list[AnniversaryItem]


@router.get("/settings/anniversaries", summary="读取自定义纪念日列表（配置中心可选层）")
async def get_anniversaries(auth=Depends(require_scopes("admin"))):
    return {"anniversaries": get_config().get("anniversaries", [])}


@router.put("/settings/anniversaries", summary="整体替换自定义纪念日列表并热重载（配置中心可选层）")
async def update_anniversaries(body: AnniversariesUpdate, auth=Depends(require_scopes("admin"))):
    from datetime import date as _date

    for item in body.anniversaries:
        if not item.key.strip():
            raise HTTPException(status_code=422, detail="纪念日 key 不能为空")
        try:
            _date(2000, item.month, item.day)  # 2000 闰年，允许 02-29
        except ValueError:
            raise HTTPException(status_code=422, detail=f"日期非法: {item.month:02d}-{item.day:02d}")

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    saved = []
    for item in body.anniversaries:
        entry = {"key": item.key.strip(), "month": item.month, "day": item.day}
        if item.year_start is not None:
            entry["year_start"] = item.year_start
        if item.prompt_zero.strip():
            entry["prompt_zero"] = item.prompt_zero.strip()
        if item.prompt_years.strip():
            entry["prompt_years"] = item.prompt_years.strip()
        saved.append(entry)
    full_cfg["anniversaries"] = saved

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")

    from core import config_loader
    config_loader.reload_config()
    return {"anniversaries": saved}


# ---------------------------------------------------------------------------
# /settings/diary — 配置中心「可选」层：Obsidian 日记读取路径（用户反馈补遗）
# core/tools/diary_reader.py 读 diary.obsidian_path，此前只能手改 config.yaml。
# 只暴露路径本身；diary.characters 白名单不在本页编辑，写入时原样保留。
# ---------------------------------------------------------------------------

class DiarySettingsUpdate(BaseModel):
    obsidian_path: str


@router.get("/settings/diary", summary="读取 Obsidian 日记读取路径（配置中心可选层）")
async def get_diary_settings(auth=Depends(require_scopes("admin"))):
    path = str(get_config().get("diary", {}).get("obsidian_path") or "")
    return {"obsidian_path": path, "configured": bool(path.strip())}


@router.put("/settings/diary", summary="写入 Obsidian 日记读取路径并热重载（配置中心可选层）")
async def update_diary_settings(body: DiarySettingsUpdate, auth=Depends(require_scopes("admin"))):
    path = body.obsidian_path.strip()
    if not path:
        raise HTTPException(status_code=422, detail="obsidian_path 不能为空")

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    full_cfg.setdefault("diary", {})["obsidian_path"] = path

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")

    from core import config_loader
    config_loader.reload_config()
    return {"obsidian_path": path, "configured": True}


# ---------------------------------------------------------------------------
# /settings/coplay-games — 配置中心「可选」层：coplay 后台游戏进程检测白名单
# core/coplay/watcher.py 靠 config.coplay.game_whitelist（[{name, process_name,
# save_dir?}, ...]）做非 Steam 游戏的 psutil 进程名匹配；此前只能手改 config.yaml。
# 整体替换语义与 /settings/anniversaries 一致。
# ---------------------------------------------------------------------------

class GameWhitelistEntry(BaseModel):
    name:         str
    process_name: str
    save_dir:     Optional[str] = None  # 可选：存档目录，供 coplay 存档变化检测用


class CoplayGamesUpdate(BaseModel):
    game_whitelist: list[GameWhitelistEntry]


@router.get("/settings/coplay-games", summary="读取 coplay 后台游戏进程检测白名单（配置中心可选层）")
async def get_coplay_games(auth=Depends(require_scopes("admin"))):
    return {"game_whitelist": get_config().get("coplay", {}).get("game_whitelist", []) or []}


@router.put("/settings/coplay-games", summary="整体替换 coplay 后台游戏进程检测白名单并热重载（配置中心可选层）")
async def update_coplay_games(body: CoplayGamesUpdate, auth=Depends(require_scopes("admin"))):
    saved: list[dict] = []
    seen_proc: set[str] = set()
    for item in body.game_whitelist:
        name = item.name.strip()
        proc = item.process_name.strip()
        if not name or not proc:
            raise HTTPException(status_code=422, detail="name 和 process_name 均不能为空")
        proc_key = proc.lower()
        if proc_key.endswith(".exe"):
            proc_key = proc_key[:-4]
        if proc_key in seen_proc:
            raise HTTPException(status_code=422, detail=f"process_name 重复（去掉 .exe 后）: {proc}")
        seen_proc.add(proc_key)
        entry: dict = {"name": name, "process_name": proc}
        if item.save_dir and item.save_dir.strip():
            entry["save_dir"] = item.save_dir.strip()
        saved.append(entry)

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置文件失败: {e}")

    full_cfg.setdefault("coplay", {})["game_whitelist"] = saved

    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(full_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")

    from core import config_loader
    config_loader.reload_config()
    return {"game_whitelist": saved}
