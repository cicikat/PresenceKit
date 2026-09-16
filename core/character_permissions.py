"""
core/character_permissions.py — "角色权限" 观测面板后端（2026-07-25，茶茶反馈）

用户原话："要不在观测里面再加一栏'角色权限'，里面写有关角色目前拥有哪些具体权限
（桌面+手机），哪些开启，哪些差什么条件，并且加个测试按钮，测试这条链路到底通没通。"

三件事：
1. 工具类目暴露面（探针 Path A / tool loop Path C 分别暴露哪些类目，是否被
   presence_ext.tool_categories 覆盖）。
2. 桌面/手机类目的"危险模式"闸门状态——desktop/system/phone_control 三个类目
   即便暴露了 schema，真正执行时也会在 core/tool_dispatcher.py::_mode_gate() 被拦
   （安全模式一律拒绝，只有危险模式窗口内放行，2 小时自动过期）。这是用户反馈里
   "哪些差什么条件"最常见的答案，值得直接摆出来而不是让用户去猜。
3. 身份固化管线（角色改自己的记忆文件那条线）状态 + 一个真正执行一次
   consolidate_to_identity() 的测试入口——这条链路是后台任务，没有交互反馈，
   用户自己完全看不出有没有在跑。

test 按钮的设计取舍：
- identity_consolidation：真实执行 consolidate_to_identity()。函数本身在"没有可固化
  的 episode"时是安全的空操作（只记一条日志，不写文件），所以随时点都不会有副作用，
  点了就能看到"通不通"的真实结果。
- fs 类目：真实执行 fs_list 列一个安全根目录，同样是安全的只读操作。
- desktop / system / phone_control 类目：不会真的执行——这些工具都有真实副作用
  （弹通知、震动玩具、关机等），一个观测面板的"测试"按钮不该意外触发它们。改为返回
  一份就绪检查清单（类目是否暴露、危险模式是否开着、config 里对应工具开关是否打开），
  清单本身已经能回答"通不通"，且在结果里明确标注"未实际执行"，不让用户误以为已经点过了。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_ALL_CATEGORIES = ("info", "desktop", "memory", "system", "fs", "phone_control", "mcp", "artifacts")

# 与 core/tool_dispatcher.py 保持一致（不 import 私有名以外的逻辑，这里只读只报告）。
_MODE_RESTRICTED_CATEGORIES = frozenset({"desktop", "system", "phone_control"})

def _load_character(char_id: str):
    from core.character_loader import load
    return load(char_id)


def get_tool_category_status(char_id: str) -> dict:
    """按类目汇总：是否对该角色暴露（探针/tool loop 分别判断）、是否受危险模式闸门约束、
    当前危险模式状态，以及该类目下每个工具的 config 开关状态。"""
    from core.config_loader import get_config
    from core.tool_dispatcher import _TOOL_REGISTRY, _current_mode

    cfg = get_config()
    try:
        char = _load_character(char_id)
        presence_ext = char.presence_ext or {}
        char_name = char.name
    except Exception as e:
        return {"error": f"角色加载失败: {e}"}

    from core.tool_exposure import resolve as resolve_exposure
    path_a = resolve_exposure("path_a", char_id=char_id)
    path_c = resolve_exposure("path_c", char_id=char_id)
    loop_enabled = bool((cfg.get("tool_loop") or {}).get("enabled", False))

    current_mode = _current_mode()

    tools_by_category: dict[str, list] = {c: [] for c in _ALL_CATEGORIES}
    for name, spec in _TOOL_REGISTRY.items():
        cat = spec.get("category", "info")
        tools_by_category.setdefault(cat, [])
        tool_cfg = cfg.get("tools", {}).get(name, {})
        tools_by_category[cat].append({
            "name": name,
            "dangerous": bool(spec.get("dangerous", False)),
            "config_enabled": tool_cfg.get("enabled", True),
            "excluded_by_path_a": name in path_a.exclude_tools or (path_a.tools is not None and name not in path_a.tools),
            "excluded_by_path_c": name in path_c.exclude_tools or (path_c.tools is not None and name not in path_c.tools),
            # Compatibility field consumed by the existing panel.
            "excluded_by_char": name in path_c.exclude_tools or (path_c.tools is not None and name not in path_c.tools),
        })

    categories = []
    for cat in _ALL_CATEGORIES:
        exposed_probe = cat in path_a.categories
        exposed_loop = loop_enabled and cat in path_c.categories
        mode_restricted = cat in _MODE_RESTRICTED_CATEGORIES
        mode_blocks_now = mode_restricted and current_mode != "danger"
        categories.append({
            "category": cat,
            "exposed_to_probe": exposed_probe,
            "exposed_to_tool_loop": exposed_loop,
            "mode_restricted": mode_restricted,
            "currently_blocked_by_mode": mode_blocks_now,
            "tools": tools_by_category.get(cat, []),
        })

    return {
        "char_id": char_id,
        "char_name": char_name,
        "tool_loop_enabled": loop_enabled,
        "tool_categories_source": path_c.source,
        "path_exposure": {
            "path_a": {
                "categories": list(path_a.categories),
                "tools": sorted(path_a.tools) if path_a.tools is not None else None,
                "exclude_tools": sorted(path_a.exclude_tools),
                "source": path_a.source,
            },
            "path_c": {
                "categories": list(path_c.categories),
                "tools": sorted(path_c.tools) if path_c.tools is not None else None,
                "exclude_tools": sorted(path_c.exclude_tools),
                "source": path_c.source,
            },
        },
        "current_mode": current_mode,
        "categories": categories,
    }


def get_identity_consolidation_status(uid: str, char_id: str) -> dict:
    """身份固化管线（角色自己改自己的记忆文件）状态快照：配置阈值、上次运行时间、
    identity.yaml 文件本身的存在与修改时间。全 fail-soft，任何一步失败都不抛异常。"""
    result: dict = {"uid": uid, "char_id": char_id}
    try:
        from core.memory.fixation_pipeline import _load_fixation_state, _should_consolidate
        state = _load_fixation_state(uid, char_id=char_id)
        result["fixation_state"] = state
        result["would_consolidate_now"] = _should_consolidate(state)
    except Exception as e:
        result["fixation_state_error"] = str(e)

    try:
        from core.memory.scope import MemoryScope
        from core.memory.path_resolver import resolve_path
        scope = MemoryScope.reality_scope(uid, char_id)
        identity_path = resolve_path(scope, "identity")
        result["identity_file_exists"] = identity_path.exists()
        if identity_path.exists():
            import time as _t
            stat = identity_path.stat()
            result["identity_file_mtime"] = stat.st_mtime
            result["identity_file_mtime_human"] = _t.strftime(
                "%Y-%m-%d %H:%M:%S", _t.localtime(stat.st_mtime)
            )
    except Exception as e:
        result["identity_file_error"] = str(e)

    return result


async def run_permission_test(link: str, *, uid: str, char_id: str) -> dict:
    """执行一次权限链路测试。见模块docstring关于哪些链路真实执行、哪些只做就绪检查。"""
    if link == "identity_consolidation":
        return await _test_identity_consolidation(uid, char_id)
    if link == "fs":
        return await _test_fs_list(uid, char_id)
    if link in _ALL_CATEGORIES:
        return _readiness_check(link, char_id)
    return {"link": link, "ok": False, "detail": f"未知测试项: {link}"}


async def _test_identity_consolidation(uid: str, char_id: str) -> dict:
    from core import llm_client
    from core.memory.fixation_pipeline import consolidate_to_identity

    before = get_identity_consolidation_status(uid, char_id)
    try:
        changed = await consolidate_to_identity(uid, llm_client, char_id=char_id)
        after = get_identity_consolidation_status(uid, char_id)
        return {
            "link": "identity_consolidation",
            "executed": True,
            "ok": True,
            "identity_changed": bool(changed),
            "detail": (
                "链路可达：consolidate_to_identity() 正常执行完毕"
                + ("，且确有维度被更新写入 identity.yaml。" if changed else
                   "，本次没有可固化的新 episode（这是正常情况，不代表链路坏了）。")
            ),
            "before": before,
            "after": after,
        }
    except Exception as e:
        logger.warning("[character_permissions] identity_consolidation 测试失败 uid=%s char_id=%s: %s", uid, char_id, e)
        return {
            "link": "identity_consolidation",
            "executed": True,
            "ok": False,
            "detail": f"链路执行报错，这就是用户反馈里\"看不出通不通\"的真实原因：{e}",
            "before": before,
        }


async def _test_fs_list(uid: str, char_id: str) -> dict:
    from core.tool_dispatcher import execute

    class _FakeState:
        status = "idle"
        WAITING_CONFIRM = "waiting_confirm"

    try:
        # 不传 path：按 fs_list 自身文档，省略时返回 fs_access.allow_roots 允许浏览的
        # 入口目录列表——这是唯一不依赖具体 allow_roots 配置、随时能跑通的安全调用形态。
        result, ask_confirm = await execute(
            tool_name="fs_list",
            tool_args={},
            user_id=uid,
            target_id=uid,
            is_group=False,
            session_state=_FakeState(),
            origin="user_live",
            char_id=char_id,
        )
        ok = result is not None and ask_confirm is None
        return {
            "link": "fs",
            "executed": True,
            "ok": ok,
            "detail": "fs_list 真实执行结果（只读，未写入任何文件）" if ok else f"执行未成功: {result}",
            "result_preview": (result or "")[:300],
        }
    except Exception as e:
        return {"link": "fs", "executed": True, "ok": False, "detail": f"执行报错: {e}"}


def _readiness_check(category: str, char_id: str) -> dict:
    status = get_tool_category_status(char_id)
    if "error" in status:
        return {"link": category, "executed": False, "ok": False, "detail": status["error"]}

    cat_entry = next((c for c in status["categories"] if c["category"] == category), None)
    if cat_entry is None:
        return {"link": category, "executed": False, "ok": False, "detail": "未知类目"}

    checklist = [
        {"item": "对该角色暴露(探针 Path A)", "pass": cat_entry["exposed_to_probe"]},
        {"item": "对该角色暴露(tool loop Path C)", "pass": cat_entry["exposed_to_tool_loop"]},
    ]
    if cat_entry["mode_restricted"]:
        checklist.append({
            "item": "危险模式窗口已开启（否则一律安全模式拒绝，见 _mode_gate）",
            "pass": not cat_entry["currently_blocked_by_mode"],
        })
    enabled_tools = [t for t in cat_entry["tools"] if t["config_enabled"] and not t["excluded_by_char"]]
    checklist.append({
        "item": f"该类目下至少一个工具在 config 中启用且未被角色排除（当前 {len(enabled_tools)}/{len(cat_entry['tools'])}）",
        "pass": len(enabled_tools) > 0,
    })

    all_pass = all(c["pass"] for c in checklist)
    return {
        "link": category,
        "executed": False,
        "ok": all_pass,
        "detail": (
            "未实际执行（该类目工具有真实副作用，如弹通知/震动/关机，观测面板不会代你触发）；"
            "以下是就绪检查清单，全部通过代表链路应当能走通。"
        ),
        "checklist": checklist,
    }
