"""
角色卡加载模块
解析 SillyTavern 和 Presence 格式的角色卡 JSON 文件
支持角色一致性检测
"""

import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from core.config_loader import get_config
from core.error_handler import log_error

logger = logging.getLogger(__name__)

CHARACTERS_DIR = Path("characters")

# 一致性检测计数器：{character_name: 轮次计数}
_consistency_counter: dict[str, int] = {}

# 边沿检测：load() 会被路由解析（model_registry._char_model_routing）等热路径
# 高频调用（每次 LLM 调用都可能带 char_id），并非只在真正切换角色时才走到这里。
# 只在"本次加载的角色名与上次不同"（真正发生切换/首次加载）时打 INFO，
# 同名重复解析降 DEBUG（Brief 54-C：记边沿，不记电平）。
_last_loaded_char_name: str | None = None
_last_loaded_signature: tuple[str, int, int] | None = None
_character_cache: dict[str, tuple[tuple[str, int, int], "Character"]] = {}


def _log_load_success(name: str, signature: tuple[str, int, int], *, note: str = "") -> None:
    global _last_loaded_char_name, _last_loaded_signature
    suffix = f"（{note}）" if note else ""
    if _last_loaded_char_name != name or _last_loaded_signature != signature:
        logger.info(f"[character_loader] 角色 '{name}' 加载成功{suffix}")
        _last_loaded_char_name = name
        _last_loaded_signature = signature
    else:
        logger.debug(f"[character_loader] 角色 '{name}' 加载成功{suffix}（重复解析，非切换）")


@dataclass
class Character:
    """角色卡数据类字段"""
    name: str = "AI"
    description: str = ""        # 外貌/背景描述
    personality: str = ""        # 性格描述
    scenario: str = ""           # 当前情境/场景
    mes_example: str = ""        # 对话示例（few-shot）
    first_mes: str = ""          # 首次发言
    system_prompt: str = ""      # 全局 system 提示
    world_book: list[dict] = field(default_factory=list)  # 世界书条目
    # 角色私人日期（S6 多角色化：从全局 config 迁入角色卡）。
    # None = 该卡未迁移此字段 → festival 回落读全局 config（向后兼容）；
    # [] / {} = 已迁移、该角色无此类日期（不回落，避免认领别的角色的纪念日）。
    anniversaries: list[dict] | None = None   # 角色专属纪念日
    birthday: dict | None = None              # 角色生日 {month, day, prompt}
    # 性别标识，用于推导人称代词（"male" → 他，"female" → 她，"neutral" → ta）。
    # 默认 "neutral" 保持向后兼容，不影响 name/personality 已迁移的卡。
    gender: str = "neutral"
    # SillyTavern 导入字段（酒馆卡适配，阶段二）。老卡缺省即空，不影响现有流程。
    post_history_instructions: str = ""   # 酒馆 Post-History Instructions
    post_history_extra: str = ""          # 常驻 after 型世界书条目（折叠存储）
    alternate_greetings: list[str] = field(default_factory=list)  # 备用开场白
    # per-char 兼容钩子（Brief 29 · "本我"模式）。缺失/字段类型不对 = 全默认 = 现有角色零行为变化。
    # disabled_layers: list[str]   — 与全局消融开关取并集（core/prompt_ablation.py）
    # model_routing:   str | None  — 路由 profile 名，存在于 routing_profiles 才生效（core/model_registry.py）
    # tool_categories: list[str] | None — tool loop 暴露面覆盖（core/pipeline.py run_agentic_loop）
    # proactive:       "full"（默认）| "off" — 主动发言总闸（core/scheduler/gating.py）
    # tool_loop:       "on" | "off" | 缺失 — 覆盖/回落全局 tool_loop.enabled（core/tool_dispatcher.py）
    presence_ext: dict = field(default_factory=dict)


