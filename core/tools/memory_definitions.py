"""Memory tool definitions and invocation adapters.

Domain implementations and trusted invocation bindings have one owner.
"""

import core.tools.activity_records as _domain_core_tools_activity_records
import core.tools.event_tools as _domain_core_tools_event_tools
import core.tools.message_context as _domain_core_tools_message_context

from core.tool_runtime.context import _active_char_id
from core.tools.memory_dossiers import get_memory_consolidation_status as _get_memory_consolidation_status_wrapper
from core import quote_notebook as _quote_notebook
from core.food_memory import read_food_preferences as _read_food_preferences
from core.tools.memory_dossiers import read_memory_dossier as _read_memory_dossier_wrapper
from core.tools.memory_dossiers import request_memory_consolidation as _request_memory_consolidation_wrapper
from core.tool_runtime.policy import _require_memory_read_scope
from core.tools.memory_dossiers import search_dossier_events as _search_dossier_events_wrapper
from core.tools.memory_dossiers import search_memory_dossiers as _search_memory_dossiers_wrapper
from core import tool_result_pins as _tool_result_pins
from core.tools.memory_dossiers import update_memory_dossier as _update_memory_dossier_wrapper
from core import character_name_provider
from core import config_loader
import json
import logging
logger = logging.getLogger("core.tool_dispatcher")


async def _read_diary_wrapper(user_id: str, date: str = "", *, char_id: str | None = None) -> str:
    from core.tools.character_recall import read_character_diary_for_user
    return await read_character_diary_for_user(user_id, char_id or _active_char_id(), date_str=date)


async def _backfill_diary_wrapper(user_id: str, date: str = "", *, char_id: str) -> str:
    from core.tools.diary_backfill import backfill_diary
    return await backfill_diary(user_id, char_id, date)


async def _read_watch_wrapper(user_id: str, query: str = "", *, char_id: str | None = None) -> str:
    from core.tools.watch_tool import read_watch_for_user
    return read_watch_for_user(user_id, query, char_id=char_id or _active_char_id())


async def _read_hds_heart_rate_wrapper(user_id: str, *, char_id: str | None = None) -> str:
    from core.hds_local import read_status
    return json.dumps(read_status(user_id, char_id=char_id or _active_char_id()), ensure_ascii=False)


async def _search_diary_wrapper(
    user_id: str, query: str = "", date: str = "", *, char_id: str | None = None,
) -> str:
    from core.tools.character_recall import search_character_diary_for_user
    return await search_character_diary_for_user(user_id, char_id or _active_char_id(), query, date_str=date)


async def _search_documents_wrapper(user_id: str, query: str = "", media_type: str = "", *, char_id: str):
    from core.tools.character_recall import search_documents_for_user
    return await search_documents_for_user(user_id, char_id, query, media_type)


async def _read_document_wrapper(user_id: str, document_id: str, offset: int = 0, mode: str = "context", query: str = "", limit: int = 6000, *, char_id: str):
    from core.tools.character_recall import read_document_for_user
    return await read_document_for_user(user_id, char_id, document_id, offset, mode=mode, query=query, limit=limit)


async def _search_character_notes_wrapper(user_id: str, query: str = "", *, char_id: str) -> str:
    from core.tools.character_recall import search_character_notes_for_user
    return await search_character_notes_for_user(user_id, char_id, query)


async def _read_document_notes_wrapper(user_id: str, *, char_id: str, **kwargs):
    from core.document_notes import read_for_user
    return await read_for_user(user_id, char_id=char_id, **kwargs)


async def _write_document_note_wrapper(user_id: str, *, char_id: str, **kwargs):
    from core.document_notes import write_for_user
    return await write_for_user(user_id, char_id=char_id, **kwargs)


async def _get_profile_wrapper(user_id: str, *, char_id: str) -> str:
    """召回用户画像。"""
    from core.memory import user_profile
    from core.memory.scope import MemoryScope
    from core.memory.temporal_facts import project
    stored_profile = user_profile.load(user_id, char_id=char_id)
    state = project(MemoryScope.reality_scope(user_id, char_id), stored_profile)
    profile = state["profile"]
    if not stored_profile and not state["_temporal_fact_decisions"]:
        return "暂无用户画像"
    parts = []
    if profile.get("nickname"):
        parts.append(f"称呼：{profile['nickname']}")
    if profile.get("location"):
        parts.append(f"位置：{profile['location']}")
    if profile.get("occupation"):
        parts.append(f"职业：{profile['occupation']}")
    parts.extend("当前事实：" + row["value"] for row in profile.get("_temporal_current", []))
    facts = profile.get("important_facts", [])
    if facts:
        parts.append("已知信息：" + "；".join(str(f) for f in facts[:10]))
    return "\n".join(parts) if parts else "暂无详细信息"


async def _get_episodic_wrapper(user_id: str, topic: str = "", *, char_id: str) -> str:
    """召回情景记忆。"""
    from core.memory.episodic_memory import retrieve, format_for_prompt
    # M4：查询不再顺手给被查到的记忆加强度（allow_strengthen=False）
    memories = retrieve(user_id=user_id, topic=topic, top_k=3, char_id=char_id, allow_strengthen=False)
    if not memories:
        return "暂无相关记忆"
    from core.memory import user_profile
    from core.memory.scope import MemoryScope
    from core.memory.temporal_facts import project
    text = format_for_prompt(memories, char_name=character_name_provider.get_char_name(char_id))
    return project(MemoryScope.reality_scope(user_id, char_id), user_profile.load(user_id, char_id=char_id),
                   {"episodic_result": text})["episodic_result"]


