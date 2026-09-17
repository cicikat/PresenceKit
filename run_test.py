"""
run_test.py — 以 test 模式启动 Emerald-presence。
数据全部写入 data/test_sandbox/{session_id}/，不污染 production 数据。
代替 main.py 使用；结束后询问是否清理沙盒目录。
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

# ── 1. 切换到 Emerald-presence 目录，与 main.py 保持一致 ────────────────────────────
os.chdir(Path(__file__).parent)

# ── 2. 初始化沙盒（必须在所有项目模块导入之前）────────────────────────────────
from core.sandbox import init_paths

_manual_session_id = os.environ.get("PRESENCE_TEST_SESSION_ID") or (
    f"manual_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
)
_paths = init_paths(mode="test", test_session_id=_manual_session_id)

print("=" * 60)
print(f"[TEST] session_id  : {_paths.test_session_id}")
print(f"[TEST] 数据根目录   : {_paths._base.resolve()}")
print(f"[TEST] data_prefix 已通过环境变量 YEXUAN_DATA_PREFIX 声明，config.yaml 保持只读")
print("=" * 60)
print()

# ── 3. 导入并运行主程序 ────────────────────────────────────────────────────────
import main as _main_module

if __name__ == "__main__":
    try:
        asyncio.run(_main_module.main())
    except KeyboardInterrupt:
        print()
        print("[TEST] 服务已停止")
    finally:
        print()
        try:
            answer = input(f"是否清理沙盒数据 {_paths._base}？(y/n) ").strip().lower()
        except EOFError:
            answer = "n"
        if answer == "y":
            _paths.cleanup()
            print("[TEST] 沙盒已清理")
        else:
            print(f"[TEST] 沙盒保留在 {_paths._base.resolve()}")