def load(filename_or_id: str) -> Character:
    """
    加载角色卡。参数可以是 id ("yexuan")、legacy filename ("yexuan.json")
    或 legacy 中文 label ("叶瑄")，均通过 asset registry 规范化。

    出错时抛出异常，不静默兜底：
    - ValueError:          id 在 registry 中不存在（unknown character id）
    - FileNotFoundError:   文件记录在 registry 但磁盘上不存在
    - json.JSONDecodeError: JSON 损坏
    - RuntimeError:        其他意外错误
    """
    from core.asset_registry import get_registry

    reg = get_registry()

    # Step 1: 规范化 legacy 形式 → 标准 id
    asset_id = reg.normalize_legacy(filename_or_id, "character")

    # Step 2: registry resolve — 失败直接 raise ValueError
    entry = reg.resolve(asset_id, "character")

    path = entry.path()
    suffix = path.suffix.lower()

    # Step 3: 文件必须存在
    if not path.exists():
        raise FileNotFoundError(
            f"[character_loader] 角色文件不存在: {path} "
            f"(id={asset_id!r}, 已在 registry 中注册但磁盘文件缺失)"
        )

    stat = path.stat()
    cache_key = str(path.resolve())
    signature = (cache_key, stat.st_mtime_ns, stat.st_size)
    cached = _character_cache.get(cache_key)
    if cached is not None and cached[0] == signature:
        char = deepcopy(cached[1])
        _log_load_success(char.name, signature, note="缓存命中")
        return char

    # Step 4: 解析
    if suffix in (".txt", ".md"):
        text = path.read_text(encoding="utf-8")
        name = path.stem
        char = Character(name=name, description=text)
        _character_cache[cache_key] = (signature, char)
        _log_load_success(name, signature, note="纯文本格式")
        return deepcopy(char)

    # JSON 格式 — json.JSONDecodeError 直接向上抛
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    char = Character(
        name=data.get("name", path.stem),
        description=data.get("description", ""),
        personality=data.get("personality", ""),
        scenario=data.get("scenario", ""),
        mes_example=data.get("mes_example", ""),
        first_mes=data.get("first_mes", ""),
        system_prompt=data.get("system_prompt", ""),
        world_book=data.get("world_book", []),
        # .get(key) without default → None when the card hasn't been migrated,
        # which festival uses to decide between card and legacy-config fallback.
        anniversaries=data.get("anniversaries"),
        birthday=data.get("birthday"),
        gender=data.get("gender", "neutral"),
        post_history_instructions=data.get("post_history_instructions") or "",
        post_history_extra=data.get("post_history_extra") or "",
        alternate_greetings=data.get("alternate_greetings") or [],
        presence_ext=data.get("presence_ext") if isinstance(data.get("presence_ext"), dict) else {},
    )
    for field_name in ("system_prompt", "description", "personality", "scenario",
                       "post_history_instructions", "post_history_extra"):
        val = getattr(char, field_name)
        if isinstance(val, list):
            setattr(char, field_name, "".join(val))

    _character_cache[cache_key] = (signature, char)
    _log_load_success(char.name, signature)
    return deepcopy(char)


def is_proactive_disabled(char_id: str | None = None) -> bool:
    """presence_ext.proactive == "off" 判定（Brief 29 · 3.3 scheduler 发言闸门）。

    char_id 显式传入时按 id 加载；未传时读当前活跃角色（pipeline_registry）。
    全 fail-soft：加载失败/未注册/字段缺失 → False，绝不阻断发言（安全默认）。
    """
    try:
        if char_id is not None:
            char = load(char_id)
        else:
            from core import pipeline_registry
            pl = pipeline_registry.get()
            char = pl.character if pl is not None else None
        if char is None:
            return False
        return (char.presence_ext or {}).get("proactive") == "off"
    except Exception:
        return False


async def consistency_check(character: Character, last_reply: str) -> dict:
    """
    检查最近一条回复是否符合角色人设

    每 consistency_check_every_n 轮调用一次
    返回：{"ok": bool, "issue": str}
    ok=False 时，issue 包含纠偏提示，将追加到下一轮的 Author's Note
    """
    cfg = get_config()
    check_every = cfg.get("character", {}).get("consistency_check_every_n", 15)
    char_name = character.name

    # 计数
    _consistency_counter[char_name] = _consistency_counter.get(char_name, 0) + 1
    if _consistency_counter[char_name] < check_every:
        return {"ok": True, "issue": ""}

    # 到达检测轮次，重置计数器
    _consistency_counter[char_name] = 0

    # 构建检测 prompt
    prompt_messages = [
        {
            "role": "system",
            "content": (
                "你是一个角色扮演一致性检查员。\n"
                "判断以下角色的最新回复是否符合其人设描述。\n"
                "只返回 JSON，格式：{\"ok\": true/false, \"issue\": \"如果不符合，用一句话描述问题和纠正方向\"}\n"
                "如果符合，issue 填空字符串。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"角色名：{character.name}\n"
                f"性格描述：{character.personality}\n\n"
                f"最新回复：{last_reply}"
            ),
        },
    ]

    try:
        from core import llm_client
        import json as _json

        raw = await llm_client.chat(prompt_messages)
        raw = raw.strip().strip("```json").strip("```").strip()
        result = _json.loads(raw)
        if not result.get("ok"):
            logger.info(f"[consistency_check] 角色 {char_name} 发现人设偏离: {result.get('issue')}")
        return result
    except Exception as e:
        log_error("character_loader.consistency_check", e)
        return {"ok": True, "issue": ""}


def should_check_consistency(character: Character) -> bool:
    """
    非阻塞地判断是否达到检测轮次
    与 consistency_check 分离，方便在 main.py 中决定是否异步触发
    """
    cfg = get_config()
    check_every = cfg.get("character", {}).get("consistency_check_every_n", 15)
    char_name = character.name
    current = _consistency_counter.get(char_name, 0)
    return (current + 1) >= check_every


class CharacterLoader:
    """角色卡加载类，封装模块级函数，供外部按类方式导入使用"""

    def load(self, filename: str) -> Character:
        return load(filename)

    async def consistency_check(self, character: Character, last_reply: str) -> dict:
        return await consistency_check(character, last_reply)

    def should_check_consistency(self, character: Character) -> bool:
        return should_check_consistency(character)
