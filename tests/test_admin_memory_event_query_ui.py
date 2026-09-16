from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_memory_event_query_page_is_registered_and_cache_busted():
    index = (ROOT / "admin/static/index.html").read_text(encoding="utf-8")
    core = (ROOT / "admin/static/js/core.js").read_text(encoding="utf-8")
    script = (ROOT / "admin/static/js/observability.js").read_text(encoding="utf-8")
    fragment = (ROOT / "admin/static/pages/observe-memory-events.html").read_text(encoding="utf-8")

    assert 'data-page="observe-memory-events"' in index
    assert 'id="page-observe-memory-events"' in index
    assert "ADMIN_UI_FRAGMENT_VERSION = 'brief-253-file-path-1'" in core
    assert '<script src="/static/js/core.js?v=brief-253-file-path-1"></script>' in index
    assert '<script src="/static/js/observability.js?v=brief-253-file-path-1"></script>' in index
    assert '<script src="/static/i18n.js?v=brief-253-file-path-1"></script>' in index
    assert "loadMemoryEventSearch" in script
    assert "/memory-events/query-trace" in script
    assert "tombstoneMemoryEvent" in script
    assert "/observability/memory-event-migration" in script
    assert 'id="event-query-result"' in fragment
    assert 'id="event-shadow-observability"' in fragment
    assert 'id="event-proposer-observability"' in fragment


def test_shadow_recall_rollout_controls_are_registered_and_cache_busted():
    index = (ROOT / "admin/static/index.html").read_text(encoding="utf-8")
    core = (ROOT / "admin/static/js/core.js").read_text(encoding="utf-8")
    settings = (ROOT / "admin/static/js/settings.js").read_text(encoding="utf-8")
    runtime = (ROOT / "admin/static/js/runtime-config.js").read_text(encoding="utf-8")
    fragment = (ROOT / "admin/static/pages/runtime-config.html").read_text(encoding="utf-8")

    assert 'id="event-shadow-uids"' in fragment
    assert "loadEventShadowRecallSettings" in settings
    assert "saveEventShadowRecallSettings" in settings
    assert "loadEventShadowRecallSettings();" in runtime
    assert "ADMIN_UI_FRAGMENT_VERSION = 'brief-253-file-path-1'" in core
    assert '<script src="/static/js/settings.js?v=brief-252-routing-profile-crud-1"></script>' in index
    assert '<script src="/static/js/runtime-config.js?v=settings-center-1"></script>' in index
    assert '<script src="/static/js/memory-event-control.js?v=brief-216-memory-event-control-1"></script>' in index


def test_memory_event_control_ui_has_effective_route_and_redacted_metrics():
    control = (ROOT / "admin/static/js/memory-event-control.js").read_text(encoding="utf-8")
    routing = (ROOT / "admin/static/pages/model-routing.html").read_text(encoding="utf-8")
    runtime = (ROOT / "admin/static/pages/runtime-config.html").read_text(encoding="utf-8")

    assert "effective_state" in control
    assert "未运行" in control
    assert "memory-event-shadow-recall" in control
    assert "memory-event-edge-proposals" in control
    assert "reload_status" in control
    assert 'id="mr-event-proposer-route-status"' in routing
    assert 'id="event-shadow-effective"' in runtime
    assert "raw_text" not in control and "seed_event_ids" not in control
