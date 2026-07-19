"""
P1-0H: 运行期 yexuan fallback 审计测试

确认生产路径中：
  * 已知 reality DLQ 兼容层仍可 WARN fallback legacy role id（被允许）
  * dream legacy state fallback 使用部署态 DEFAULT_CHAR_ID
  * 已修 active resolver（garden / mood / users / hidden_state_decay / episodic_sweep）不再 fallback yexuan
  * 所有新 slow_queue payload 携带 char_id
  * 关键 admin/core 源文件中不存在活跃 resolver fallback "yexuan" 字符串

已知剩余 TODO（不写 failing 断言，见文件底部注释）：
  T1: [已修] prompt_builder.get_period_info 已传 char_id（P1-0H.1, 2026-06-05）
  T2: scheduler/loop.py + period.py get_period_info 无 char_id 上下文
  T3: scheduler/time_based.py yexuan_inner_diary() 无 char_id
  T4: admin/routers/chat.py get_affection_level 未传 char_id（已冻结系统）
"""

import json
import pathlib
import re
import sys
import types
import unittest.mock as mock
from unittest.mock import patch

import pytest

ROOT = pathlib.Path(__file__).parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# 帮助工具
# ─────────────────────────────────────────────────────────────────────────────

def _read_src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _lines_with(src: str, pat: str) -> list[str]:
    """返回包含 pat 的行列表（去除空白）。"""
    return [l.strip() for l in src.splitlines() if pat in l]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Reality DLQ 兼容层：WARN fallback legacy role id 只在明确函数中存在
# ─────────────────────────────────────────────────────────────────────────────

_DLQ_ALLOWED_FUNCTIONS = {
    "core/pipeline.py":              "_get_scope_from_payload",
    "core/memory/fixation_pipeline.py": "_get_scope_from_payload",
}

# P1-3A: DLQ fallback 由旧 `return "yexuan"` 改为 `MemoryScope.reality_scope(uid, "yexuan")`
_DLQ_FALLBACK_PATTERN = ', "yexuan")'  # 出现在 reality_scope(str(uid), "yexuan") 中


def test_dlq_fallback_yexuan_only_in_allowed_functions():
    """
    Reality DLQ legacy role id fallback 只出现在两个已知兼容函数中
    （pipeline/_get_scope_from_payload、fixation/_get_scope_from_payload）。
    """
    for rel, allowed_fn in _DLQ_ALLOWED_FUNCTIONS.items():
        src = _read_src(rel)
        fn_idx = src.find(f"def {allowed_fn}")
        assert fn_idx != -1, f"{rel} 找不到函数 {allowed_fn}"

        next_def = min(
            (src.find(p, fn_idx + 1) for p in ("\ndef ", "\nasync def ") if src.find(p, fn_idx + 1) != -1),
            default=-1,
        )
        fn_body = src[fn_idx: next_def if next_def != -1 else len(src)]
        assert _DLQ_FALLBACK_PATTERN in fn_body, (
            f"{rel}: {allowed_fn} 函数体中找不到 DLQ fallback pattern {_DLQ_FALLBACK_PATTERN!r}"
        )


def test_no_unexpected_return_yexuan_in_admin_routers():
    """所有 admin/routers/*.py 中不存在 `return "yexuan"` 型 fallback。"""
    router_dir = ROOT / "admin" / "routers"
    for py_file in router_dir.glob("*.py"):
        src = py_file.read_text(encoding="utf-8")
        lines = [l.strip() for l in src.splitlines() if 'return "yexuan"' in l]
        assert not lines, (
            f"{py_file.name} 中存在 `return 'yexuan'`: {lines}"
        )


def test_no_unexpected_return_yexuan_in_core_memory():
    """
    core/memory/*.py 中不应有意外的 `return "yexuan"` fallback。
    P1-3A 后 fixation_pipeline 使用 MemoryScope.reality_scope(uid, "yexuan")，
    不再出现裸 `return "yexuan"` 行，但应保留 _get_scope_from_payload 函数。
    """
    mem_dir = ROOT / "core" / "memory"
    for py_file in mem_dir.glob("*.py"):
        src = py_file.read_text(encoding="utf-8")
        bad = [l.strip() for l in src.splitlines() if 'return "yexuan"' in l]
        assert not bad, (
            f"core/memory/{py_file.name} 中出现意外的 `return 'yexuan'`: {bad}"
        )
    # fixation_pipeline 应保留 DLQ scope fallback helper
    fp_src = (ROOT / "core/memory/fixation_pipeline.py").read_text(encoding="utf-8")
    assert "_get_scope_from_payload" in fp_src, (
        "fixation_pipeline.py 应保留 _get_scope_from_payload DLQ 兼容 helper"
    )


