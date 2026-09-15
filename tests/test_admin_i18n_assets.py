from pathlib import Path
from html.parser import HTMLParser
import re

from admin_static_assets import PAGES, read_admin_client_source, read_admin_page

ROOT = Path(__file__).parents[1]
INDEX = ROOT / "admin" / "static" / "index.html"
I18N = ROOT / "admin" / "static" / "i18n.js"


def _dictionary_keys(runtime: str, language: str) -> set[str]:
    if language == "zh-CN":
        body = re.search(r"'zh-CN': \{(.*?)\n    \},\n    en: \{", runtime, re.S)
    else:
        body = re.search(r"\n    en: \{(.*?)\n    \},\n  \};", runtime, re.S)
    assert body is not None
    return set(re.findall(r"^\s+'([^']+)':", body.group(1), re.M))


def _chinese_dictionary_values(runtime: str) -> set[str]:
    body = re.search(r"'zh-CN': \{(.*?)\n    \},\n    en: \{", runtime, re.S)
    assert body is not None
    values = set()
    for match in re.finditer(
        r"^\s+'[^']+':\s*(?:'((?:\\.|[^'\\])*)'|\"((?:\\.|[^\"\\])*)\"),",
        body.group(1),
        re.M,
    ):
        value = match.group(1) if match.group(1) is not None else match.group(2)
        values.add(re.sub(r"\s+", " ", value).strip())
    return values


class _VisibleChineseParser(HTMLParser):
    _EXCLUDED_TAGS = {"script", "style", "code", "pre"}
    _VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
    _TRANSLATED_ATTRIBUTES = {"placeholder", "title", "aria-label"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._excluded = [False]
        self.values: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = set(attributes.get("class", "").split())
        excluded = self._excluded[-1] or tag in self._EXCLUDED_TAGS or any(
            key == "data-i18n" or key == "data-i18n-skip" for key in attributes
        ) or bool(classes & {"log-box", "i18n-raw"}) or attributes.get("id") == "chat-messages"
        if tag not in self._VOID_TAGS:
            self._excluded.append(excluded)
        if not excluded:
            for name in self._TRANSLATED_ATTRIBUTES:
                value = attributes.get(name, "")
                if value and re.search(r"[\u3400-\u9fff]", value) and f"data-i18n-{name}" not in attributes:
                    self.values.append(value)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, _tag):
        self._excluded.pop()

    def handle_data(self, data):
        if not self._excluded[-1] and re.search(r"[\u3400-\u9fff]", data):
            self.values.append(data)


def test_i18n_runtime_is_wired_with_persistent_chinese_default():
    index = read_admin_client_source()
    runtime = I18N.read_text(encoding="utf-8")
    core_js = (ROOT / "admin" / "static" / "js" / "core.js").read_text(encoding="utf-8")

    assert '<link rel="stylesheet" href="/static/style.css?v=admin-design-1">' in index
    assert '<script src="/static/i18n.js?v=brief-252-image-presets-1"></script>' in index
    assert '<script src="/static/js/core.js?v=brief-252-image-presets-1"></script>' in index
    assert '<script src="/static/js/dream-settings.js?v=brief-223-rpg-dream-admin-2"></script>' in index
    assert "ADMIN_UI_FRAGMENT_VERSION = 'brief-252-image-presets-1'" in core_js
    assert '<script src="/static/js/observability.js?v=brief-242-settings-4"></script>' in index
    assert '<script src="/static/js/character.js?v=admin-prompt-followups-1"></script>' in index
    assert 'id="ds-private-truths"' in read_admin_page("dream-settings")
    assert "dream.scenario.policy_reveal_required" in runtime
    assert '<script src="/static/js/overview.js?v=brief-180-admin-static-1"></script>' in index
    assert '<script src="/static/js/mcp.js?v=brief-252-mcp-no-flash-1"></script>' in index
    assert '<script src="/static/js/scheduler.js?v=calendar-ui-1"></script>' in index
    assert '<script src="/static/js/integrations.js?v=brief-160-garden-freeze-1"></script>' in index
    assert "const DEFAULT_LANGUAGE = 'zh-CN';" in runtime
    assert "presence.admin.language" in runtime
    assert "localStorage.setItem(STORAGE_KEY, language)" in runtime
    assert "window.dispatchEvent(new CustomEvent('admin-language-changed'" in runtime
    assert "console.debug(`[admin-i18n] missing ${currentLanguage}: ${key}`)" in runtime


def test_i18n_javascript_is_served_with_an_executable_mime_type():
    from fastapi.testclient import TestClient

    from admin.admin_server import app

    response = TestClient(app).get("/static/i18n.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")


