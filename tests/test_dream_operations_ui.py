"""Admin Dream operations page is wired into the versioned static shell."""
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_admin_dream_operations_static_wiring():
    index = (ROOT / "admin/static/index.html").read_text(encoding="utf-8")
    registry = (ROOT / "admin/static/js/admin-page-registry.js").read_text(encoding="utf-8")
    i18n = (ROOT / "admin/static/i18n.js").read_text(encoding="utf-8")
    assert "observe-dream-operations" in index
    assert "dream-operations" in index
    assert "brief-175-scenario-reconciler-1" in index
    assert "observe-dream-operations" in registry
    assert "dream_ops.title" in i18n
    assert "dream_ops.scenario_progress" in i18n
    assert "dream_ops.title" in (ROOT / "admin/static/pages/observe-dream-operations.html").read_text(encoding="utf-8")
    page = (ROOT / "admin/static/pages/observe-dream-operations.html").read_text(encoding="utf-8")
    script = (ROOT / "admin/static/js/dream-operations.js").read_text(encoding="utf-8")
    assert "dream-ops-scenario-progress" in page
    assert "dream-ops-scenario-reconciliation" in page
    assert "scenario_progress" in script
    assert "scenario_reconciliation" in script
