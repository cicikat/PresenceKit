"""
core/perform_mapper.py — 句级表演意图映射（Brief 20）

把 LLM 回复中已有的 do/feel 叙事段映射为受控表演 spec，挂到 message_segments 的
say 段上（可选 `perform` 键），驱动桌面端逐句表演。任何内部异常/超时都 fail-open，
原样返回输入 say_segs——不影响 message_segments 主流程。

两个 provider：
  rules（默认，v1）— 纯规则词典，零成本零延迟。
  llm（v2，config 门控）— 整条回复一次调用，严格 schema 校验，超时/解析失败 fail-open。

契约见 cc-tasks/20-句级表演意图映射-后端.md §1（与
PresenceKit-desktop `docs/protocol-v0.md` 的 perform/action 约定一致）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from core.config_loader import get_config

logger = logging.getLogger(__name__)

_EXPRESSIONS = frozenset({
    "neutral", "gentle", "thinking", "happy", "sad", "surprised", "angry", "sleepy", "yandere",
})
_HEADS = frozenset({"nod", "shake", "tilt_l", "tilt_r", "dip"})
_POSTURES = frozenset({"lean_in", "lean_back", "shrink", "straighten"})
_GAZES = frozenset({"user", "away", "down", "wander"})

_DEFAULT_INTENSITY = 0.6
_DEFAULT_ENERGY = 0.5

# Table order matters: within a field, the first matching rule wins.
# "also_say": rule may also match on the say text itself (expression words only —
# posture/head/gaze rely on do/feel text alone per spec §2.2).
_WORD_RULES: list[dict] = [
    {"pattern": re.compile(r"凑近|靠近|贴近|探身"), "field": "posture", "value": "lean_in"},
    # "往后缩" excludes "缩成"（"往后缩成一团" is a shrink, not a lean-back）
    {"pattern": re.compile(r"后仰|退开|往后缩(?!成)"), "field": "posture", "value": "lean_back"},
    {"pattern": re.compile(r"缩|蜷|抱膝|埋进"), "field": "posture", "value": "shrink", "energy_delta": -0.2},
    {"pattern": re.compile(r"挺直|坐直|站直"), "field": "posture", "value": "straighten"},
    {"pattern": re.compile(r"点头|点了点头"), "field": "head", "value": "nod"},
    {"pattern": re.compile(r"摇头|摇了摇头"), "field": "head", "value": "shake"},
    {"pattern": re.compile(r"歪头|侧过头"), "field": "head", "value": "tilt_r"},
    {"pattern": re.compile(r"低头|垂下头|垂眸"), "field": "head", "value": "dip", "extra": {"gaze": "down"}},
    {"pattern": re.compile(r"看着你|盯着|直视"), "field": "gaze", "value": "user"},
    {"pattern": re.compile(r"移开视线|别过头|看向别处|避开目光"), "field": "gaze", "value": "away"},
    {"pattern": re.compile(r"环顾|张望"), "field": "gaze", "value": "wander"},
    {"pattern": re.compile(r"笑|勾起嘴角|眉眼弯弯"), "field": "expression", "value": "happy", "also_say": True},
    {"pattern": re.compile(r"叹气|垂下肩|黯淡"), "field": "expression", "value": "sad", "energy_delta": -0.2, "also_say": True},
    {"pattern": re.compile(r"瞪大眼|愣住|怔住"), "field": "expression", "value": "surprised", "also_say": True},
    {"pattern": re.compile(r"鼓起脸|瞪了.*?一眼|哼"), "field": "expression", "value": "angry", "intensity": 0.5, "also_say": True},
    {"pattern": re.compile(r"脸红|耳尖发烫|害羞"), "field": "expression", "value": "gentle", "extra": {"gaze": "away"}, "also_say": True},
]


async def enrich_say_segments(reply: str, say_segs: list, *, char_id: str) -> list:
    """输入 build_say_segments 的产出，返回同长度 segments（say 段可能多出 perform 键）。
    任何内部异常/超时 → 原样返回输入（fail-open，绝不影响主流程）。
    """
    try:
        return await _enrich(reply, say_segs, char_id=char_id)
    except Exception:
        logger.debug("[perform_mapper] enrich failed, fail-open", exc_info=True)
        return say_segs


async def _enrich(reply: str, say_segs: list, *, char_id: str) -> list:
    cfg = get_config().get("performance_mapping", {})
    if not cfg.get("enabled", True) or not say_segs:
        return say_segs

    from core.narrative_parser import parse_narrative_segments

    all_segs = parse_narrative_segments(reply)["segments"]
    mapping_inputs = _assign_action_text(all_segs, say_segs)
    if mapping_inputs is None:
        return say_segs

    provider = cfg.get("provider", "rules")
    if provider == "llm":
        timeout = float(cfg.get("llm_timeout_sec", 3.0))
        try:
            performs = await asyncio.wait_for(
                _call_llm_for_perform(mapping_inputs, char_id=char_id), timeout=timeout,
            )
        except Exception:
            logger.debug("[perform_mapper] llm provider failed/timeout, fail-open", exc_info=True)
            performs = [None] * len(mapping_inputs)
    else:
        performs = [_map_with_rules(action_text, say_text) for action_text, say_text in mapping_inputs]

    result = []
    for seg, perform in zip(say_segs, performs):
        new_seg = dict(seg)
        if perform:
            new_seg["perform"] = perform
        result.append(new_seg)
    return result


def _assign_action_text(all_segs: list, say_segs: list) -> list[tuple[str, str]] | None:
    """把 do/feel 文本按 §2.1 规则挂靠到对应 say 段，返回 [(action_text, say_text), ...]。
    数量对不上（parser 输出与调用方 say_segs 不一致）→ None，调用方 fail-open。
    """
    say_type_segs = [s for s in all_segs if s.get("type") == "say"]

    if not say_type_segs:
        # No say segments at all: build_say_segments already fell back to a single
        # whole-content segment — map every do/feel line onto that one segment.
        if len(say_segs) != 1:
            return None
        combined = " ".join(s.get("text", "") for s in all_segs if s.get("type") in ("do", "feel"))
        return [(combined, say_segs[0].get("text", ""))]

    if len(say_type_segs) != len(say_segs):
        return None

    inputs: list[tuple[str, str]] = []
    buffer: list[str] = []
    say_idx = 0
    for seg in all_segs:
        seg_type = seg.get("type")
        if seg_type in ("do", "feel"):
            buffer.append(seg.get("text", ""))
        elif seg_type == "say":
            action_text = " ".join(buffer)
            buffer = []
            inputs.append((action_text, say_segs[say_idx].get("text", "")))
            say_idx += 1

    if buffer and inputs:
        last_action, last_say = inputs[-1]
        inputs[-1] = (f"{last_action} {' '.join(buffer)}".strip(), last_say)

    return inputs


def _map_with_rules(action_text: str, say_text: str) -> dict | None:
    fields: dict[str, str] = {}
    energy_delta = 0.0
    intensity = None
    matched_any = False

    for rule in _WORD_RULES:
        field = rule["field"]
        if field in fields:
            continue
        pool = (action_text, say_text) if rule.get("also_say") else (action_text,)
        if not any(text and rule["pattern"].search(text) for text in pool):
            continue

        matched_any = True
        fields[field] = rule["value"]
        energy_delta += rule.get("energy_delta", 0.0)
        if "intensity" in rule:
            intensity = rule["intensity"]
        for extra_field, extra_value in rule.get("extra", {}).items():
            if extra_field not in fields:
                fields[extra_field] = extra_value

    stripped_say = say_text.rstrip()
    if stripped_say.endswith(("！", "!")):
        energy_delta += 0.2
        matched_any = True
    if stripped_say.endswith("……") or say_text.count("……") >= 2:
        energy_delta -= 0.15
        matched_any = True

    if not matched_any:
        return None

    energy = max(0.0, min(1.0, _DEFAULT_ENERGY + energy_delta))
    return {
        "expression": fields.get("expression"),
        "intensity": intensity if intensity is not None else _DEFAULT_INTENSITY,
        "head": fields.get("head"),
        "posture": fields.get("posture"),
        "gaze": fields.get("gaze"),
        "energy": round(energy, 2),
    }


# ── llm provider (v2) ────────────────────────────────────────────────────────

_VOCAB_HINT = (
    "expression: neutral|gentle|thinking|happy|sad|surprised|angry|sleepy|yandere|null\n"
    "head: nod|shake|tilt_l|tilt_r|dip|null\n"
    "posture: lean_in|lean_back|shrink|straighten|null\n"
    "gaze: user|away|down|wander|null\n"
    "intensity: 0~1 数字，缺省 0.6\n"
    "energy: 0~1 数字，缺省 0.5"
)

_SYSTEM_PROMPT = (
    "你是一个表演意图标注器。给定若干句台词及其挂靠的动作/感受描写，"
    "为每一句输出一个表演 spec 或 null。宁缺毋滥——只在动作/感受文本有明确信号时才置值，"
    "没有信号的通道输出 null，不要给每句强行填满。\n"
    f"字段取值：\n{_VOCAB_HINT}\n"
    "严格输出 JSON 数组，长度必须与输入句数相同，不要输出任何其他文字。"
    '数组第 i 个元素对应第 i 句台词，取值为 null 或 '
    '{"expression":...,"intensity":...,"head":...,"posture":...,"gaze":...,"energy":...}。'
)

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _build_llm_user_prompt(mapping_inputs: list[tuple[str, str]]) -> str:
    lines = []
    for i, (action_text, say_text) in enumerate(mapping_inputs, start=1):
        lines.append(f"{i}. 动作/感受：{action_text or '（无）'}\n   台词：{say_text}")
    return "\n".join(lines)


async def _call_llm_for_perform(mapping_inputs: list[tuple[str, str]], *, char_id: str) -> list[dict | None]:
    from core.llm_client import chat

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_llm_user_prompt(mapping_inputs)},
    ]
    raw = await chat(messages, call_category="perform")
    data = _parse_json_array(raw, expected_len=len(mapping_inputs))
    if data is None:
        return [None] * len(mapping_inputs)
    return [_sanitize_llm_perform(item) for item in data]


def _parse_json_array(raw: str, *, expected_len: int) -> list | None:
    if not raw:
        return None
    text = _JSON_FENCE_RE.sub("", raw.strip()).strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, list) or len(data) != expected_len:
        return None
    return data


def _sanitize_llm_perform(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None

    expression = raw.get("expression") if raw.get("expression") in _EXPRESSIONS else None
    head = raw.get("head") if raw.get("head") in _HEADS else None
    posture = raw.get("posture") if raw.get("posture") in _POSTURES else None
    gaze = raw.get("gaze") if raw.get("gaze") in _GAZES else None

    if expression is None and head is None and posture is None and gaze is None:
        return None

    try:
        intensity = float(raw.get("intensity", _DEFAULT_INTENSITY))
    except (TypeError, ValueError):
        intensity = _DEFAULT_INTENSITY
    try:
        energy = float(raw.get("energy", _DEFAULT_ENERGY))
    except (TypeError, ValueError):
        energy = _DEFAULT_ENERGY

    return {
        "expression": expression,
        "intensity": round(max(0.0, min(1.0, intensity)), 2),
        "head": head,
        "posture": posture,
        "gaze": gaze,
        "energy": round(max(0.0, min(1.0, energy)), 2),
    }