def test_all_navigation_links_use_semantic_i18n_keys():
    index = read_admin_client_source()
    nav = re.search(r"<nav\b[^>]*>(.*?)</nav>", index, re.S)
    assert nav is not None

    links = re.findall(r'<a\b[^>]*data-page="[^"]+"[^>]*>(.*?)</a>', nav.group(1), re.S)
    assert links
    assert all('data-i18n="nav.' in link for link in links)
    assert 'id="admin-language-select"' in index
    assert 'id="auth-language-select"' in index
    assert index.count("data-language-select") == 2
    assert 'data-action-args=\'["observation-center"]\'' in index
    assert 'data-action-args=\'["operations-center"]\'' in index
    assert 'data-page="observe-probe"' not in index


def test_status_page_and_feature_flags_use_semantic_i18n_keys():
    index = read_admin_client_source()
    runtime = I18N.read_text(encoding="utf-8")
    status = read_admin_page("status")
    runtime_config = "".join(read_admin_page(page) for page in ("runtime-config", "network-config", "conversation-settings", "device-policy", "output-settings", "personal-settings"))
    tts_config = read_admin_page("tts-config")
    routing = read_admin_page("model-routing")

    for key in (
        "status.title",
        "status.data_environment.title",
        "status.model.title",
        "status.tts.summary_title",
        "status.attention.title",
    ):
        assert f'data-i18n="{key}"' in status

    for key in (
        "status.proxy.title",
        "status.context.title",
        "status.llm.title",
        "status.screen.title",
        "status.relay.title",
        "status.sticker.title",
        "status.pronoun.title",
    ):
        assert f'data-i18n="{key}"' in runtime_config

    for key in (
        "tts_config.title",
        "status.tts.provider",
        "status.tts.provider_openai_compatible",
        "status.tts.provider_params",
        "status.tts.api_url",
    ):
        assert f'data-i18n="{key}"' in tts_config

    for key in ("status.vision.title", "status.phone_vision.title"):
        assert f'data-i18n="{key}"' in routing

    assert "t('flag.' + name, item.label)" in index
    for flag in (
        "qq",
        "mail",
        "visual_perception",
        "spend",
        "practice",
        "action_trace",
        "mcp_servers",
        "fs_access",
        "anti_collapse",
        "coplay",
        "toy_autogrow",
        "web_autosearch",
        "performance_mapping",
        "private_exchange",
    ):
        assert f"'flag.{flag}'" in runtime

    assert 'id="vision-connections-body"' in routing
    assert 'data-action="saveImageRoute"' in routing


def test_group_arbiter_private_exchange_and_prompt_inspector_are_localized():
    index = read_admin_client_source()
    runtime = I18N.read_text(encoding="utf-8")
    page = read_admin_page("observe-group-arbiter")
    for key in ("group.title", "group.subtitle", "group.stage", "common.refresh"):
        assert f'data-i18n="{key}"' in page

    for key in (
        "group.trace",
        "group.impressions",
        "group.private",
        "group.private_none",
        "group.prompt",
        "group.prompt_subtitle",
        "group.prompt_pruned",
        "group.prompt_kept",
        "group.prompt_no_layers",
    ):
        assert f"t('{key}'" in index
        assert f"'{key}'" in runtime

    assert "origin.origin === 'stage' && origin.group_id === groupId" in index


def test_setup_page_and_common_empty_state_are_localized():
    index = read_admin_client_source()
    runtime = I18N.read_text(encoding="utf-8")
    page = "".join(read_admin_page(name) for name in ("setup", "embedding-config", "mail-config", "personal-settings", "diary-config", "coplay-config"))

    for key in (
        "setup.title",
        "setup.base.title",
        "setup.owner.description",
        "setup.embedding.title",
        "setup.optional_models.title",
        "setup.mail.title",
        "setup.anniversaries.title",
        "setup.diary.title",
        "setup.coplay.title",
    ):
        assert f'data-i18n="{key}"' in page
        assert runtime.count(f"'{key}'") == 2

    assert page.count('data-i18n="common.save"') == 8
    assert 'data-i18n-placeholder="setup.secret.keep"' in page
    assert "t('setup.base.saved'" in index
    assert "t('setup.mail.saved'" in index
    assert "t('setup.diary.saved'" in index
    assert "t('setup.coplay.saved'" in index
    assert "label || t('common.no_data', '暂无数据')" in index


def test_chinese_and_english_dictionaries_have_identical_semantic_keys():
    runtime = I18N.read_text(encoding="utf-8")

    chinese = _dictionary_keys(runtime, "zh-CN")
    english = _dictionary_keys(runtime, "en")

    assert chinese == english
    assert len(chinese) >= 900
    assert all(not re.search(r"[\u3400-\u9fff]", key) for key in chinese)


