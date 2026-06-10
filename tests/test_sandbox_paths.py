from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest

from core.sandbox import DataPaths, safe_user_id


def test_datapaths_rejects_parent_traversal(sandbox):
    with pytest.raises(ValueError):
        sandbox._p("../evil")


def test_datapaths_rejects_absolute_part(sandbox, tmp_path):
    with pytest.raises(ValueError):
        sandbox._p(tmp_path / "evil")


def test_datapaths_fixed_paths_still_generate(sandbox, tmp_path):
    assert sandbox.history() == tmp_path / "chars" / "yexuan" / "history"
    assert sandbox.channel_queue() == tmp_path / "runtime" / "channel_queue.json"


def test_debug_llm_output_dir_uses_test_sandbox_session_path():
    paths = DataPaths(mode="test", test_session_id="unit_session")

    assert paths.debug_llm_output_dir() == (
        Path("data") / "test_sandbox" / "unit_session" / "debug" / "llm_output"
    )


def test_llm_output_validator_writes_debug_to_sandbox(sandbox):
    from core.llm_output_validator import FailureCounter

    uid = f"sandbox_debug_{uuid4().hex}"
    production_matches_before = list(Path("data/debug/llm_output").glob(f"*_{uid}.txt"))

    FailureCounter().record_failure("unit_validator", "raw debug output", uid)

    sandbox_matches = list(sandbox.debug_llm_output_dir().glob(f"*_{uid}.txt"))
    production_matches_after = list(Path("data/debug/llm_output").glob(f"*_{uid}.txt"))
    assert len(sandbox_matches) == 1
    assert sandbox_matches[0].read_text(encoding="utf-8") == "raw debug output"
    assert production_matches_after == production_matches_before


def test_safe_user_id_digits_unchanged():
    assert safe_user_id("1234567890") == "1234567890"


def test_memory_paths_reject_malicious_uid(sandbox):
    from core.memory import episodic_memory, event_log, mid_term, short_term, user_identity, user_profile

    checks = [
        lambda: short_term._history_path("../evil"),
        lambda: user_profile._profile_write_path("../evil"),
        lambda: user_identity._identity_write_file("../evil"),
        lambda: event_log._day_file_write("../evil", datetime(2026, 1, 1)),
        lambda: event_log._full_log_file_write("../evil"),
        lambda: episodic_memory._mem_write_file("../evil"),
        lambda: episodic_memory._index_write_file("../evil"),
        lambda: mid_term._write_file("../evil"),
    ]

    for check in checks:
        with pytest.raises(ValueError):
            check()
