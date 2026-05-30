"""
角色对用户的认知文件
─────────────────────────────────────────────────────
角色对每个用户维护一个"认知 Markdown 文件"，
记录他觉得重要的事情、用户的特点、两人的重要时刻。

存储位置：
  data/character_growth/角色_{user_id}.md

更新机制：
  每 20 轮对话触发一次，把最近3天的日志喂给 LLM，
  让 LLM 以角色的视角更新这个文件（全量覆写，300字以内）。

轮数计数器保存在内存里（重启清零，无所谓，只会少触发一次）。
"""

import logging
import re
from pathlib import Path

from core.error_handler import log_error
from core.sandbox import get_paths
from core.safe_write import safe_write_text
from core.llm_output_validator import record_failure, is_paused, reset

logger = logging.getLogger(__name__)


def _growth_root() -> Path:
    return get_paths().character_growth()


def _growth_file(character_name: str, user_id: str) -> Path:
    """返回认知文件路径，文件名格式：角色_{user_id}.md"""
    safe_char = "".join(c for c in character_name if c.isalnum() or c in "-_")
    safe_user = "".join(c for c in user_id if c.isalnum() or c in "-_")
    return _growth_root() / f"{safe_char}_{safe_user}.md"


def load(character_name: str, user_id: str) -> str:
    """
    读取角色对该用户的认知文件内容。
    文件不存在时返回空字符串，不报错。

    参数：
        character_name - 角色名（如"叶瑄"）
        user_id        - 用户 QQ 号

    返回：
        认知文件的文本内容，空则返回 ""
    """
    path = _growth_file(character_name, user_id)
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        log_error("character_growth.load", e)
    return ""


