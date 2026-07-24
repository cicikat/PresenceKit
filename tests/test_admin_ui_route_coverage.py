"""Brief 116 §1：后端写接口 与 管理面板 UI 引用的一致性扫描。

`test_admin_mcp_ui.py` 手工列一串字符串断言只覆盖了 MCP 一个模块，但同类
"后端概念存在，UI 没对上" 的问题在那轮实际炸出过两次（`vision-save-msg`
引用不存在的 DOM 元素、`vision.enabled`/`visual_perception.enabled` 两个
开关撞脸）。这个测试把该模式推广成通用扫描：`admin/admin_server.py` 里每个
`include_router` 的前缀 + `admin/routers/*.py` 里每个
`@router.post`/`@router.patch`/`@router.put` 的路径，拼成完整路由后必须能在
`admin/static/index.html` 里找到引用（字符串字面量或 `` `${var}` `` 模板字面量），
否则要么把 UI 接上，要么显式加进 `NO_ADMIN_UI_WHITELIST` 并写清楚理由——逼着
"新增写接口忘记接 UI" 在 CI 就炸，而不是等用户点开面板发现少个开关。

白名单只应收录"设计上就不该有管理面板 UI"的接口：桌宠客户端
（Emerald-client）/ 手机端（Emerald-mobile）直连的持久化通道、活动类小游戏、
纯内部/测试用接口等。管理面板本该覆盖但目前没做的功能缺口，请补 UI 而不是
加白名单掩盖。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_FILE = ROOT / "admin/admin_server.py"
ROUTERS_DIR = ROOT / "admin/routers"
INDEX_FILE = ROOT / "admin/static/index.html"

_INCLUDE_RE = re.compile(r'app\.include_router\(\s*(\w+)\.router\s*,\s*prefix\s*=\s*"([^"]*)"')
_ROUTE_RE = re.compile(r'@router\.(post|patch|put)\(\s*"([^"]+)"')

# (METHOD, 完整路径) -> 白名单理由（一行，说明为什么管理面板不需要接这个接口）。
# 新增写接口默认要求要么出现在 index.html 里，要么显式加进这里。
NO_ADMIN_UI_WHITELIST: dict[tuple[str, str], str] = {
    # ── 桌宠客户端（Emerald-client）专属通道 ────────────────────────────
    ("POST", "/desktop/chat"): "桌宠对话入口，由 Emerald-client 直连，非管理面板功能",
    ("POST", "/desktop/activate"): "桌宠上线激活 desktop 通道，由 Emerald-client 直连",
    ("POST", "/desktop/wake"): "桌宠重开问候，由 Emerald-client 直连",
    ("POST", "/upload/ingest"): "三端统一文件上传入口，管理面板不做上传 UI",
    ("POST", "/hardware/connect"): "硬件配对，Emerald-client shared/api/hardware.ts 消费",
    ("POST", "/transcribe"): "语音转写，Emerald-client shared/voice/useVoiceInput.ts 消费",
    ("POST", "/garden/water"): "陪伴花园浇水，Emerald-client water_garden Tauri invoke 消费",
    ("POST", "/coplay/arm"): "陪玩模式开关，Emerald-client CoplaySettingsPage 消费",
    ("POST", "/coplay/disarm"): "陪玩模式开关，Emerald-client CoplaySettingsPage 消费",

    # ── 梦境正式对话流程（区别于管理面板做的"世界/剧本编辑"与"梦境状态观测"）
    ("POST", "/dream/enter"): "梦境对话流程由桌宠客户端驱动，管理面板只做世界/剧本编辑与状态观测",
    ("POST", "/dream/chat"): "同上，梦境对话由 Emerald-client 驱动",
    ("POST", "/dream/exit"): "同上，梦境硬退出由 Emerald-client 驱动",
    ("POST", "/dream/wake"): "同上，梦境软挽留闸门由 Emerald-client 驱动",
    ("POST", "/dream/resume"): "同上，挽留后留下由 Emerald-client 驱动",
    ("PATCH", "/dream/settings"): "persona 级设置，供桌面客户端使用（docs/feature-control-surface.md §1）",

    # ── persona 级设置：docs/feature-control-surface.md 明确"供桌面客户端使用"，
    #    管理面板不重复做界面 ──────────────────────────────────────────
    ("PATCH", "/character/{char_id}/model-routing"): "角色卡模型路由绑定，persona scope，桌宠客户端消费",
    ("PUT", "/settings/model-routing"): "桌面端切换已有模型路由，persona 级设置（docs/feature-control-surface.md §1）",
    ("PUT", "/chat-mode"): "聊天模式切换，persona 级桌面客户端设置，不在管理面板",
    ("PUT", "/chat-style"): "对话风格切换，persona 级桌面客户端设置，不在管理面板",
    ("PUT", "/chat-multi-message"): "分条发送开关，persona 级桌面客户端设置，不在管理面板",
    ("POST", "/settings/tts-desktop"): "桌面语音播放开关，persona 级设置（docs/feature-control-surface.md §1）",
    ("POST", "/tts/synthesize"): "按需合成语音，桌面端 {text,emotion}->{audio_b64,mime} 契约，非管理面板功能",
    ("POST", "/settings/thinking"): "persona 级设置，供桌面客户端使用（docs/feature-control-surface.md §1）",
    ("POST", "/settings/tool-loop"): "persona 级设置，供桌面客户端使用（docs/feature-control-surface.md §1）",
    ("POST", "/settings/characters/{char_id}/avatar"): "角色头像 runtime override 上传，persona scope，桌宠客户端消费",

    # ── 手机端（Emerald-mobile）专属通道 ────────────────────────────────
    ("POST", "/mobile/activate"): "手机端上线激活，Emerald-mobile backend_client.dart 消费",
    ("POST", "/mobile/deactivate"): "手机端下线停用，Emerald-mobile backend_client.dart 消费",
    ("POST", "/mobile/ack"): "手机端消息确认，Emerald-mobile backend_client.dart 消费",
    ("POST", "/mobile/push"): "向手机端推送消息，Emerald-mobile backend_client.dart 消费",
    ("POST", "/sensor/push"): "手机传感器数据上报，Emerald-mobile 消费",
    ("POST", "/sensor/realtime"): "桌面端实时传感器快照，Emerald-client sensor 消费",
    ("PATCH", "/settings/prompt-assets"): "Prompt 资产部分更新，已确认由 Emerald-mobile PATCH 调用",
    ("PATCH", "/system/meta-mode"): "安全/危险模式切换，已确认由 Emerald-mobile 调用",

    # ── 群聊/群梦：面向 Emerald-mobile，管理面板未做对应界面 ────────────────
    ("POST", "/group/create"): "建群，Emerald-mobile backend_client.dart 消费",
    ("POST", "/group/{group_id}/send"): "触发 arbiter 一轮，Emerald-mobile backend_client.dart 消费",
    ("PATCH", "/group/{group_id}/roster"): "改群成员，Emerald-mobile backend_client.dart 消费",
    ("PATCH", "/group/{group_id}/settings"): "改群设置，Emerald-mobile backend_client.dart 消费",
    ("POST", "/group/{group_id}/dream/enter"): "群聊入梦，Emerald-mobile backend_client.dart 消费",
    ("POST", "/group/{group_id}/dream/send"): "群聊梦境发言，Emerald-mobile backend_client.dart 消费",
    ("POST", "/group/{group_id}/dream/exit"): "群聊梦境硬退出，Emerald-mobile backend_client.dart 消费",
    ("PATCH", "/group/{group_id}/dream/settings"): "改群聊梦境设置，Emerald-mobile 消费（暂无对应 UI）",

    # ── 活动类小游戏/阅读：桌宠客户端自带活动窗口 UI，非管理面板 ────────────
    ("POST", "/activity/chess/start"): "国际象棋活动，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/chess/move"): "国际象棋活动，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/chess/close"): "国际象棋活动，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/chess/ai_move"): "国际象棋活动，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/chess/chat"): "国际象棋活动内对话，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/chess/comment"): "国际象棋活动主动评论，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/gomoku/start"): "五子棋活动，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/gomoku/move"): "五子棋活动，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/gomoku/close"): "五子棋活动，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/gomoku/ai_move"): "五子棋活动，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/gomoku/chat"): "五子棋活动内对话，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/gomoku/comment"): "五子棋活动主动评论，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/reading/start"): "阅读活动，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/reading/turn_page"): "阅读活动翻页，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/reading/close"): "阅读活动，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/reading/library/add"): "阅读书库管理，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/reading/start_from_library"): "阅读活动，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/reading/library/delete"): "阅读书库管理，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/reading/library/rename"): "阅读书库管理，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/reading/library/categorize"): "阅读书库管理，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/reading/chat"): "阅读活动内对话，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/dream_seed/start"): "梦境预构活动，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/dream_seed/chat"): "梦境预构活动内对话，UI 在 Emerald-client 桌宠活动窗口",
    ("POST", "/activity/dream_seed/close"): "梦境预构活动，UI 在 Emerald-client 桌宠活动窗口",

    # ── 暂无已知调用方的细粒度写接口（管理面板走批量 PUT /profile 覆盖达到同等效果）
    ("PUT", "/memory/{user_id}/profile/important-facts/{index}"): (
        "细粒度覆盖单条 important_fact 的 API；管理面板经 PUT /users/{uid}/profile "
        "批量覆盖整个数组达成同等效果，暂无客户端单独调用此接口；如确认无用可另开工单清理"
    ),
    ("PUT", "/memory/{user_id}/identity/{key}"): (
        "细粒度覆盖单个 user_identity 维度的 API，暂无客户端单独调用；如确认无用可另开工单清理"
    ),
}


def _prefix_map() -> dict[str, str]:
    source = SERVER_FILE.read_text(encoding="utf-8")
    return {mod: prefix for mod, prefix in _INCLUDE_RE.findall(source)}


def _iter_routes():
    """Yield (method, full_path, module, lineno) for every write route that's mounted in admin_server.py."""
    prefixes = _prefix_map()
    for path in sorted(ROUTERS_DIR.glob("*.py")):
        module = path.stem
        prefix = prefixes.get(module)
        if prefix is None:
            continue  # 没被 include_router 挂载（比如可选依赖缺失时的 chess），不算真实路由
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = _ROUTE_RE.search(line)
            if not m:
                continue
            method, route_path = m.group(1).upper(), m.group(2)
            full_path = prefix.rstrip("/") + route_path if prefix else route_path
            yield method, full_path, module, lineno


