"""Full-runtime dependency inventory vs requirements-full.txt / lock."""
from pathlib import Path

from core.runtime_deps import (
    RUNTIME_OPTIONAL_DEPS,
    declared_full_requirement_names,
    lock_distribution_names,
    missing_runtime_imports,
)


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_optional_deps_are_declared_in_full_requirements():
    declared = set(declared_full_requirement_names(ROOT / "requirements-full.txt"))
    inventory = {_normalize(item["name"]) for item in RUNTIME_OPTIONAL_DEPS}
    missing = sorted(inventory - declared)
    assert missing == [], f"RUNTIME_OPTIONAL_DEPS not in requirements-full.txt: {missing}"


def test_lock_contains_full_runtime_distributions():
    declared = declared_full_requirement_names(ROOT / "requirements-full.txt")
    locked = lock_distribution_names(ROOT / "requirements.lock")
    absent = [name for name in declared if name not in locked]
    assert absent == [], f"requirements.lock missing full-runtime extras: {absent}"


def test_current_interpreter_imports_full_runtime_packages():
    missing = missing_runtime_imports()
    assert missing == [], (
        "current interpreter is missing full-runtime packages: "
        + ", ".join(item["name"] for item in missing)
    )


def test_install_and_upgrade_scripts_run_runtime_dep_check():
    install = (ROOT / "AA1安装并启动.bat").read_text(encoding="utf-8")
    upgrade = (ROOT / "AA更新.bat").read_text(encoding="utf-8")
    release = (ROOT / "scripts" / "update_release.py").read_text(encoding="utf-8")
    assert "pip sync requirements.lock" in install
    assert "scripts\\check_runtime_deps.py" in install
    assert "scripts\\check_runtime_deps.py" in upgrade
    assert "scripts/check_runtime_deps.py" in release


def _normalize(name: str) -> str:
    return name.replace("_", "-").lower()
