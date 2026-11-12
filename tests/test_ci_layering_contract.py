from pathlib import Path

import yaml


WORKFLOW_ROOT = Path(__file__).parents[1] / ".github" / "workflows"


def _workflow(filename):
    return yaml.load((WORKFLOW_ROOT / filename).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_pr_workflow_requires_both_supported_python_versions_and_offline_evals():
    workflow = _workflow("tests.yml")
    assert "main" in workflow["on"]["pull_request"]["branches"]
    assert "main" in _workflow("offline-evals.yml")["on"]["pull_request"]["branches"]
    for job_id in ("pytest", "full-pytest"):
        job = workflow["jobs"][job_id]
        assert set(job["strategy"]["matrix"]["python-version"]) == {"3.10", "3.12"}
        for step in [job, *job["steps"]]:
            assert step.get("continue-on-error", "false") == "false"


def test_external_and_manual_evidence_are_separate_from_pr_required_checks():
    evals = (WORKFLOW_ROOT / "offline-evals.yml").read_text(encoding="utf-8")
    manual = (WORKFLOW_ROOT / "manual-sensor-evidence.yml").read_text(encoding="utf-8")
    assert "run_coplay" in evals
    assert "inputs.run_coplay == true" in evals
    assert "cost_boundary=explicit workflow_dispatch opt-in" in evals
    assert '"level": "manual"' in manual
    assert "evidence_location" in manual
