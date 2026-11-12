"""
tests/run_memeval.py — memeval 离线 runner，不依赖 pytest 收集，供开发者单独跑。

用法：
    python tests/run_memeval.py

对每条 tests/memeval/cases/*.yaml 用例，播种记忆 → 跑
Pipeline.fetch_context() / episodic_memory.retrieve() / Pipeline.build_prompt() →
按 case 的 expect 字段做确定性断言。xfail 用例失败视为预期，不计入退出码。
"""

import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

_ROOT = Path(__file__).parent.parent
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402  (只借用 MonkeyPatch，不跑 pytest 收集)

from tests.memeval import engine  # noqa: E402

_RECALL_MODES = ["natural", "sem_zeroed"]


def _run_one(case: dict, recall_mode: str) -> list[str]:
    """返回该 case 在指定 recall_mode 下的断言失败信息（空列表=通过）。"""
    with pytest.MonkeyPatch.context() as mp, tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        import core.sandbox as _sandbox
        paths = _sandbox.DataPaths(mode="test", test_session_id="run_memeval")
        paths._base = tmp_path
        paths._project_root = tmp_path
        mp.setattr(_sandbox, "_instance", paths)

        import core.asset_registry as _reg_mod
        char_id = engine.new_test_char_id()
        engine.install_test_character(char_id)
        mp.setattr(_reg_mod, "_registry", None)
        try:
            result = engine.run_case(case, mp, recall_mode=recall_mode, char_id=char_id)
            return engine.check_expectations(case, result)
        finally:
            engine.remove_test_character(char_id)
            _reg_mod._registry = None


def main() -> int:
    cases = engine.load_cases()
    if len(cases) < 25:
        print(f"[FAIL] memeval case count {len(cases)} < minimum 25")
        return 1
    print("=" * 70)
    print(f"memeval: {len(cases)} cases x {len(_RECALL_MODES)} recall modes")
    print("=" * 70)

    failures = 0
    xfail_ok = 0
    xfail_broken = 0

    for case in cases:
        cid = case["id"]
        category = case["category"]
        is_xfail = bool(case.get("xfail"))

        for mode in _RECALL_MODES:
            label = f"{cid}/{mode}"
            try:
                problems = _run_one(case, mode)
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"[FAIL] {label} {category}: 异常: {e!r}")
                continue
            if is_xfail and problems:
                xfail_ok += 1
                print(f"[XFAIL] {label} {category} ({case['xfail_reason']}): {'; '.join(problems)}")
            elif is_xfail:
                xfail_broken += 1
                print(f"[XPASS] {label} {category}: remove stale xfail")
            elif problems:
                failures += 1
                print(f"[FAIL] {label} {category}: {'; '.join(problems)}")
            else:
                print(f"[ok] {label} {category}")

    print("=" * 70)
    print(
        f"结果：{len(cases) * len(_RECALL_MODES) - failures - xfail_ok - xfail_broken} 正常通过 / "
        f"{xfail_ok} xfail(预期) / {xfail_broken} xfail 意外转正 / {failures} 失败"
    )

    return 1 if (failures or xfail_broken) else 0


if __name__ == "__main__":
    sys.exit(main())
