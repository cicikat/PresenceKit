"""Regression contracts for model-preset create/edit action boundaries."""

from pathlib import Path


ROOT = Path(__file__).parent.parent
PAGE = (ROOT / "admin/static/pages/model-routing.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "admin/static/js/settings.js").read_text(encoding="utf-8")


def test_preset_create_action_has_a_dedicated_entrypoint():
    assert 'data-action="openCreatePresetModal"' in PAGE
    assert 'data-action="openPresetModal"' not in PAGE
    assert "function openCreatePresetModal()" in SCRIPT
    assert "_mrEditingPresetName = null;" in SCRIPT


def test_preset_edit_action_only_accepts_a_string_name():
    assert "function openPresetModal(name)" in SCRIPT
    assert "if (typeof name !== 'string')" in SCRIPT
    assert "openCreatePresetModal();" in SCRIPT


def test_preset_editor_exposes_native_reasoning_escape_hatch():
    index = (ROOT / "admin/static/index.html").read_text(encoding="utf-8")
    assert 'id="mr-preset-reasoning-native"' in index
    assert 'id="mr-preset-reasoning-extra-body"' in index
    assert "body.reasoning_native =" in SCRIPT
    assert "body.reasoning_extra_body = extraBody;" in SCRIPT
    assert "body.reasoning_extra_body = {};" in SCRIPT


def test_profile_editor_exposes_independent_fallback_selects():
    assert 'data-i18n="routing.failover.hint"' in PAGE
    assert 'id="mr-failover-stats"' in PAGE
    assert "mr-profile-fallback-${cat}" in SCRIPT
    assert "body.fallback = fallback;" in SCRIPT
    assert "function loadLlmFailoverStats()" in SCRIPT
    assert "bindPageActions(el);" in SCRIPT
    assert "/observability/llm-failover?window_hours=" in SCRIPT
    records = (ROOT / "admin/static/pages/call-records.html").read_text(encoding="utf-8")
    assert "/observability/llm-failover?window_hours=24" in records
    assert 'data-i18n="settings_center.llm_failover"' in records