async def _read_long_user_message_wrapper(user_id: str, sequence: int, offset: int = 0,
                                          *, char_id: str) -> str:
    from core.memory.short_term import read_long_user_message
    return read_long_user_message(user_id, sequence, offset=offset, char_id=char_id)


async def _revise_memory_wrapper(user_id: str, episode_id: str, correction: str, *, char_id: str) -> str:
    from core.memory.episodic_memory import revise_episode
    correction_id = revise_episode(user_id, episode_id, correction, char_id=char_id)
    if not correction_id:
        return "没有找到可更正的那条记忆，或更正内容为空。"
    return f"已更正那段记忆：原条目已降级，补充了新的更正记录（{correction_id}）。"


async def _forget_episodic_wrapper(
    user_id: str,
    episode_id: str = "",
    topic: str = "",
    *,
    char_id: str,
) -> str:
    from core.memory.episodic_memory import forget_episodes

    episode_id = str(episode_id or "").strip()
    topic = str(topic or "").strip()
    if not episode_id and not topic:
        return "请提供要遗忘的 episode_id，或一个具体 topic。"
    forgotten = forget_episodes(
        user_id,
        episode_id=episode_id,
        topic=topic,
        char_id=char_id,
        origin={"source": "assistant_tool", "tool": "forget_episodic"},
    )
    if not forgotten:
        return "没有找到需要遗忘的情景记忆。"
    return f"已将 {len(forgotten)} 条情景记忆降级为不再主动召回的归档。"


async def _clear_midterm_wrapper(user_id: str, *, char_id: str) -> str:
    from core.memory.mid_term import clear

    removed = clear(
        user_id,
        char_id=char_id,
        origin={"source": "assistant_tool", "tool": "clear_midterm"},
    )
    return f"已清空当前 {removed} 条中期记忆。" if removed else "当前没有可清空的中期记忆。"


async def _revise_user_profile_wrapper(user_id: str, field: str, correction: str, *, char_id: str) -> str:
    from core.memory.user_identity import overwrite_dimension
    ok = await overwrite_dimension(
        user_id, field, correction, char_id=char_id,
        trigger_signal="user_profile_correction",
        origin={"source": "assistant_tool", "tool": "revise_user_profile"},
    )
    return "已更新用户画像中的这一项。" if ok else "这个画像字段不存在或内容无效，未能更新。"


async def _exit_yandere_wrapper() -> str:
    from core.deployment_capabilities import is_remote_server
    if is_remote_server():
        return "disabled_remote_server_local_capability"
    import json
    from pathlib import Path
    from core import config_loader
    emerald_path = config_loader.get_config().get("emerald_desktop", {}).get("path", "")
    if not emerald_path:
        logger.warning("[exit_yandere] config.yaml 未配置 emerald_desktop.path，跳过")
        return "未配置旧 Emerald-desktop 路径"
    signal_file = Path(emerald_path) / "data" / "yandere_exit.signal"
    signal_file.parent.mkdir(parents=True, exist_ok=True)
    signal_file.write_text(json.dumps({"exit": True}), encoding="utf-8")
    return f"{character_name_provider.get_active_char_name()}平静下来了"


async def _invoke_read_document_notes(call):
    func = call.func
    tool_args = call.tool_args
    user_id = call.user_id
    char_id = call.char_id
    is_group = call.is_group
    owner_id = str(config_loader.get_config().get("scheduler", {}).get("owner_id") or "")
    if is_group or not owner_id or str(user_id) != owner_id:
        raise ValueError('document notes are owner-private only')
    _require_memory_read_scope(user_id, char_id)
    result = await func(user_id=user_id, char_id=char_id, **tool_args)

    return result


async def _invoke_read_life_records(call):
    func = call.func
    tool_args = call.tool_args
    user_id = call.user_id
    char_id = call.char_id
    is_group = call.is_group
    if is_group:
        raise ValueError('owner memory tools are unavailable in group sessions')
    _require_memory_read_scope(user_id, char_id)
    result = await func(user_id=user_id, char_id=char_id, **tool_args)

    return result


async def _invoke_get_profile(call):
    func = call.func
    tool_args = call.tool_args
    user_id = call.user_id
    char_id = call.char_id
    _require_memory_read_scope(user_id, char_id)
    result = await func(user_id=user_id, char_id=char_id, **tool_args)

    return result


async def _invoke_update_memory_dossier(call):
    func = call.func
    tool_args = call.tool_args
    user_id = call.user_id
    char_id = call.char_id
    _require_memory_read_scope(user_id, char_id)
    result = await func(user_id=user_id, char_id=char_id, **tool_args)

    return result


async def _invoke_backfill_diary(call):
    func = call.func
    tool_args = call.tool_args
    user_id = call.user_id
    char_id = call.char_id
    _require_memory_read_scope(user_id, char_id)
    result = await func(user_id=user_id, char_id=char_id, **tool_args)

    return result


async def _invoke_read_diary(call):
    func = call.func
    tool_args = call.tool_args
    user_id = call.user_id
    char_id = call.char_id
    _require_memory_read_scope(user_id, char_id)
    result = await func(user_id=user_id, char_id=char_id, **tool_args)

    return result