def test_no_unexpected_return_yexuan_in_core_garden():
    """core/garden/*.py 不含 `return 'yexuan'`。"""
    garden_dir = ROOT / "core" / "garden"
    for py_file in garden_dir.glob("*.py"):
        src = py_file.read_text(encoding="utf-8")
        bad = [l.strip() for l in src.splitlines() if 'return "yexuan"' in l]
        assert not bad, (
            f"core/garden/{py_file.name}: `return 'yexuan'` 应已移除: {bad}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2. slow_queue 新 payload 路径都携带 char_id
# ─────────────────────────────────────────────────────────────────────────────

def test_pipeline_slow_queue_payloads_carry_char_id():
    """
    pipeline.py 的 slow_queue.enqueue 调用（capture_turn_retry /
    summarize_to_midterm / user_profile_update）都包含 char_id 键。
    用源码扫描确认：每个 enqueue 块内 "char_id" 出现在 "}" 之前。
    """
    src = _read_src("core/pipeline.py")
    # 找到所有 enqueue(...) 块
    for m in re.finditer(r'slow_queue\.enqueue\([^)]+\{', src):
        start = m.start()
        # 找到这个 dict 字面量的结束（向后找匹配的 }）
        depth = 0
        end = start
        for i in range(start, min(start + 500, len(src))):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        block = src[start:end + 1]
        handler = re.search(r'enqueue\("([^"]+)"', block)
        handler_name = handler.group(1) if handler else "unknown"
        # consistency_check 不需要 char_id（回复一致性检查，非 scoped）
        if handler_name in ("consistency_check",):
            continue
        assert '"char_id"' in block or "'char_id'" in block, (
            f"pipeline.py: enqueue({handler_name!r}) payload 缺少 char_id\n{block}"
        )


def test_fixation_pipeline_slow_queue_payloads_carry_char_id():
    """
    fixation_pipeline.py 的 slow_queue.enqueue 调用都携带 char_id。
    """
    src = _read_src("core/memory/fixation_pipeline.py")
    for m in re.finditer(r'slow_queue\.enqueue\([^)]+\{', src):
        start = m.start()
        depth = 0
        end = start
        for i in range(start, min(start + 500, len(src))):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        block = src[start:end + 1]
        handler = re.search(r'enqueue\("([^"]+)"', block)
        handler_name = handler.group(1) if handler else "unknown"
        assert '"char_id"' in block or "'char_id'" in block, (
            f"fixation_pipeline.py: enqueue({handler_name!r}) payload 缺少 char_id\n{block}"
        )


def test_episodic_sweep_slow_queue_payload_carries_char_id():
    """episodic_sweep.py 的 enqueue 调用携带 char_id。"""
    src = _read_src("core/scheduler/triggers/episodic_sweep.py")
    assert '"char_id": char_id' in src or '"char_id":char_id' in src or (
        '"char_id"' in src
    ), "episodic_sweep.py enqueue payload 应含 char_id"


# ─────────────────────────────────────────────────────────────────────────────
# 3. admin routes active 缺失 / 非法时不读写 yexuan
# ─────────────────────────────────────────────────────────────────────────────

# ── 3a. admin/users active 缺失 / 非法 → 503/422（覆盖性回归）────────────────
# 详细测试见 test_admin_users_profile_scope.py；此处仅做源码级检查回归。

def test_admin_users_router_source_has_resolve_char_id():
    """
    admin/routers/users.py 应包含 _resolve_char_id 函数，
    确认 P1-0E 的 fail-loud 逻辑未被意外移除。
    """
    src = _read_src("admin/routers/users.py")
    assert "def _resolve_char_id" in src, (
        "admin/routers/users.py 应有 _resolve_char_id 函数（fail-loud resolver）"
    )
    assert "status_code=503" in src, (
        "_resolve_char_id 应在 active 缺失时抛 503"
    )
    assert "status_code=422" in src, (
        "_resolve_char_id 应在 char_id 非法时抛 422"
    )


# ── 3b. admin/users _resolve_char_id 源文件不含 "yexuan" 硬编码 ──────────────

def test_admin_users_router_source_no_hardcoded_yexuan():
    """
    admin/routers/users.py 不含硬编码的 `"yexuan"` 字符串（fallback 或 default）。
    注释 / 文档字符串里可以有。
    """
    src = _read_src("admin/routers/users.py")
    # 去掉注释和字符串（简单检测：去掉 # 行 + 多行字符串）
    code_lines = [
        l for l in src.splitlines()
        if not l.strip().startswith("#") and '"""' not in l and "'''" not in l
    ]
    code = "\n".join(code_lines)
    assert '"yexuan"' not in code, (
        'admin/routers/users.py 代码行中不应出现 "yexuan" 硬编码'
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. 源码扫描：生产 active resolver 不在允许列表外 fallback
# ─────────────────────────────────────────────────────────────────────────────

_ACTIVE_RESOLVER_FILES = [
    "admin/routers/mood.py",
    "admin/routers/garden.py",
    "admin/routers/users.py",
    "core/garden/manager.py",
    "core/scheduler/triggers/hidden_state_decay.py",
    "core/scheduler/triggers/episodic_sweep.py",
]


def test_active_resolver_files_no_return_yexuan_fallback():
    """
    active resolver 关键文件中不存在 `return "yexuan"` 型 fallback。
    """
    for rel in _ACTIVE_RESOLVER_FILES:
        src = _read_src(rel)
        bad = [l.strip() for l in src.splitlines() if 'return "yexuan"' in l]
        assert not bad, (
            f"{rel} 中存在意外的 `return 'yexuan'` fallback: {bad}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 5. DLQ 兼容层有 WARN 日志
# ─────────────────────────────────────────────────────────────────────────────

def test_dlq_compat_layers_emit_warn():
    """
    三个 DLQ 兼容函数都包含 logger.warning(...) 调用。
    P1-3A 后 pipeline/fixation 改名为 _get_scope_from_payload。
    """
    checks = [
        ("core/pipeline.py",               "_get_scope_from_payload",  "logger.warning"),
        ("core/memory/fixation_pipeline.py","_get_scope_from_payload",  "logger.warning"),
        ("core/dream/dream_pipeline.py",    "_state_char_id",            "logger.warning"),
    ]
    for rel, fn_name, warn_token in checks:
        src = _read_src(rel)
        fn_start = src.find(f"def {fn_name}")
        assert fn_start != -1, f"{rel}: 找不到 {fn_name}"
        fn_end = src.find("\ndef ", fn_start + 1)
        fn_body = src[fn_start: fn_end if fn_end != -1 else fn_start + 1200]
        assert warn_token in fn_body, (
            f"{rel}: {fn_name} 应包含 {warn_token} 调用"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 6. garden active resolver 不再存在 fallback yexuan（回归）
# ─────────────────────────────────────────────────────────────────────────────

def test_garden_manager_source_no_active_fallback_yexuan():
    """
    core/garden/manager.py 不含 active fallback yexuan 字符串
    （P1-0F 移除了 `or "yexuan"` 型 active 兼容代码）。
    """
    src = _read_src("core/garden/manager.py")
    # 允许 API 默认值 char_id="yexuan"，但不允许 active 解析路径 fallback
    bad_patterns = [
        'or "yexuan"',
        'return "yexuan"',
        'else "yexuan"',
    ]
    for pat in bad_patterns:
        assert pat not in src, (
            f"core/garden/manager.py 仍含已移除的 active fallback: {pat!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 7. hidden_state_decay 遍历所有注册角色而不是只处理 yexuan（回归）
# ─────────────────────────────────────────────────────────────────────────────


def test_hidden_state_decay_source_iterates_registry():
    """
    hidden_state_decay.py 使用 get_registry().list_all("character") 遍历所有角色，
    不硬编码 yexuan。（详细运行时测试见 test_hidden_state_decay_all_chars.py）
    """
    src = _read_src("core/scheduler/triggers/hidden_state_decay.py")
    assert "get_registry" in src, "hidden_state_decay 应调用 get_registry"
    assert 'list_all("character")' in src, "应遍历 list_all(character)"
    code_lines = [l for l in src.splitlines() if not l.strip().startswith("#")]
    assert 'return "yexuan"' not in "\n".join(code_lines), (
        "不应有 active fallback return yexuan"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 8. episodic_sweep 对所有注册角色运行（源码级回归）
# ─────────────────────────────────────────────────────────────────────────────

def test_episodic_sweep_source_iterates_registry():
    """
    episodic_sweep.py 使用 get_registry().list_all("character") 遍历所有角色。
    （详细运行时测试见 test_episodic_sweep_char_scope.py）
    """
    src = _read_src("core/scheduler/triggers/episodic_sweep.py")
    assert "get_registry" in src, "episodic_sweep 应调用 get_registry"
    assert 'list_all("character")' in src, "应遍历 list_all(character)"
    code_lines = [l for l in src.splitlines() if not l.strip().startswith("#")]
    assert 'return "yexuan"' not in "\n".join(code_lines), (
        "不应有 active fallback return yexuan"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 9. slow_queue 旧 payload（无 char_id）由 DLQ 兼容层处理并发出 WARN
# ─────────────────────────────────────────────────────────────────────────────

def test_pipeline_dlq_fallback_emits_warning_for_legacy_payload():
    """
    _get_scope_from_payload(payload_without_char_id) 返回 character_id="yexuan" 并发出 WARNING。
    P1-3A: 返回值由 str 改为 MemoryScope。
    """
    import logging
    from core.pipeline import _get_scope_from_payload

    with mock.patch.object(
        __import__("logging").getLogger("core.pipeline"),
        "warning",
    ) as warn_mock:
        scope = _get_scope_from_payload({"uid": "u1"}, "test_handler")

    assert scope.character_id == "yexuan"
    warn_mock.assert_called_once()
    call_args = str(warn_mock.call_args)
    assert "yexuan" in call_args


def test_fixation_dlq_fallback_emits_warning_for_legacy_payload():
    """
    fixation_pipeline._get_scope_from_payload 对旧 payload 同样 WARN + fallback yexuan。
    P1-3A: 返回值由 str 改为 MemoryScope。
    """
    import logging
    from core.memory.fixation_pipeline import _get_scope_from_payload

    with mock.patch.object(
        __import__("logging").getLogger("core.memory.fixation_pipeline"),
        "warning",
    ) as warn_mock:
        scope = _get_scope_from_payload({"uid": "u1"}, "test_handler")

    assert scope.character_id == "yexuan"
    warn_mock.assert_called_once()


def test_dream_pipeline_dlq_fallback_emits_warning_for_legacy_state():
    """
    dream_pipeline._state_char_id 对缺少 char_id 的 state 发出 WARN，
    并 fallback 到部署态 DEFAULT_CHAR_ID。
    """
    from core.data_paths import DEFAULT_CHAR_ID
    from core.dream.dream_pipeline import _state_char_id
    import logging

    with mock.patch.object(
        __import__("logging").getLogger("core.dream.dream_pipeline"),
        "warning",
    ) as warn_mock:
        result = _state_char_id({}, "test_handler", uid="u1", dream_id="d1")

    assert result == DEFAULT_CHAR_ID
    warn_mock.assert_called_once()
    assert "legacy" in str(warn_mock.call_args).lower()


def test_pipeline_dlq_new_payload_no_fallback():
    """
    _get_scope_from_payload 对包含 char_id（无 scope）的旧 payload 不触发 fallback WARNING。
    P1-3A: 返回值由 str 改为 MemoryScope。
    """
    import logging
    from core.pipeline import _get_scope_from_payload

    with mock.patch.object(
        __import__("logging").getLogger("core.pipeline"),
        "warning",
    ) as warn_mock:
        scope = _get_scope_from_payload({"uid": "u1", "char_id": "character_b"}, "h")

    assert scope.character_id == "character_b"
    warn_mock.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 知名剩余 TODO（不写 failing 断言）
# ─────────────────────────────────────────────────────────────────────────────
#
# T1 (prompt_builder.py) ── [已修 P1-0H.1, 2026-06-05]
#   get_period_info(user_id, char_id=char_id) に修正済。
#   tests/test_prompt_builder_period_scope.py にて 7 件のテストで検証済。
#
# T2 (scheduler/loop.py:478, scheduler/triggers/period.py:13)
#   get_period_info(uid) 在调度器中无 char_id 上下文。
#   风险：调度器始终读 yexuan 的生理期记录。
#   修复建议：在调度器读取 active_prompt_assets.json 解析 char_id，
#   缺失时 warning + skip（per 判断规则）。
#
# T3 (scheduler/triggers/time_based.py:437, :817)
#   get_paths().yexuan_inner_diary() 调用无 char_id。
#   调度器写内心日记时始终写到 yexuan 路径。
#   注意：函数名 yexuan_inner_diary 本身暗示了 yexuan 专属，
#   但多角色系统下应传 char_id。
#   修复建议：调度器解析 active char_id 并透传。
#
# T4 (admin/routers/chat.py:107, :219)
#   get_affection_level(user_id) 未传 char_id（好感度系统已冻结）。
#   风险：好感度始终从 yexuan 桶读取。
#   修复建议：从 pipeline._active_character_id 获取 char_id 并透传，
#   或将好感度迁移到非角色域存储。
