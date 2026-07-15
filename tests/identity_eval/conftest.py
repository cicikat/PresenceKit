from pathlib import Path

import pytest

from tests.identity_eval import engine


@pytest.fixture
def identity_case_env(sandbox):
    primary = engine.new_test_char_id()
    alternate = engine.new_test_char_id()
    engine.install_test_character(primary)
    engine.install_test_character(alternate)
    try:
        yield {"primary": primary, "alternate": alternate}
    finally:
        engine.remove_test_character(primary)
        engine.remove_test_character(alternate)


@pytest.fixture(autouse=True)
def _production_data_untouched():
    root = Path(__file__).parent.parent.parent / "data"
    before = set(root.rglob("*")) if root.exists() else set()
    yield
    after = set(root.rglob("*")) if root.exists() else set()
    assert not after - before, f"identity eval polluted production data: {after - before}"