async def _invoke_get_listening_state(call):
    func = call.func
    tool_args = call.tool_args
    user_id = call.user_id
    char_id = call.char_id
    for key in ("user_id", "uid", "char_id", "owner"):
        if key in tool_args:
            result = "grant_principal_mismatch"
            break
    else:
        result = await func(user_id=user_id, char_id=char_id, **tool_args)

    return result


async def _invoke_pin_character_image_reference(call):
    func = call.func
    tool_args = call.tool_args
    user_id = call.user_id
    char_id = call.char_id
    origin = call.origin
    is_group = call.is_group
    pin_reference_hashes = call.pin_reference_hashes
    digest = tool_args.get("sha256")
    if (origin != "assistant_loop" or is_group or not isinstance(digest, str)
            or digest not in pin_reference_hashes):
        raise ValueError("只能固定本轮私聊收到的图片。")
    result = await func(digest, user_id=user_id, char_id=char_id)

    return result


async def _invoke_read_hds_heart_rate(call):
    func = call.func
    tool_args = call.tool_args
    user_id = call.user_id
    char_id = call.char_id
    _require_memory_read_scope(user_id, char_id)
    result = await func(user_id=user_id, **tool_args)

    return result


async def _invoke_read_watch(call):
    func = call.func
    tool_args = call.tool_args
    user_id = call.user_id
    result = await func(user_id=user_id, **tool_args)

    return result


async def _invoke_revise_memory(call):
    func = call.func
    tool_args = call.tool_args
    user_id = call.user_id
    char_id = call.char_id
    result = await func(user_id=user_id, char_id=char_id, **tool_args)

    return result