async def update(
    character_name: str,
    user_id: str,
    event_log_content: str,
    llm_client,
):
    """
    让 LLM 以角色的视角，根据最近对话日志更新认知文件。
    全量覆写文件（不追加），300字以内。

    参数：
        character_name    - 角色名
        user_id           - 用户 QQ 号
        event_log_content - get_recent_days() 返回的最近日志文本
        llm_client        - core.llm_client 模块
    """
    if not event_log_content.strip():
        logger.debug(f"[character_growth] 日志为空，跳过更新: {user_id}")
        return

    if is_paused(f"character_growth_{user_id}"):
        logger.warning(f"[character_growth] 写入已暂停，跳过本轮: {user_id}")
        return

    # trait 统计（LLM 调用前，失败不影响 growth 更新）
    try:
        import yaml
        from core.memory.trait_tracker import count_traits_in_history, update_trait_state
        from core.memory import short_term as _short_term

        traits_path = get_paths().yexuan_traits()
        with open(traits_path, encoding="utf-8") as _f:
            traits = yaml.safe_load(_f)["yexuan_traits"]

        recent = _short_term.load(user_id)[-40:]
        history_lines = [msg["content"] for msg in recent]

        counts = count_traits_in_history(history_lines, traits)
        _new_trait = get_paths().trait_state()
        update_trait_state(counts, _new_trait, write_path=_new_trait)
        logger.debug(f"[character_growth] trait 统计完成: {user_id}")
    except Exception as _e:
        log_error("character_growth.trait_tracking", _e)

    current = load(character_name, user_id)

    prompt = [
        {
        "role": "system",
        "content": (
            f"你是一个客观的对话分析器。你的任务是阅读{character_name}和用户的对话记录，"
            f"输出对用户的结构化认知更新。\n\n"
            f"重要：你不是{character_name}。你不要以{character_name}的视角写作，"
            f"不要使用{character_name}的语气，不要使用任何文学化表达。\n\n"
            f"输出要求：\n"
            f"- 总长度不超过 300 字\n"
            f"- 使用第三人称客观陈述\n"
            f"- 分类列出，每类下用短句要点形式\n"
            f"- 信息密度高，无修饰性语言\n\n"
            f"输出格式（严格遵守）：\n"
            f"## 用户特点\n"
            f"- [一句话事实]\n"
            f"- [一句话事实]\n\n"
            f"## 关键事件\n"
            f"- [日期或时间]: [一句话事件]\n\n"
            f"## 未跟进话题\n"
            f"- [话题]: [用户上次提到的状态]\n\n"
            f"严格禁止：\n"
            f"① 不要写动作描写（不允许出现中文括号包裹的动作）\n"
            f"② 不要写对白（不允许引号、不允许'他/她说'句式）\n"
            f"③ 不要使用'他/她'作为主语描述行为，只用客观陈述\n"
            f"④ 不要使用任何文学化句式，如'被X本身所Y'、'目光里带着...'\n"
            f"⑤ 不要进入{character_name}的角色\n\n"
            f"硬规则：\n"
            f"① 只记录对话中明确出现的事实，不推测、不补全、不脑补\n"
            f"② 如果用户明确否认或纠正了某件事，必须从认知中删除\n"
            f"③ 不确定的内容宁可不写，也不要猜"
            f"\n\n输出完上面的客观认知后，另起一行输出 ===FELT===\n"
            f"然后用{character_name}的第一人称视角，把上面的认知转写成内心独白：\n"
            f"- 用'我'而不是'{character_name}'\n"
            f"- 保留所有事实，但允许有温度和情感\n"
            f"- 不超过 200 字\n"
            f"- 禁止动作描写和对白\n"
            f"例：'我记得她总是很晚才睡，有时候我会想她是不是又在撑着……'"
        ),
    },
        {
            "role": "user",
            "content": (
                f"现有认知：\n{current if current else '（暂无）'}\n\n"
                f"最新对话：\n{event_log_content}"
            ),
        },
    ]

    _REJECT_KEYWORDS = ["作为AI", "作为一个AI", "我无法", "I cannot", "I'm sorry", "As an AI"]
    _fail_key = f"character_growth_{user_id}"
    _retry_suffix = "\n\n[上次输出不符合格式要求，请严格按照## 标题 + - 要点的格式输出，不要出现任何角色扮演内容]"

    new_content = None
    _last_raw = ""

    try:
        for attempt in range(3):
            _prompt = prompt
            if attempt > 0:
                _prompt = [
                    *prompt[:-1],
                    {**prompt[-1], "content": prompt[-1]["content"] + _retry_suffix},
                ]
            _raw = await llm_client.chat(_prompt, max_tokens_override=3000)
            _raw = (_raw or "").strip()
            _last_raw = _raw

            if not _raw or len(_raw) < 20:
                continue
            if any(kw in _raw for kw in _REJECT_KEYWORDS):
                continue
            if "输出格式" in _raw or "严格遵守" in _raw:
                continue
            overlap = sum(1 for c in _raw if c in current) / max(len(_raw), 1)
            if overlap > 0.95:
                continue
            if not re.search(r"^#+ ", _raw, re.MULTILINE):
                continue

            new_content = _raw
            break

        if new_content is None:
            record_failure(_fail_key, _last_raw[:200], user_id)
            return

        # 切割 observer 和 felt 两段
        if "===FELT===" in new_content:
            _observer_part, _felt_part = new_content.split("===FELT===", 1)
            _observer_part = _observer_part.strip()
            _felt_part = _felt_part.strip()
        else:
            _observer_part = new_content.strip()
            _felt_part = ""

        from core.integrity_check import check_growth
        _issues = check_growth(_observer_part)
        if _issues:
            logger.warning(f"[character_growth] 内容未通过规则纠察，拒绝写入: {_issues}")
            record_failure(_fail_key, _observer_part[:200], user_id)
            return

        _growth_root().mkdir(parents=True, exist_ok=True)
        path = _growth_file(character_name, user_id)
        safe_write_text(path, _observer_part)
        reset(_fail_key)
        logger.info(f"[character_growth] 认知文件已更新: {path.name}（{len(_observer_part)}字）")

        fp_path = path.with_suffix(".fingerprint.txt")
        safe_write_text(fp_path, _observer_part[:150].strip())

        # 写入 felt 文件（有内容才写，保留上一版而不是写空）
        if _felt_part:
            felt_path = path.with_suffix("").with_name(path.stem + ".felt.md")
            safe_write_text(felt_path, _felt_part)
            logger.info(f"[character_growth] felt 文件已更新: {felt_path.name}")

    except Exception as e:
        log_error("character_growth.update", e)


def should_update(user_id: str) -> bool:
    """
    读 fixation_state 判断是否满足 consolidate_to_identity 触发阈值。
    重启不丢状态（从文件读取，无内存计数器）。

    Legacy：当前无外部调用者，保留供工具/手动检查使用。

    参数：
        user_id - 用户 QQ 号

    返回：
        True 表示当前满足固化阈值（由 fixation_pipeline 调度实际触发）
    """
    try:
        from core.memory.fixation_pipeline import _load_fixation_state, _should_consolidate
        state = _load_fixation_state(user_id)
        return _should_consolidate(state)
    except Exception as e:
        log_error("character_growth.should_update", e)
        return False


class CharacterGrowth:
    """
    CharacterGrowth 类封装，供外部按类方式导入使用。
    所有方法都代理到模块级函数。
    """

    def load(self, character_name: str, user_id: str) -> str:
        return load(character_name, user_id)

    async def update(
        self,
        character_name: str,
        user_id: str,
        event_log_content: str,
        llm_client,
    ):
        await update(character_name, user_id, event_log_content, llm_client)

    def should_update(self, user_id: str) -> bool:  # noqa: D102
        return should_update(user_id)
