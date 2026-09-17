"""
元数据写入前的规则纠察。
确定性规则检查，不依赖 LLM，用于拒绝格式违规的写入。
"""
import re
import logging

logger = logging.getLogger(__name__)


def check_growth(content: str) -> list[str]:
    """检查 character_growth 内容合规性"""
    issues = []
    if not re.search(r"^#+ ", content, re.MULTILINE):
        issues.append("缺标题结构（需含 # 开头的标题）")
    if not re.search(r"^- ", content, re.MULTILINE):
        issues.append("缺列表条目（需含 - 开头的要点）")
    return issues


def check_diary_facts(content: str) -> list[str]:
    """检查日记事件层合规性"""
    issues = []
    if "## 今日事件" not in content:
        issues.append("缺 ## 今日事件 标记")
    if not re.search(r"^- ", content, re.M):
        issues.append("事件条目不是 - 列表格式")
    if "（" in content or "）" in content:
        issues.append("含中文括号（动作描写标志）")
    if "“" in content or "”" in content:
        issues.append("含引号（对白标志）")
    return issues

_PROFILE_ALLOWED_KEYS = {"name", "location", "pets", "interests", "occupation", "important_facts", "_pending_overrides"}


def check_profile(data: dict) -> list[str]:
    """检查 user_profile 内容合规性"""
    issues = []
    if not isinstance(data, dict):
        issues.append("profile 不是 dict")
        return issues
    unexpected = set(data.keys()) - _PROFILE_ALLOWED_KEYS
    if unexpected:
        issues.append(f"含非法字段：{unexpected}")
    if not isinstance(data.get("important_facts", []), list):
        issues.append("important_facts 不是列表")
    for k in ("name", "location", "pets", "interests", "occupation"):
        v = data.get(k)
        if v is not None and not isinstance(v, str):
            issues.append(f"{k} 字段不是字符串")
        if isinstance(v, str) and len(v) > 100:
            issues.append(f"{k} 字段过长（超100字）")
    facts = data.get("important_facts", [])
    if isinstance(facts, list) and len(facts) > 20:
        issues.append("important_facts 条数超过20条")
    return issues
