"""
core/api_contract_check.py — 后端→前端 desktop action 契约测试（2026-07-25，茶茶反馈）

背景：工具领域模块里的 desktop 类 _TOOL_REGISTRY wrapper 会通过
_push_desktop_action({"type": ..., ...})
把动作推给桌面客户端；前端 Emerald-client 的 src/shared/api/ws.ts::_dispatchAction 用一个
switch(type) 消费，不认识的 type 会 throw "unsupported action type"。两边分属两个仓库，
字符串字面量不共享 schema，历史上确实发生过漂移。这个模块把"扫两边源码找漂移"变成一个可重复运行的检查，
而不是每次都要人工重新翻两个仓库的代码。

不是真正的 JSON Schema 契约测试（协议本来就是扁平 {type, ...params}，没有版本化 schema），
但足以在下一次漂移发生时第一时间自动报出来。

前端仓库是否存在（sibling 目录，见 docs/dev-environment.md「与本仓同级目录」的约定）是
可选依赖：不存在时优雅跳过（frontend_available=False），不让没有前端 checkout 的开发者/
CI 挂掉。

供 GET /observability/api-contract-check 使用，见 admin/routers/observability.py。
与 core/resource_completeness.py（item 9）是两块独立面板，都挂在观测(observability) 分区
下，互不依赖。
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_TYPE_KEY_RE = re.compile(r'"type"\s*:\s*"([a-zA-Z0-9_]+)"')
_FRONTEND_CASE_RE = re.compile(r"case\s*'([a-zA-Z0-9_]+)'\s*:")

# 前端认识、但产出路径不在本模块扫描范围内的 type：不算契约漂移，只是生产者不在
# 工具领域定义/pipeline.py 里，说明写在这防止被误判为"后端漏产出"。
_FRONTEND_ONLY_EXPECTED: frozenset[str] = frozenset({
    # 由 core/scheduler/triggers/presence_nag.py 提案 → core/scheduler/loop.py 解析后
    # 推送，走 scheduler 专属链路而非 tool_dispatcher/pipeline 里的 _push_desktop_action
    # 直接调用点，本模块的静态扫描覆盖不到。
    "presence_nag",
})


def _find_frontend_repo() -> Path | None:
    """定位 Emerald-client 仓库。优先环境变量 EMERALD_CLIENT_REPO，否则按
    docs/dev-environment.md 的约定尝试同级目录 ../Emerald-client。"""
    env_path = os.environ.get("EMERALD_CLIENT_REPO")
    if env_path:
        p = Path(env_path)
        if (p / "src" / "shared" / "api" / "ws.ts").exists():
            return p
        logger.warning("[api_contract_check] EMERALD_CLIENT_REPO=%s 下未找到 ws.ts", env_path)

    backend_root = Path(__file__).resolve().parents[1]
    candidates = [
        backend_root.parent / "Emerald-client",
        backend_root.parent / "emerald-client",
    ]
    for c in candidates:
        if (c / "src" / "shared" / "api" / "ws.ts").exists():
            return c
    return None


def _scan_push_desktop_action_types(py_source: str) -> dict[str, list[int]]:
    """扫描一段后端源码里所有 `_push_desktop_action({"type": "xxx", ...})` 静态字面量调用，
    返回 {type: [出现行号, ...]}。"""
    hits: dict[str, list[int]] = {}
    for m in re.finditer(r"_push_desktop_action\(", py_source):
        window = py_source[m.end(): m.end() + 400]
        type_m = _TYPE_KEY_RE.search(window)
        if not type_m:
            continue
        line_no = py_source[: m.start()].count("\n") + 1
        hits.setdefault(type_m.group(1), []).append(line_no)
    return hits


def _backend_producible_types() -> dict[str, list[str]]:
    """汇总静态 _push_desktop_action 调用点的所有可产出 type。"""
    backend_root = Path(__file__).resolve().parent
    sources: dict[str, list[str]] = {}

    # AR-01 moved the actual producers into domain-owned definitions. Keep the
    # facade/pipeline compatibility sources and scan all current domain owners.
    py_files = (backend_root / "tool_dispatcher.py", backend_root / "pipeline.py",
                *sorted((backend_root / "tools").glob("*_definitions.py")))
    for py_file in py_files:
        try:
            text = py_file.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("[api_contract_check] 读取 %s 失败: %s", py_file, e)
            continue
        for t, lines in _scan_push_desktop_action_types(text).items():
            for ln in lines:
                sources.setdefault(t, []).append(f"{py_file.name}:{ln}")

    return sources


def _frontend_recognized_types(repo_root: Path) -> set[str]:
    """只扫 `_dispatchAction` 方法内 `switch (type)` 的 case 分支——ws.ts 里还有别的
    switch（顶层 WS 信封消息类型：ping/hello_ack/message_stream_* 等，与桌面 action 是
    完全不同的协议层），整файл 扫会把两层混在一起，产生大量误报的"frontend_only"。
    用括号计数取出 switch 块的边界，比正则匹配嵌套结构更可靠。"""
    ws_ts = repo_root / "src" / "shared" / "api" / "ws.ts"
    text = ws_ts.read_text(encoding="utf-8")

    anchor = text.find("_dispatchAction(")
    if anchor == -1:
        raise ValueError("ws.ts 中未找到 _dispatchAction，前端契约扫描失败（可能已重构，需更新本模块定位方式）")
    switch_kw = text.find("switch (type)", anchor)
    if switch_kw == -1:
        raise ValueError("_dispatchAction 内未找到 switch (type)，前端契约扫描失败")
    brace_start = text.find("{", switch_kw)
    if brace_start == -1:
        raise ValueError("switch (type) 后未找到 {，前端契约扫描失败")

    depth = 0
    i = brace_start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    else:
        raise ValueError("switch (type) 块未闭合，前端契约扫描失败")

    switch_body = text[brace_start:i + 1]
    return set(_FRONTEND_CASE_RE.findall(switch_body)) - {"default"}


def run_check() -> dict:
    backend_types = _backend_producible_types()
    frontend_repo = _find_frontend_repo()

    if frontend_repo is None:
        return {
            "frontend_available": False,
            "detail": "未找到 Emerald-client 仓库（约定同级目录，或设 EMERALD_CLIENT_REPO 环境变量），跳过对比",
            "backend_producible": {t: srcs for t, srcs in sorted(backend_types.items())},
            "status": "frontend_unavailable",
        }

    try:
        frontend_types = _frontend_recognized_types(frontend_repo)
    except Exception as e:
        logger.warning("[api_contract_check] 解析前端 ws.ts 失败: %s", e)
        return {
            "frontend_available": False,
            "detail": f"找到仓库但解析 ws.ts 失败: {e}",
            "backend_producible": {t: srcs for t, srcs in sorted(backend_types.items())},
            "status": "frontend_unavailable",
        }

    broken = sorted(set(backend_types) - frontend_types)
    frontend_only = sorted(frontend_types - set(backend_types) - _FRONTEND_ONLY_EXPECTED)

    return {
        "frontend_available": True,
        "frontend_repo_path": str(frontend_repo),
        "backend_producible": {t: srcs for t, srcs in sorted(backend_types.items())},
        "frontend_recognized": sorted(frontend_types),
        "broken": broken,
        "broken_detail": {t: backend_types[t] for t in broken},
        "frontend_only": frontend_only,
        "status": "drift_detected" if broken else "ok",
    }
