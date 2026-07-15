import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from tests.identity_eval import engine  # noqa: E402


def main() -> int:
    failures = 0
    cases = engine.load_cases()
    for case in cases:
        with pytest.MonkeyPatch.context() as mp, tempfile.TemporaryDirectory() as tmp:
            import core.sandbox as sandbox
            import core.asset_registry as registry
            paths = sandbox.DataPaths(mode="test", test_session_id="identity_eval")
            paths._base = Path(tmp)
            mp.setattr(sandbox, "_instance", paths)
            char_ids = {"primary": engine.new_test_char_id(), "alternate": engine.new_test_char_id()}
            for char_id in char_ids.values():
                engine.install_test_character(char_id)
            mp.setattr(registry, "_registry", None)
            try:
                result = engine.run_case(case, mp, char_ids=char_ids)
                problems = engine.check_expectations(case, result)
            except Exception as exc:  # noqa: BLE001
                problems = [repr(exc)]
            finally:
                for char_id in char_ids.values():
                    engine.remove_test_character(char_id)
                registry._registry = None
        if problems:
            failures += 1
            print(f"[FAIL] {case['id']}: {'; '.join(problems)}")
        else:
            print(f"[ok]   {case['id']}")
    print(f"identity eval: {len(cases) - failures}/{len(cases)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
