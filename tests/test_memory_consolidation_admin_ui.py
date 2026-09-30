from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_memory_consolidation_page_is_registered_and_cache_busted():
    index = (ROOT / "admin/static/index.html").read_text(encoding="utf-8")
    core = (ROOT / "admin/static/js/core.js").read_text(encoding="utf-8")
    script = (ROOT / "admin/static/js/observability.js").read_text(encoding="utf-8")
    page = (ROOT / "admin/static/pages/memory-consolidation.html").read_text(encoding="utf-8")

    assert 'data-page="memory-consolidation"' in index
    assert 'id="page-memory-consolidation"' in index
    assert "ADMIN_UI_FRAGMENT_VERSION = 'v1-local-runtime-1'" in core
    assert '<script src="/static/js/core.js?v=v1-local-runtime-1"></script>' in index
    assert '<script src="/static/js/observability.js?v=self-tool-audit-5"></script>' in index
    assert "loadMemoryConsolidationStatus" in script
    assert "/observability/memory-consolidation" in script
    assert "/settings/memory-consolidation" in script
    assert "/memory-consolidation/control" in script
    assert "/memory/dossiers" in script
    assert "/observability/memory-history-reconciliation" in script
    assert "/memory/history-source-items" in script
    assert "loadMemoryHistoryReconciliation" in script
    assert "last_closeout" in script
    assert 'data-action="controlMemoryConsolidation"' in page
    assert "onclick=" not in page


def test_memory_consolidation_page_has_required_status_and_controls():
    page = (ROOT / "admin/static/pages/memory-consolidation.html").read_text(encoding="utf-8")
    for marker in (
        "memory-consolidation-status", "memory-consolidation-enabled",
        "memory-consolidation-start", "memory-consolidation-end",
        "memory-consolidation-calls", "memory-consolidation-daily-tokens",
        "memory-consolidation-wall", "recover_unknown", "revoke",
        "memory-consolidation-dossiers", "memory-consolidation-detail",
        "memory-history-status", "memory-history-items",
        "controlMemoryHistoryReconciliation", "searchMemoryHistorySourceItems",
        "createMemoryHistoryManifest", "history_calibrate", "history_admit",
        "memory-history-go-live", "memory-history-timezone",
    ):
        assert marker in page
