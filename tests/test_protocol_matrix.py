"""Static guard for the fixed-SHA compatibility matrix."""

import json
import re
from pathlib import Path


MATRIX = Path(__file__).parents[1] / ".github" / "protocol-matrix.json"


def test_protocol_matrix_uses_full_commit_shas_and_one_fixture_version():
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert matrix["matrix_version"] == "1"
    assert matrix["fixture_version"] == "v1"
    from tests.protocol_evidence import verified
    assert matrix["classification"] in {"not_verified", "compatible"}
    if matrix["classification"] == "compatible":
        assert verified(matrix["evidence"], matrix["repositories"])
    else:
        assert matrix["evidence"]["recorded_results"] == "not_verified"
    assert set(matrix["repositories"]) == {"backend", "desktop", "mobile"}
    for repository in matrix["repositories"].values():
        assert re.fullmatch(r"[0-9a-f]{40}", repository["sha"])
        assert repository["repository"].count("/") == 1


def test_protocol_matrix_has_one_contract_suite_per_repository():
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    names = {suite["name"] for suite in matrix["suites"]}
    assert names == {
        "backend protocol provider",
        "desktop protocol consumer",
        "mobile protocol consumer",
    }