def test_every_dream_ablation_layer_has_bilingual_semantic_key():
    runtime = I18N.read_text(encoding="utf-8")
    chinese = _dictionary_keys(runtime, "zh-CN")
    english = _dictionary_keys(runtime, "en")
    from core.dream.dream_prompt_ablation import KNOWN_LAYERS

    expected = {f"observe.dream_prompt.layer.{layer}" for layer, _desc in KNOWN_LAYERS}
    assert expected <= chinese
    assert expected <= english


def test_every_static_visible_chinese_string_is_localized_or_authored_content():
    runtime = I18N.read_text(encoding="utf-8")
    parser = _VisibleChineseParser()
    for source in [INDEX, *sorted(PAGES.glob("*.html"))]:
        parser.feed(source.read_text(encoding="utf-8"))
    translated_values = _chinese_dictionary_values(runtime)
    allowed_authored_values = {"叶瑄", "中文"}

    missing = sorted(
        {
            re.sub(r"\s+", " ", value).strip()
            for value in parser.values
            if re.sub(r"\s+", " ", value).strip()
            not in translated_values | allowed_authored_values
        }
    )

    assert missing == [], "\n".join(value.encode("unicode_escape").decode("ascii") for value in missing)


def test_every_page_fragment_has_complete_i18n_coverage_in_both_languages():
    """Keep lazy fragments from silently restoring a previous-language label."""
    from fastapi.testclient import TestClient

    from admin.admin_server import app

    runtime = I18N.read_text(encoding="utf-8")
    dictionaries = {
        "zh-CN": _dictionary_keys(runtime, "zh-CN"),
        "en": _dictionary_keys(runtime, "en"),
    }
    assert dictionaries["zh-CN"] == dictionaries["en"]

    client = TestClient(app)
    translated_values = _chinese_dictionary_values(runtime)
    allowed_authored_values = {"叶瑄", "中文"}
    attribute_pattern = re.compile(r'data-i18n(?:-(?:placeholder|aria-label))?="([^"]+)"')

    for fragment in sorted(PAGES.glob("*.html")):
        response = client.get(f"/static/pages/{fragment.name}")
        assert response.status_code == 200, fragment.name
        assert response.text.strip(), fragment.name

        keys = attribute_pattern.findall(response.text)
        missing_keys = {
            language: sorted(set(keys) - dictionary)
            for language, dictionary in dictionaries.items()
        }
        assert not missing_keys["zh-CN"], f"{fragment.name}: {missing_keys['zh-CN']}"
        assert not missing_keys["en"], f"{fragment.name}: {missing_keys['en']}"

        parser = _VisibleChineseParser()
        parser.feed(response.text)
        untranslated = {
            re.sub(r"\s+", " ", value).strip()
            for value in parser.values
            if re.sub(r"\s+", " ", value).strip()
            not in translated_values | allowed_authored_values
        }
        assert not untranslated, f"{fragment.name}: {sorted(untranslated)}"


def test_legacy_bridge_localizes_dynamic_dom_and_protects_raw_content():
    index = read_admin_client_source()
    runtime = I18N.read_text(encoding="utf-8")

    assert "function translateUiText(value, allowFragments=false)" in runtime
    assert "function applyLegacyI18n(root)" in runtime
    assert "legacyPatterns.sort((a, b) => b.literalLength - a.literalLength" in runtime
    assert "new MutationObserver(records =>" in runtime
    assert "record.addedNodes?.forEach(applyLegacyI18n)" in runtime
    assert "window.prompt =" in runtime
    assert "window.confirm =" in runtime
    assert "window.alert =" in runtime
    assert ".i18n-raw" in runtime
    assert 'class="i18n-raw"' in index
    assert "AdminI18n.translateUiText(msg)" in index

    for family in (
        "dynamic.character.",
        "dynamic.dream.",
        "dynamic.facts.",
        "dynamic.logs.",
        "dynamic.memory.",
        "dynamic.observe.",
        "dynamic.routing.",
        "dynamic.scheduler.",
        "dynamic.tokens.",
        "dynamic.users.",
        "dynamic.vector.",
    ):
        assert runtime.count(f"'{family}") >= 2


def test_updated_settings_fragments_have_complete_translation_keys():
    runtime = I18N.read_text(encoding="utf-8")
    values = _chinese_dictionary_values(runtime)
    for page in ("model-routing", "scheduler", "autonomy-settings"):
        content = read_admin_page(page)
        parser = _VisibleChineseParser()
        parser.feed(content)
        missing = {re.sub(r"\s+", " ", value).strip() for value in parser.values} - values
        assert not missing, (page, sorted(missing))
