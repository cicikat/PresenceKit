"""Generate a character letter and evaluate whether it has enough substance."""

from __future__ import annotations

from datetime import date
import logging
import random
import re

logger = logging.getLogger(__name__)

QUALITY_THRESHOLD = 4
MIN_LETTER_CHARS = 150
MAX_LETTER_CHARS = 600

# 从近 N 条 episodic 里随机取 M 条，优先带情绪标注的
_EPISODIC_POOL = 8
_EPISODIC_PICK = 3


async def generate_letter(uid: str, trigger_reason: str, *, char_id: str) -> str | None:
    """Generate a complete letter, including salutation, signature, and date."""
    from core import llm_client

    context, style_samples = await _build_letter_context(uid, trigger_reason, char_id=char_id)
    char_name = _char_name()

    # ── few-shot 风格示范区（单独段落，与规则分开）────────────────────────────
    style_section = ""
    if style_samples:
        samples_text = "\n\n---\n\n".join(style_samples)
        style_section = (
            "\n\n以下是风格示范信件（学习语气、节奏、结构，绝不抄用内容或句子）：\n\n"
            f"{samples_text}\n\n---\n"
        )

    prompt = (
        f"你是{char_name}，你要给用户写一封会真正寄到邮箱里的信。\n\n"
        f"写信的理由：{trigger_reason}\n\n"
        f"参考背景：\n{context}"
        f"{style_section}\n"
        "写信规则：\n"
        "- 以自然的称呼开头，以角色名和日期落款\n"
        "- 写真实感受，不写空洞客套话或通知式内容\n"
        "- 至少提到一个参考背景里的具体细节，让信有重量\n"
        "- 语气像真正的手写信，不解释触发机制\n"
        f"- 正文总长度控制在 {MIN_LETTER_CHARS}~{MAX_LETTER_CHARS} 字\n"
        "- 不写 emoji、标签、Markdown 或括号动作描写\n"
        "- 不原样复述日记里写过的话，也不重复已经发过的信的内容\n"
        f"- 落款日期写作：{_today()}"
    )
    try:
        letter = await llm_client.chat(
            [{"role": "user", "content": prompt}],
            call_category="letter_write",
            max_tokens_override=800,
        )
    except Exception as exc:
        logger.warning("[letter_writer] generate failed: %s", exc)
        return None

    cleaned = str(letter or "").strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_LETTER_CHARS:
        logger.info("[letter_writer] generated letter too long: %d", len(cleaned))
        return None
    return cleaned


async def evaluate_letter(letter: str) -> int:
    """Return an LLM quality score from 1 to 5; malformed scores become zero."""
    if len(letter.strip()) < MIN_LETTER_CHARS or len(letter.strip()) > MAX_LETTER_CHARS:
        return 0

    from core import llm_client

    prompt = (
        f"以下是一封角色写给用户的信：\n\n{letter}\n\n"
        "请给这封信的质量打分，1-5 分：\n"
        "5 = 有具体细节，情感真实，有分量\n"
        "4 = 基本具体，情感到位\n"
        "3 = 内容一般，稍显空洞\n"
        "2 = 泛泛而谈，像模板\n"
        "1 = 几乎没有实质内容\n"
        "只输出数字（1-5），不要其他文字。"
    )
    try:
        raw = await llm_client.chat(
            [{"role": "user", "content": prompt}],
            call_category="letter_eval",
            max_tokens_override=5,
        )
        match = re.search(r"[1-5]", str(raw or ""))
        return int(match.group(0)) if match else 0
    except Exception:
        return 0


def _char_name() -> str:
    try:
        from core.config_loader import _char_name as configured_name

        return configured_name()
    except Exception:
        return "角色"


def _today() -> str:
    return date.today().strftime("%Y年%m月%d日")