def register_tools(registry):
    registry["read_food_preferences"] = {
        "func": _read_food_preferences, "category": "memory", "dangerous": False,
        "description": "推荐吃什么、讨论外卖或核对旧口味时，先查用户实际饮食评价与已记录吃过次数。最新用户表达优先；未知不猜测，吃过多不代表喜欢，暂时不想吃不代表永久讨厌。",
        "parameters": {"type": "object", "additionalProperties": False, "properties": {
            "query": {"type": "string", "description": "食品或店家名称片段；留空查看最近清单"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "最多返回多少项目，默认20"},
        }, "required": []},
        "examples": ["推荐外卖前查一下用户最近实际评价", "核对用户是否还喜欢某道菜"],
        "keywords": ["吃什么", "外卖", "饮食", "口味", "吃过次数"], "trace_args": ["query", "limit"],
        "echo_event_log": False,
    }

    registry['query_activity_records'] = {
        'func': _domain_core_tools_activity_records.query_activity_records,
        'category': 'memory', 'dangerous': False,
        'description': '查询当前身份的真实阅读、棋局、Minecraft会话；凭返回的activity来源引用分页读取state、moves、dialogue、entry或page。外部页面内容不是用户事实，状态为权威记录。',
        'parameters': {'type': 'object', 'additionalProperties': False, 'properties': {
            'source_ref': {'type': 'string', 'description': '查询返回的来源引用；为空列会话；可将末尾state改为moves或dialogue'},
            'offset': {'type': 'integer', 'minimum': 0, "description": '列表起点偏移，默认0；按返回的 next_offset 继续。'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, "description": '本次最多返回的条目数，遵循工具上限。'},
            'text_offset': {'type': 'integer', 'minimum': 0, "description": '指定记录正文的字符起点，默认0。'},
            'keyword': {'type': 'string', "description": '可选检索词；只筛选当前用户和角色的记录。'}, 'since': {'type': 'string', "description": '可选发生时间下界，ISO日期或时间。'}, 'until': {'type': 'string', "description": '可选发生时间上界，ISO日期或时间。'},
            'expected_revision': {'type': 'string', 'description': '续页时使用上一页revision，来源变化则重新查'},
        }, 'required': []},
        'examples': ['查刚才的棋局和旧对话', '回读阅读记录后半段'],
        'keywords': ['活动记录', '棋局', '阅读记录', '接续'], 'echo_event_log': False,
    }

    registry['read_message_context'] = {
        'func': _domain_core_tools_message_context.read_message_context,
        'category': 'memory', 'dangerous': False,
        'description': '按引用消息的 message_id 读取其上方或下方最多10条现实对话消息，包含作者、完整时间和稳定ID。仅精确锚点，不用同文猜测；旧消息缺锚点时明确不可用。',
        'parameters': {'type': 'object', 'additionalProperties': False, 'properties': {
            'message_id': {'type': 'string', 'description': '引用中的稳定消息ID，通常为 turn_id:user 或 turn_id:assistant'},
            'direction': {'type': 'string', 'enum': ['before', 'after'], 'description': '向上 before 或向下 after，默认before'},
            'count': {'type': 'integer', 'minimum': 1, 'maximum': 10, 'description': '邻近消息数量，默认5，最多10'},
        }, 'required': ['message_id']},
        'examples': ['看看被引用那句话前5条消息', '查那条消息后面的10条'],
        'keywords': ['引用', '上文', '下文', '邻近消息'], 'trace_args': ['message_id', 'direction', 'count'],
        'echo_event_log': False,
    }

    for _pin_name, _pin_description, _pin_properties, _pin_required in (
        ('pin_tool_result','临时驻留真实工具结果1到10轮。只接受成功结果或 list_tool_result_pins 提供的完整 result_id；没有 ID 的结果无法驻留。最多3项，硬过期最多24小时。',
         {'result_id':{'type':'string','description':'成功结果或候选列表给出的完整 32 位 result_id'},'rounds':{'type':'integer','minimum':1,'maximum':10,'description':'后续保留轮数，角色按需选择'}},['result_id','rounds']),
        ('unpin_tool_result','取消临时驻留的工具结果，下一轮停止注入。',{'result_id':{'type':'string','description':'已标记的结果ID'}},['result_id']),
        ('list_tool_result_pins','查看已驻留项以及最近5个可驻留候选的真实 ID、工具名称和时间。没有 ID 或找不到时，先查候选列表。',{},[]),
    ):
        registry[_pin_name]={'func':getattr(_tool_result_pins,_pin_name),'category':'memory','dangerous':False,
            'description':_pin_description,'parameters':{'type':'object','additionalProperties':False,'properties':_pin_properties,'required':_pin_required},
            'examples':[_pin_description],'keywords':['临时驻留','复用','标记工具结果'],'trace_args':list(_pin_properties),'echo_event_log':False}

    for _quote_tool, _quote_description, _quote_properties, _quote_required in (
        ('save_quote', '收藏用户或角色已经入账的原话到小本本。先从引用、search_events或read_message_context获取精确message_id；当前尚未完成的回复不能收藏。保存日期、来源及最多3条相关召回快照，可写笔记。',
         {'message_id': {'type':'string','description':'精确消息ID'}, 'note': {'type':'string','description':'角色笔记，最多2000字'},
          'related_event_ids': {'type':'array','items':{'type':'string'},'description':'最多3条已查证相关记忆event_id，不填则无关联'}}, ['message_id']),
        ('search_quotes', '搜索双方收藏的小本本，可以按原话或笔记检索，默认20项、最多50项；返回quote_id供打开和补笔记。',
         {'query':{'type':'string','description':'搜索原话或笔记，留空查看最近收藏'},'limit':{'type':'integer','minimum':1,'maximum':50,'description':'条数，默认20'}}, []),
        ('read_quote', '打开收藏原话、作者、原消息及收藏时间、当时相关召回与笔记。快照保留原话，源消息后来不可用会标注；不是当前状态证据。',
         {'quote_id':{'type':'string','description':'收藏ID'}}, ['quote_id']),
        ('write_quote_note', '为收藏补写或显式更新自己的笔记；不覆盖原话和收藏时关联，旧笔记留变更记录。',
         {'quote_id':{'type':'string','description':'收藏ID'},'note':{'type':'string','description':'新笔记正文，最多2000字'}}, ['quote_id','note']),
    ):
        registry[_quote_tool] = {'func':getattr(_quote_notebook,_quote_tool),'category':'memory','dangerous':False,
            'description':_quote_description,'parameters':{'type':'object','additionalProperties':False,'properties':_quote_properties,'required':_quote_required},
            'examples':[_quote_description], 'keywords':['收藏','小本本','原话','笔记'], 'trace_args':list(_quote_properties),'echo_event_log':False}

    registry["backfill_diary"] = {
        "func": _backfill_diary_wrapper,
        "description": (
            "用户要求补写时，补写当前角色自己的缺失日记。当天仅本地时间23点后允许；"
            "昨天可在今天全天补写；更早日期不允许。已有日记绝不覆盖，无记录不编造。"
            "工具自行核验是否缺失；不需要先读日记。"
        ),
        "dangerous": False, "category": "info", "effect": "write",
        "echo_event_log": False,
        "parameters": {"type": "object", "additionalProperties": False, "properties": {
            "date": {"type": "string", "description": "今天、昨天或 YYYY-MM-DD；省略时23点前补昨天，23点起补今天。"},
        }, "required": []},
        "examples": ["补写一下你昨天的日记", "今天的日记没写，帮我补上", "昨晚报错了，把你的日记补一下"],
        "keywords": ["补写日记", "补日记", "日记没写", "日记补一下", "日记补上"],
        "trace_args": ["date"],
    }

    registry["read_diary"] = {
        "func": _read_diary_wrapper,
        "description": (
            "读取角色指定日期的日记。仅在用户明确询问过去手记、邀请{char}核实或评价日记时调用；"
            "不要因为日常对话中偶然提到“日记”而自行读取。"
        ),
        "dangerous": False,
        "category": "info",
        "persist": True,
        "write_short_term": False,
        "echo_event_log": False,
        "examples": [
            "帮我看看今天的日记",
            "你来读读我写的日记",
            "评价一下我最近的日记",
            "把我的日记给你看",
            "读一下我4月10号写的",
        ],
        "keywords": ["看日记", "读日记", "日记给你看", "日记给你", "日记读一下", "日记你看看"],
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "要读取的日期，例如“4月10日”或“04-10”；省略时读取当天日记。",
                },
            },
            "required": [],
        },
        "trace_args": ["date"],
    }

    registry["read_watch"] = {
        "func": _read_watch_wrapper,
        "description": "读取用户已授权的睡眠、心率或运动记录。用户询问自己的身体数据或趋势、或{char}想了解用户身体状况时调用。",
        "dangerous": False,
        "category": "memory",
        "persist": True,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要查看的数据主题，如“睡眠”“心率”“运动”或“最近”；省略时返回综合摘要。",
                },
            },
            "required": [],
        },
        "trace_args": ["query"],
    }

    registry["read_hds_heart_rate"] = {
        "func": _read_hds_heart_rate_wrapper,
        "description": "只读查询 Apple Watch HDS 当前心率样本、实时新鲜度、近 3 分钟统计，以及上次自动化心率评估和候选；想了解用户当前身体状态或心率变化时可主动调用。样本过期时不得称为实时，不作医疗诊断。",
        "dangerous": False,
        "category": "memory",
        "examples": ["看看我手表现在的心率", "最近心率有什么变化", "上次心率自动化判断出了什么"],
        "keywords": ["手表心率", "实时心率", "HDS", "心率变化", "心率判断"],
        "parameters": {"type": "object", "properties": {}, "required": []},
        "trace_args": [],
        "write_short_term": False,
    }

    registry["search_diary"] = {
        "func": _search_diary_wrapper,
        "description": "按主题检索最近 30 天的日记。需要确认用户曾写过的特定事件、情绪或话题、或{char}想主动回忆时调用；不得把结果当作未检索的记忆猜测。",
        "dangerous": False,
        "category": "memory",
        "persist": True,
        "write_short_term": False,
        "echo_event_log": False,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "用于检索的主题或关键词，例如“失眠”“焦虑”或“考试”；省略时返回最近片段。",
                },
                "date": {
                    "type": "string",
                    "description": "可选的精确日期，例如“2026-09-03”或“9月3日”。",
                },
            },
            "required": [],
        },
        "trace_args": ["query"],
    }

    registry["get_profile"] = {
        "func": _get_profile_wrapper,
        "description": "读取已存储的用户基本资料和重要事实。{char}需要依据这些资料回答、且当前上下文未提供时调用。",
        "dangerous": False,
        "category": "memory",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    }

    registry["get_episodic"] = {
        "func": _get_episodic_wrapper,
        "description": "检索与当前主题相关的情景记忆。{char}需要核对过去具体事件而不是凭印象回答时调用。",
        "dangerous": False,
        "category": "memory",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "用于召回的主题或关键词，例如“失眠”“考试”或“吵架”；可省略。",
                },
            },
            "required": [],
        },
        "trace_args": ["topic"],
    }

    registry["read_long_user_message"] = {
        "func": _read_long_user_message_wrapper,
        "description": "按历史里的长消息序号查用户那条消息的原文。概述不足以回答具体细节时调用；用 offset 继续读取。",
        "dangerous": False, "category": "memory", "echo_event_log": False,
        "examples": ["查长消息序号 3 的原文", "继续读序号 3 从第 1401 字开始的内容"],
        "keywords": ["长消息", "原文", "概述", "序号", "已裁剪"],
        "trace_args": ["sequence", "offset"],
        "parameters": {"type": "object", "properties": {
            "sequence": {"type": "integer", "minimum": 1, "description": "历史中标出的长消息序号"},
            "offset": {"type": "integer", "minimum": 0, "description": "从第几字开始，默认 0；每次最多 1400 字"},
        }, "required": ["sequence"]},
    }

    registry["search_memory_dossiers"] = {
        "func": _search_memory_dossiers_wrapper,
        "description": "按主题检索当前角色维护的记忆档案。需要核对当前理解或找到 dossier_id 时调用；最多返回三本。",
        "dangerous": False, "category": "memory", "echo_event_log": False,
        "examples": ["查一下我们关于饮食的记忆档案", "你现在怎么理解我最近的偏好"],
        "keywords": ["记忆档案", "当前理解", "偏好变化", "主题记忆"],
        "trace_args": ["query"],
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "主题或关键词。"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 3, "description": '本次最多返回的条目数，遵循工具上限。'},
        }, "required": ["query"]},
    }

    registry["read_memory_dossier"] = {
        "func": _read_memory_dossier_wrapper,
        "description": "读取检索结果中的一本记忆档案及当前理解、经历成员和关系；只使用真实返回的 dossier_id。",
        "dangerous": False, "category": "memory", "echo_event_log": False,
        "examples": ["读取刚才找到的记忆档案详情"],
        "keywords": ["档案详情", "读取档案", "当前理解详情"],
        "trace_args": ["dossier_id"],
        "parameters": {"type": "object", "properties": {
            "dossier_id": {"type": "string", "pattern": "^[0-9a-f]{32}$", "description": '当前角色档案的32位十六进制标识，取自查询结果。'},
        }, "required": ["dossier_id"]},
    }

    registry["search_dossier_events"] = {
        "func": _search_dossier_events_wrapper,
        "description": "分页读取一本档案关联的经历与证据引用。需要细查事件链时才调用，单页有硬上限。",
        "dangerous": False, "category": "memory", "echo_event_log": False,
        "examples": ["看看这本档案里具体有哪些经历", "继续读下一页档案事件"],
        "keywords": ["档案事件", "具体经历", "证据引用", "下一页"],
        "trace_args": ["dossier_id", "offset"],
        "parameters": {"type": "object", "properties": {
            "dossier_id": {"type": "string", "pattern": "^[0-9a-f]{32}$", "description": '当前角色档案的32位十六进制标识，取自查询结果。'},
            "offset": {"type": "integer", "minimum": 0, "description": '列表起点偏移，默认0；按返回的 next_offset 继续。'},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": '本次最多返回的条目数，遵循工具上限。'},
        }, "required": ["dossier_id"]},
    }

    registry["update_memory_dossier"] = {
        "func": _update_memory_dossier_wrapper,
        "description": "以受限结构化操作创建或修订当前角色的派生记忆档案。必须携带新的 operation_id 和当前 revision；不能修改原始证据、文件或 SQL。",
        "dangerous": False, "category": "memory", "effect": "write", "echo_event_log": False,
        "examples": ["把我刚才的明确纠正写进相关档案", "撤回你自己的错误概括"],
        "keywords": ["更正记忆档案", "修订理解", "撤回概括", "合并主题"],
        "trace_args": ["operation_id"],
        "parameters": {"type": "object", "properties": {
            "operation_id": {"type": "string", "pattern": "^[0-9a-f]{32}$", "description": '本次修改的唯一请求标识，重试使用相同标识。'},
            "operations": {"type": "array", "minItems": 1, "maxItems": 100,
                           "items": {"type": "object"}, "description": '按顺序应用的有界档案修改操作，字段遵循下方 schema。'},
        }, "required": ["operation_id", "operations"]},
    }

    registry["get_memory_consolidation_status"] = {
        "func": _get_memory_consolidation_status_wrapper,
        "description": "读取当前角色记忆档案与整理积压的元数据状态，不返回正文。",
        "dangerous": False, "category": "memory", "echo_event_log": False,
        "examples": ["记忆整理到哪里了", "还有多少记忆待整理"],
        "keywords": ["记忆整理状态", "整理进度", "记忆积压"],
        "trace_args": [],
        "parameters": {"type": "object", "properties": {}, "required": []},
    }

    registry["request_memory_consolidation"] = {
        "func": _request_memory_consolidation_wrapper,
        "description": "请求耐久的增量或全历史记忆整理，立即返回 task_id，不在聊天请求内扫描或等待。",
        "dangerous": False, "category": "memory", "effect": "write", "echo_event_log": False,
        "examples": ["后台整理一下新记忆", "开始分批整理全部历史记忆"],
        "keywords": ["后台整理记忆", "全历史整理", "分批整理", "记忆任务"],
        "trace_args": ["request_id", "scope_mode"],
        "parameters": {"type": "object", "properties": {
            "request_id": {"type": "string", "pattern": "^[0-9a-f]{32}$", "description": '请求幂等标识，重试同一请求时保持不变。'},
            "scope_mode": {"type": "string", "enum": ["incremental", "full_history"], "description": '整合范围模式，当前角色 recent 为近期、backlog 为历史积压。'},
        }, "required": ["request_id"]},
    }

    registry["search_documents"] = {
        "func": _search_documents_wrapper,
        "description": "检索当前用户与当前角色私有资料库中的已上传文档和图片描述。需要核实用户以前发来的材料时调用。",
        "dangerous": False, "category": "info", "trace_result": False, "echo_event_log": False,
        "examples": ["我之前发给你的那份文件", "找一下那张图片", "查查我上传的资料"],
        "keywords": ["之前发的文件", "上传的资料", "那张图片", "找一下文档"],
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "资料名称、主题或关键词。"},
            "media_type": {"type": "string", "description": "可选的精确媒体类型筛选，例如 text/markdown 或 image/png。"},
        }, "required": ["query"]},
    }

    registry["read_document"] = {
        "func": _read_document_wrapper,
        "description": "按上传引用或 search_documents 返回的 document_id 分段读私有资料。context 按 offset 读取；continue 接续最早未提供正文；overview 查看带位置的目录和进度；summary 查看/补全分块生成的全文概要。每段最多6000字符，按 next_offset 继续直到末尾，不把一段或部分概要当完整阅读，不猜测 id。",
        "dangerous": False, "category": "info", "trace_result": False, "echo_event_log": False,
        "examples": ["读取检索结果里的 doc_...", "继续读这份资料"],
        "keywords": ["读这份资料", "打开文档", "继续读文件"],
        "parameters": {"type": "object", "properties": {
            "document_id": {"type": "string", "description": "上传消息引用或 search_documents 返回的稳定 document_id。"},
            "offset": {"type": "integer", "description": "可选正文偏移量，用于继续读取。", "minimum": 0},
            "mode": {"type": "string", "enum": ["summary", "context", "overview", "continue"], "description": "context 正文；continue 从持久提供进度接续；overview 带字符位置的目录（offset 为目录条目偏移）；summary 分块全文概要，可能 pending/partial，可再次请求。"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 6000, "description": "正文每段字符数，默认6000。"},
            "query": {"type": "string", "description": "在文档内定位文字，从命中位置前后读取；offset 可用于继续查找。"},
        }, "required": ["document_id"]},
    }

    registry["read_document_notes"] = {
        "func": _read_document_notes_wrapper,
        "description": "检索当前用户和角色的持久文档笔记：自动分段概要与角色注释，含 document_id、字符范围和版本。每页3条，按 next_offset 翻页。可跨历史文档按关键词检索；需原文时用 read_document 按笔记位置回看。",
        "dangerous": False, "category": "info", "trace_result": False, "echo_event_log": False,
        "examples": ["看看这份文档的分段概要", "找以前记下的文档笔记"],
        "keywords": ["文档笔记", "分段概要", "阅读笔记"],
        "parameters": {"type": "object", "properties": {
            "document_id": {"type": "string", "description": "可选稳定文档ID，留空检索本作用域所有历史文档。"},
            "query": {"type": "string", "description": "可选文件名或笔记内容关键词。"},
            "offset": {"type": "integer", "minimum": 0, "description": "条目偏移，默认0；按 next_offset 翻页。"},
        }, "required": []},
    }

    registry["write_document_note"] = {
        "func": _write_document_note_wrapper,
        "description": "为上传文档新建、补充或修改角色自己的持久阅读笔记，包括自动概要缺失时自行写概要。先回读相关原文确认位置。自动概要只读，纠正它应新建角色注释。更新必须传 read_document_notes 返回的 note_id 和当前 revision；冲突时重新读后再改。原文不会被笔记修改。",
        "dangerous": False, "category": "info", "trace_result": False, "echo_event_log": False,
        "examples": ["补上这份文档的阅读笔记", "修改你之前写的文档概要"],
        "keywords": ["补文档笔记", "改阅读笔记", "新建文档概要"],
        "parameters": {"type": "object", "properties": {
            "document_id": {"type": "string", "description": "上传引用或检索结果中的稳定文档ID。"},
            "content": {"type": "string", "description": "笔记正文，最多1500字符；过长应分条保存。"},
            "start_offset": {"type": "integer", "minimum": 0, "description": "对应原文起点，包含该字符。"},
            "end_offset": {"type": "integer", "minimum": 1, "description": "对应原文终点，不包含该字符；必须大于起点且不超过全文长度。"},
            "note_id": {"type": "string", "description": "留空新建；修改传角色笔记ID。"},
            "expected_revision": {"type": "integer", "minimum": 0, "description": "新建默认0；修改传读到的当前 revision。"},
            "mode": {"type": "string", "enum": ["append", "replace"], "description": "append 追加，replace 替换（默认）；总正文仍最多1500字符。"},
        }, "required": ["document_id", "content", "start_offset", "end_offset"]},
    }

    registry["search_character_notes"] = {
        "func": _search_character_notes_wrapper,
        "description": "检索当前角色在当前用户对话范围内的日记和手记。用户问角色以前写过什么、是否记下过某事时调用。",
        "dangerous": False, "category": "info", "trace_result": False, "echo_event_log": False,
        "examples": ["你以前写过这个吗", "翻翻你的手记", "你那天记了什么"],
        "keywords": ["你的手记", "你写过", "翻翻笔记", "你那天记了什么"],
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "要核实的主题或关键词。"},
        }, "required": ["query"]},
    }

    registry["search_events"] = {
        "func": _domain_core_tools_event_tools.search_events_wrapper,
        "description": "在当前 reality 事件账本中检索具体证据，为后续窗口或关联读取提供 event_id。只读、范围受限，不从摘要推断事件。",
        "dangerous": False,
        "category": "memory",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "maxLength": 256, "description": "Search text matched against bounded event evidence."},
            "actor": {"type": "string", "maxLength": 64, "description": "Optional actor filter, such as user or assistant."},
            "kind": {"type": "string", "maxLength": 64, "description": "Optional event kind filter."},
            "source": {"type": "string", "maxLength": 128, "description": "Optional source filter."},
            "occurred_after": {"type": ["number", "null"], "description": "Optional exclusive lower Unix timestamp bound."},
            "occurred_before": {"type": ["number", "null"], "description": "Optional exclusive upper Unix timestamp bound."},
            "cursor": {"type": "string", "maxLength": 1024, "description": "Opaque cursor returned by a previous search page."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Maximum number of matching events to return."},
        }, "required": []},
        "examples": ["查找上次关于考试的原始事件", "找最近的 desktop owner_chat 事件"],
        "keywords": ["查找事件", "原始事件", "事件证据", "event_id"],
        "trace_args": ["actor", "kind", "source"],
    }

    registry["expand_event_window"] = {
        "func": _domain_core_tools_event_tools.expand_event_window_wrapper,
        "description": "读取一个已知 event_id 的确定性时间窗口，返回事件正文和前后事件。只读，最多各20条。",
        "dangerous": False,
        "category": "memory",
        "parameters": {"type": "object", "properties": {
            "event_id": {"type": "string", "minLength": 1, "maxLength": 256, "description": "Known event ID whose bounded context should be read."},
            "before": {"type": "integer", "minimum": 0, "maximum": 20, "description": "Number of preceding events to include."},
            "after": {"type": "integer", "minimum": 0, "maximum": 20, "description": "Number of following events to include."},
        }, "required": ["event_id"]},
        "examples": ["展开这个事件前后发生了什么", "查看该 event_id 的上下文"],
        "keywords": ["展开事件", "前后上下文", "事件窗口"],
        "trace_args": ["event_id"],
    }

    registry["get_related_events"] = {
        "func": _domain_core_tools_event_tools.get_related_events_wrapper,
        "description": "读取已知 event_id 的确定性关联事件边。只读、单跳、最多20条，可按 relation_types 过滤。",
        "dangerous": False,
        "category": "memory",
        "parameters": {"type": "object", "properties": {
            "event_id": {"type": "string", "minLength": 1, "maxLength": 256, "description": "Known event ID whose deterministic relations should be read."},
            "relation_types": {"type": "array", "items": {"type": "string", "maxLength": 64}, "maxItems": 20, "description": "Optional relation-type allowlist."},
            "cursor": {"type": "string", "maxLength": 1024, "description": "Opaque cursor returned by a previous relation page."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Maximum number of related events to return."},
            "depth": {"type": "integer", "minimum": 1, "maximum": 1, "description": "Relation traversal depth; only one hop is supported."},
        }, "required": ["event_id"]},
        "examples": ["查看这个事件关联的同轮事件", "找该事件的确定性关联"],
        "keywords": ["关联事件", "同轮事件", "事件关系"],
        "trace_args": ["event_id", "relation_types"],
    }

    registry["revise_memory"] = {
        "func": _revise_memory_wrapper,
        "description": "Correct a specific episodic memory after the user explicitly says it is wrong. Use only with a known episode id; it preserves history by weakening the old entry and recording the correction.",
        "dangerous": False,
        "category": "memory",
        "parameters": {"type": "object", "properties": {
            "episode_id": {"type": "string", "description": "The exact episodic-memory identifier to correct."},
            "correction": {"type": "string", "description": "The concise replacement fact explicitly supplied or confirmed by the user."},
        }, "required": ["episode_id", "correction"]},
        "trace_args": ["episode_id"],
    }

    registry["forget_episodic"] = {
        "func": _forget_episodic_wrapper,
        "description": "Forget an episodic memory only when the user explicitly asks to forget it. Provide either a known episode id or a specific topic. This downgrades the entry for audit/archive; it does not physically erase history.",
        "dangerous": False,
        "category": "memory",
        # Keep both fields optional in JSON Schema. Top-level anyOf/required
        # alternatives are rejected by some Gemini OpenAI-compat relays; the
        # wrapper still requires at least one of episode_id / topic.
        "parameters": {"type": "object", "properties": {
            "episode_id": {"type": "string", "description": "The exact episodic-memory identifier to forget, if known. Provide this or topic."},
            "topic": {"type": "string", "description": "A specific topic to forget when no episode id is available. Provide this or episode_id."},
        }},
        "examples": ["忘掉那次失眠的记忆", "别再记得我们关于考试的那段事"],
        "keywords": ["忘掉记忆", "别再记得", "遗忘这段"],
        "trace_args": ["episode_id", "topic"],
    }

    registry["clear_midterm"] = {
        "func": _clear_midterm_wrapper,
        "description": "Clear the current user's short-lived mid-term memory bucket only when the user explicitly asks to clear recent memory. This does not alter episodic memory or the stable user profile.",
        "dangerous": False,
        "category": "memory",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "examples": ["清空刚才这些近期记忆", "把最近的临时记忆忘掉"],
        "keywords": ["清空近期记忆", "忘掉临时记忆"],
        "trace_args": [],
    }

    registry["revise_user_profile"] = {
        "func": _revise_user_profile_wrapper,
        "description": "Correct one stable user-profile dimension after the user explicitly says the stored profile is wrong. Do not infer a replacement; use only the user's stated correction.",
        "dangerous": False,
        "category": "memory",
        "parameters": {"type": "object", "properties": {
            "field": {"type": "string", "enum": ["trust_pattern", "emotion_expression", "help_seeking", "stress_response", "intimacy_comfort", "sleep_pattern", "topic_preference", "self_relation", "address_style"], "description": "The stable profile dimension to replace."},
            "correction": {"type": "string", "description": "The concise corrected user pattern explicitly given by the user."},
        }, "required": ["field", "correction"]},
        "trace_args": ["field"],
    }

    registry["exit_yandere"] = {
        "func": _exit_yandere_wrapper,
        "description": "结束{char}当前的 yandere 状态并恢复平静。仅在该状态确实活跃且对话已自然缓和时调用。",
        "dangerous": False,
        "category": "system",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    }


INVOCATION_ADAPTERS = {
    **dict.fromkeys(('read_document_notes', 'write_document_note'), _invoke_read_document_notes),
    **dict.fromkeys(('read_life_records', 'read_food_preferences', 'read_message_context', 'pin_tool_result', 'unpin_tool_result', 'list_tool_result_pins', 'save_quote', 'search_quotes', 'read_quote', 'write_quote_note', 'reread_image'), _invoke_read_life_records),
    **dict.fromkeys(('get_profile', 'get_episodic', 'read_long_user_message', 'search_memory_dossiers', 'read_memory_dossier', 'search_dossier_events', 'get_memory_consolidation_status', 'search_documents', 'read_document', 'search_character_notes', 'search_events', 'expand_event_window', 'get_related_events'), _invoke_get_profile),
    **dict.fromkeys(('update_memory_dossier', 'request_memory_consolidation'), _invoke_update_memory_dossier),
    **dict.fromkeys(('backfill_diary',), _invoke_backfill_diary),
    **dict.fromkeys(('read_diary', 'search_diary'), _invoke_read_diary),
    **dict.fromkeys(('get_listening_state', 'get_listening_queue', 'get_listening_history', 'get_track_note', 'write_track_note', 'choose_next_track', 'get_ambient_state'), _invoke_get_listening_state),
    **dict.fromkeys(('pin_character_image_reference',), _invoke_pin_character_image_reference),
    **dict.fromkeys(('read_hds_heart_rate',), _invoke_read_hds_heart_rate),
    **dict.fromkeys(('read_watch',), _invoke_read_watch),
    **dict.fromkeys(('revise_memory', 'forget_episodic', 'clear_midterm', 'revise_user_profile', 'phone_control_start'), _invoke_revise_memory),
}


def register_invokers(registry):
    for name, adapter in INVOCATION_ADAPTERS.items():
        registry[name]["invoke"] = adapter