def _path_pattern(full_path: str) -> re.Pattern:
    """把 `{param}` 段替换成通配符——UI 里路径参数通常写成模板字面量 `${var}`。"""
    segments = full_path.split("/")
    parts = [
        r"[^/'\"`]+" if seg.startswith("{") and seg.endswith("}") else re.escape(seg)
        for seg in segments
    ]
    return re.compile("/".join(parts))


def test_no_admin_ui_whitelist_entries_are_stale():
    """白名单里的每一条都必须对应一个真实存在的路由，否则说明接口已改名/删除，白名单该跟着清理。"""
    real_routes = {(method, full_path) for method, full_path, _, _ in _iter_routes()}
    stale = sorted(set(NO_ADMIN_UI_WHITELIST) - real_routes)
    assert not stale, (
        "NO_ADMIN_UI_WHITELIST 里有找不到对应真实路由的过期条目（接口可能已改名/删除），"
        f"请清理：{stale}"
    )


def test_write_routes_are_referenced_in_admin_ui_or_whitelisted():
    """admin/routers/*.py 里的每个写接口，要么被 index.html 引用，要么在白名单里写明理由。"""
    index_source = INDEX_FILE.read_text(encoding="utf-8")
    unexplained = []
    for method, full_path, module, lineno in _iter_routes():
        key = (method, full_path)
        if key in NO_ADMIN_UI_WHITELIST:
            continue
        if _path_pattern(full_path).search(index_source):
            continue
        unexplained.append(f"{method} {full_path}  ({module}.py:{lineno})")

    assert not unexplained, (
        "以下写接口在 admin/static/index.html 里找不到引用，也没有加入白名单："
        "\n  " + "\n  ".join(unexplained) +
        "\n\n要么把 UI 接上，要么在 NO_ADMIN_UI_WHITELIST 里显式加一条并写明理由。"
    )