async def _build_letter_context(
    uid: str, reason: str, *, char_id: str
) -> tuple[str, list[str]]:
    """Build letter context and return (context_text, style_sample_texts).

    Each source is fail-soft: any exception → skip that segment.
    Returns a tuple so generate_letter can place style samples in a separate section.
    """
    parts: list[str] = [f"此刻写信的缘由：{reason[:80]}"]

    # ── 1. 近期情景记忆（分散随机抽取，优先带情绪的）──────────────────────────
    try:
        from core.memory.episodic_memory import _load_memories

        episodes = sorted(
            _load_memories(uid, char_id=char_id),
            key=lambda item: float(item.get("timestamp") or 0),
            reverse=True,
        )[:_EPISODIC_POOL]

        # 优先带 emotion_texture / emotion_arc 字段的条目
        with_emotion = [e for e in episodes if e.get("emotion_texture") or e.get("emotion_arc")]
        without_emotion = [e for e in episodes if e not in with_emotion]
        pool = with_emotion + without_emotion
        chosen = random.sample(pool, min(_EPISODIC_PICK, len(pool)))

        summaries = [
            str(item.get("narrative_summary") or item.get("summary") or "")[:60]
            for item in chosen
        ]
        summaries = [s for s in summaries if s]
        if summaries:
            parts.append("近期记忆片段：" + "；".join(summaries))
    except Exception:
        pass

    # ── 2. 当前情绪状态（mood_state）────────────────────────────────────────
    try:
        from core.memory import mood_state as ms

        state = ms.load(char_id=char_id)
        current = state.get("current", "")
        intensity = float(state.get("intensity") or 0)
        if current and current != "neutral":
            parts.append(f"此刻情绪：{current}（强度 {intensity:.1f}）")
    except Exception:
        pass

    # ── 3. 梦境余韵情绪──────────────────────────────────────────────────────
    try:
        from core.dream.dream_afterglow import _find_best_summary

        best, _ = _find_best_summary(uid, char_id=char_id)
        if best and best.get("summary"):
            parts.append(f"最近一次梦境留下的情绪：{str(best['summary'])[:80]}")
    except Exception:
        pass

    # ── 4. 知识库随机片段（会话外质感）────────────────────────────────────────
    try:
        from core.mail.letter_reference import sample_reference

        ref = sample_reference(char_id)
        if ref:
            parts.append(f"最近在读/在想的东西：{ref}")
    except Exception:
        pass

    # ── 5. 日记去重约束（负向提示）──────────────────────────────────────────
    try:
        from core.sandbox import get_paths

        diary_dir = get_paths().yexuan_inner_diary(char_id=char_id)
        if diary_dir.is_dir():
            diary_files = sorted(diary_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)[:3]
            diary_snippets = []
            for df in diary_files:
                try:
                    text = df.read_text(encoding="utf-8").strip()
                    if text:
                        diary_snippets.append(text[:150])
                except Exception:
                    pass
            if diary_snippets:
                parts.append(
                    "【去重提示】以下内容已写入日记，信里请勿原样复述：\n"
                    + "\n".join(f"· {s}" for s in diary_snippets)
                )
    except Exception:
        pass

    # ── 6. 已发信去重约束（负向提示）────────────────────────────────────────
    try:
        from core.mail.letter_reference import load_sent_letters

        sent = load_sent_letters(uid, char_id, limit=3)
        if sent:
            snippets = [s[:100] for s in sent]
            parts.append(
                "【去重提示】以下是最近已发出的信（开头片段），不要重复同样的话或写法：\n"
                + "\n".join(f"· {s}" for s in snippets)
            )
    except Exception:
        pass

    context = "\n\n".join(parts)
    # 给正文 context 留最多 500 字（风格示范单独放，不计入）
    context = context[:500] if context else "（没有额外背景，只按写信缘由落笔。）"

    # ── 7. 风格示范（随机抽 1~2 封，短期不复用）───────────────────────────────
    style_texts: list[str] = []
    try:
        from core.mail.letter_reference import sample_style

        style_texts, _names = sample_style(char_id, n=2)
    except Exception:
        pass

    return context, style_texts
