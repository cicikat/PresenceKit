"""Fail loud when the current interpreter is missing full-runtime extras.

Install / upgrade scripts sync requirements.lock (compiled from
requirements-full.txt). This check is the post-sync guard so a core-only
environment cannot be mistaken for a complete runtime.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.runtime_deps import (  # noqa: E402
    declared_full_requirement_names,
    lock_distribution_names,
    missing_runtime_imports,
)


def main() -> int:
    missing = missing_runtime_imports()
    if missing:
        print("完整运行依赖缺失（requirements.lock / requirements-full.txt）：")
        for item in missing:
            print(f"  - {item['name']}  （{item['label']}）")
        print("请重新运行安装或更新脚本，确认 uv pip sync requirements.lock 成功。")
        return 1

    full_path = ROOT / "requirements-full.txt"
    lock_path = ROOT / "requirements.lock"
    if full_path.is_file() and lock_path.is_file():
        declared = declared_full_requirement_names(full_path)
        locked = lock_distribution_names(lock_path)
        absent = [name for name in declared if name not in locked]
        if absent:
            print("requirements.lock 未包含完整运行依赖：")
            for name in absent:
                print(f"  - {name}")
            print("请从 requirements-full.txt 重新编译锁文件后再同步。")
            return 1

    print("完整运行依赖已就绪。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
