import re
from pathlib import Path

from admin_static_assets import PAGES, read_admin_client_source


STATIC = Path(__file__).parents[1] / "admin" / "static"


def test_common_form_control_primitive_covers_textual_and_native_inputs():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    primitive = css[css.index("label.field span"):css.index(".checkbox-row {")]

    assert "input:not([type])" in primitive
    for input_type in (
        "text", "number", "time", "date", "datetime-local", "email",
        "password", "search", "tel", "url",
    ):
        assert f'input[type="{input_type}"]' in primitive
    for state in (":hover", ":focus", ":disabled", "[readonly]", "::placeholder"):
        assert state in primitive
    assert "textarea" in primitive
    assert "select" in primitive
    assert "box-shadow" in primitive
    assert re.search(r"(?m)^\s*input\s*\{", css) is None
    assert 'color-scheme: dark' in primitive

    for excluded_type in ("checkbox", "radio", "file", "range", "button", "submit", "reset", "image", "hidden"):
        assert f'input[type="{excluded_type}"]' not in primitive
        assert f"input[type={excluded_type}]" not in primitive


def test_admin_templates_declare_input_types_and_do_not_keep_orphan_input_class():
    source = read_admin_client_source()
    omitted_type = re.compile(r"<input\b(?![^>]*\btype\s*=)[^>]*>", re.IGNORECASE | re.DOTALL)

    assert not omitted_type.search(source)
    assert 'class="input' not in source


def test_setup_and_character_anniversary_editors_consume_shared_helper():
    core = (STATIC / "js" / "core.js").read_text(encoding="utf-8")
    setup = (STATIC / "js" / "setup.js").read_text(encoding="utf-8")
    character = (STATIC / "js" / "character.js").read_text(encoding="utf-8")

    assert "function renderAnniversaryRow" in core
    assert "function renderAnniversaryEditor" in core
    assert "function addAnniversaryEditorRow" in core
    assert "function removeAnniversaryEditorRow" in core
    assert "function readAnniversaryEditor" in core
    assert "escapeHtml(String(rawValue))" in core
    for field in ("key", "month", "day", "year_start", "prompt_zero", "prompt_years"):
        assert f"['{field}'" in core or f'"{field}"' in core

    for page_source, remove_action in ((setup, "removeSetupRow"), (character, "removeCharacterAnniversary")):
        assert "renderAnniversaryEditor(" in page_source
        assert "addAnniversaryEditorRow(" in page_source
        assert "readAnniversaryEditor(" in page_source
        assert remove_action in page_source
        assert "_anniversaryRow" not in page_source
        assert "_characterAnniversaryRow" not in page_source

    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '/static/js/setup.js?v=admin-navigation-guide-1' in index
    assert '/static/js/character.js?v=v1-1-0-ci-1' in index
    assert '/static/js/user-data.js?v=brief-162-userdata-assets-1' in index


def test_anniversary_fragments_keep_their_page_containers_and_accessible_dynamic_fields():
    setup = (PAGES / "personal-settings.html").read_text(encoding="utf-8")
    character = (PAGES / "character.html").read_text(encoding="utf-8")
    assert 'id="setup-anniversaries-list"' in setup
    assert 'id="char-anniversaries"' in character
    core = (STATIC / "js" / "core.js").read_text(encoding="utf-8")
    for label in (
        "Anniversary key", "Anniversary month", "Anniversary day",
        "Anniversary starting year", "Anniversary first-year prompt",
        "Anniversary later-years prompt",
    ):
        assert label in core
    assert 'aria-label="${ariaLabel}"' in core
