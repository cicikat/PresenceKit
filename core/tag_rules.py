"""
话题标签规则
substring 匹配主路径，规则集中在此文件维护，不散落在其他模块。
"""
import logging
from dataclasses import dataclass, field


@dataclass
class TagRule:
    tag: str
    patterns: list[str]


TAG_RULES: list[TagRule] = [
    TagRule("topic.energy",   ["累", "困", "没精神", "熬夜", "睡不着", "睡眠", "疲"]),
    TagRule("topic.health",   ["身体", "头疼", "发烧", "不舒服", "生病", "医院"]),
    TagRule("topic.activity", ["运动", "跑步", "健身", "走路", "步数"]),
    TagRule("topic.writing",  ["写作", "写诗", "小说", "散文", "创作"]),
    TagRule("topic.drawing",  ["画画", "绘画", "素描", "插画"]),
    TagRule("topic.music",    ["音乐", "作曲", "弹琴", "练琴"]),
    TagRule("topic.learning", ["学习", "练习", "在学", "在练"]),
    TagRule("query.body_state", ["今天状态", "最近怎么样", "身体怎么"]),
    TagRule("query.what_doing", ["你看到我在干嘛", "你知道我在做什么", "我在干嘛", "我在做什么"]),
    TagRule("query.growth_self", ["你最近在学什么", "你最近忙什么", "你最近在忙什么", "你有没有在练", "最近练了什么"]),
    TagRule("topic.body",     ["肚子", "痛", "生理期", "例假", "姨妈"]),
    TagRule("emotion.physical_discomfort", ["难受", "不舒服", "很疼"]),
    TagRule("topic.relation", ["我们", "你还记得", "之前", "那次", "上次"]),
    TagRule("topic.history",  ["那时候", "以前", "当时", "记得吗"]),
    # M5：长期/关系层问题。触发后 fetch_context 强制拉齐关系层记忆（long/repair 桶、全量 event_log 窗口）并注入
    # 相识日期事实。词表只放明确指向"关系/过往整体"的短语，不放"我们""之前"这类过泛单词（已被 topic.relation 占用）。
    TagRule("query.relationship_longterm", [
        "认识多久", "认识多少天", "认识多长时间", "在一起多久", "怎么看我", "觉得我是", "你眼里的我",
        "我在你心里", "我们之间", "过去发生", "以前发生", "这段时间以来", "一路走来", "我们的关系", "你还记得我们",
    ]),
    TagRule("emotion.deep",   ["其实", "说真的", "一直", "从来", "没人"]),
    TagRule("meta.identity",  ["你是谁", "你是什么", "你了解我吗"]),
    TagRule("emotion.down",     ["难过", "想哭", "想吐", "恶心", "痛苦", "呃呃", "呕呕", "想似"]),
    TagRule("emotion.positive", ["好耶", "噢噢噢", "喵喵喵"]),
    TagRule("emotion.indirect", ["咪", "好累", "不想动", "没胃口", "吃不下", "今天又没"]),
    # Brief 88：与 Dream D4.5 门控（dream_prompt._HIDDEN_STATE_TRIGGER_TAGS）同一标签名，
    # 现实侧 get_tags() 产出，供 user_hidden_state BODY_TOPIC 判定复用。
    TagRule("body_intimate", ["做爱", "啪啪", "上床", "情趣", "自慰", "高潮"]),
    TagRule("physical_closeness", ["贴近", "肌肤相亲", "缠绵", "依偎", "拥入怀"]),
]


_tag_logger = logging.getLogger("tag_rules.debug")


def get_tags(text: str) -> set[str]:
    """对用户消息跑 substring 匹配，返回命中的标签集合。"""
    tags = set()
    hit_details = []
    miss_details = []

    for rule in TAG_RULES:
        if not rule.patterns:
            continue
        hit_patterns = [p for p in rule.patterns if p in text]
        if hit_patterns:
            tags.add(rule.tag)
            hit_details.append(f"{rule.tag}（触发词：{'、'.join(hit_patterns)}）")
        else:
            miss_details.append(rule.tag)

    if _tag_logger.isEnabledFor(logging.DEBUG):
        _tag_logger.debug(
            f"[tag_rules] 输入：{text[:50]!r}\n"
            f"  命中（{len(tags)}）：{', '.join(hit_details) or '无'}\n"
            f"  未命中：{', '.join(miss_details) or '无'}"
        )

    return tags
