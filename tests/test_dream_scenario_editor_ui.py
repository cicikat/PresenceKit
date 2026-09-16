from pathlib import Path


ROOT = Path(__file__).parents[1]
PAGE = (ROOT / "admin" / "static" / "pages" / "dream-settings.html").read_text(
    encoding="utf-8"
)
SOURCE = (ROOT / "admin" / "static" / "js" / "dream-settings.js").read_text(
    encoding="utf-8"
)
CHARACTER_PAGE = (ROOT / "admin" / "static" / "pages" / "character.html").read_text(encoding="utf-8")
CHARACTER_SOURCE = (ROOT / "admin" / "static" / "js" / "character.js").read_text(encoding="utf-8")


def test_scenario_mode_reveals_the_hidden_authoring_panel():
    assert "mode === 'scenario' ? 'block' : 'none'" in SOURCE
    assert "mode === 'mirror' ? 'block' : 'none'" in SOURCE
    assert "dream-scenario-editor-card').style.display = 'block'" in SOURCE


def test_scenario_editor_is_structured_and_supports_json_exchange():
    assert 'id="ds-stages"' in PAGE
    assert 'id="ds-private-truths"' in PAGE
    assert 'id="ds-json-file"' in PAGE
    assert 'id="ds-yaml"' not in PAGE
    assert 'data-action="addDreamScenarioStage"' in PAGE
    assert 'data-action="importDreamScenarioJson"' in PAGE
    assert 'data-action="exportDreamScenarioJson"' in PAGE
    assert "{ document: documentValue }" in SOURCE
    assert "JSON.parse(text)" in SOURCE
    assert "JSON.stringify(result.document, null, 2)" in SOURCE
    assert "_renderDreamScenarioPrivateTruths" in SOURCE
    assert "data-truth-policy" in SOURCE
    assert "reveal_required" in SOURCE


def test_scenario_editor_supports_backend_yaml_round_trip_and_accessible_format_state():
    assert 'accept=".yaml,.yml,.json,application/yaml,text/yaml,application/json"' in PAGE
    assert 'id="ds-import-format"' in PAGE
    assert 'data-i18n-aria-label="dream.scenario.import_file"' in PAGE
    assert "api('POST', '/dream/scenarios/validate'" in SOURCE
    assert "exportDreamScenarioYaml" in PAGE
    assert "_downloadDreamScenario(`${result.id}.yaml`" in SOURCE
    assert "JSON.parse(text)" in SOURCE
    assert "YAML parser" not in SOURCE


def test_character_editor_exposes_per_mode_dream_behavior():
    assert 'id="char-dream-identity-anchor"' in CHARACTER_PAGE
    assert 'id="char-dream-sandbox-directive"' in CHARACTER_PAGE
    assert 'id="char-dream-scenario-directive"' in CHARACTER_PAGE
    assert "presenceExt.dream_behavior" in CHARACTER_SOURCE
    assert "presenceExt.dream_behavior = dreamBehavior" in CHARACTER_SOURCE


def test_character_editor_shows_json_fields_without_permanent_hide_class():
    assert 'id="char-edit-form" class="admin-inline-056"' in CHARACTER_PAGE
    assert 'id="char-text-form" class="admin-inline-056"' in CHARACTER_PAGE
    assert 'id="char-world-book"' in CHARACTER_PAGE
    assert 'id="char-post-history"' in CHARACTER_PAGE
    assert 'id="char-post-history-extra"' in CHARACTER_PAGE
    assert 'id="char-alternate-greetings"' in CHARACTER_PAGE
    assert 'id="char-proactive"' in CHARACTER_PAGE
    assert 'id="char-tool-loop"' in CHARACTER_PAGE
    assert "classList.remove('admin-inline-056')" in CHARACTER_SOURCE
    assert "classList.add('admin-inline-056')" in CHARACTER_SOURCE
    assert "_renderCharacterWorldBook" in CHARACTER_SOURCE
    assert "_worldBookExtra" in CHARACTER_SOURCE
    assert "post_history_instructions" in CHARACTER_SOURCE
    assert "alternate_greetings" in CHARACTER_SOURCE
    assert "const {type: _charType, filename: _charFilename, ...charRest}" in CHARACTER_SOURCE
