"""
tests/test_source_lint.py — Brief 50 · 工单C.1

长期价值的源码字符串守卫（inspect.getsource / read_text 断言），从已退役的
一次性迁移审计文件中压缩迁入：

- test_memory_resolver_remaining_paths_audit.py（P1-2J）
  → character_growth 不在 pipeline 主链 + 不是 slow_queue handler

其余迁移完结的逐函数 resolve_path() 断言、路径覆盖率断言均已被
test_memory_path_resolver.py（行为级测试）和 test_r3_scope_lint.py /
test_memory_direct_path_lint.py（全仓扫描级 lint）覆盖，未随本文件迁移。

"legacy trigger 不复活"（_legacy_active_window_blocks / _legacy_dnd_blocks 不
得重新出现在 core/scheduler/loop.py）守卫已存在于
tests/test_r2b_active_window_gating.py::TestPipelineSendR2C，同样未重复迁移。

每条守卫一个测试函数，docstring 注明来源。
"""
from __future__ import annotations
from core.post_process import handlers as _handlers

import inspect
import re


def _has_growth_code_call(src: str) -> bool:
    """剥离 docstring/注释后判断源码是否仍有 character_growth 的真实代码引用。"""
    stripped = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    stripped = re.sub(r"'''.*?'''", "", stripped, flags=re.DOTALL)
    stripped = re.sub(r"#[^\n]*", "", stripped)
    return "character_growth" in stripped


def test_prompt_builder_has_no_character_growth_reference():
    """来源：P1-2J。prompt_builder 不得引用 character_growth（已退役、非主链）。"""
    from core import prompt_builder

    src = inspect.getsource(prompt_builder)
    assert "character_growth" not in src, "prompt_builder must not reference character_growth"


def test_pipeline_module_has_no_character_growth_code_call():
    """来源：P1-2J。core/pipeline.py 全模块不得有 character_growth 的真实代码调用
    （fetch_context / build_prompt / post_process / register_slow_handlers 均覆盖在内；
    docstring/注释里的历史提及不算违规）。
    """
    from core import pipeline as _pipeline

    src = inspect.getsource(_pipeline)
    assert not _has_growth_code_call(src), (
        "core/pipeline.py must not contain character_growth code calls anywhere "
        "(character_growth is legacy/dead, not in the reality pipeline main chain)"
    )


def test_consolidate_to_growth_not_registered_in_slow_queue():
    """来源：P1-2J。slow_queue 不得注册 consolidate_to_growth handler。"""
    from core import pipeline as _pipeline

    register_src = inspect.getsource(_handlers.register_slow_handlers)
    assert "consolidate_to_growth" not in register_src, (
        "consolidate_to_growth must not be a slow_queue handler"
    )
