from pathlib import Path
from html.parser import HTMLParser
import re


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
    index = INDEX.read_text(encoding="utf-8")
    runtime = I18N.read_text(encoding="utf-8")

    assert '<script src="/static/i18n.js"></script>' in index
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
    index = INDEX.read_text(encoding="utf-8")
    nav = re.search(r"<nav>(.*?)</nav>", index, re.S)
    assert nav is not None

    links = re.findall(r'<a\b[^>]*data-page="[^"]+"[^>]*>(.*?)</a>', nav.group(1), re.S)
    assert links
    assert all('data-i18n="nav.' in link for link in links)
    assert 'id="admin-language-select"' in index
    assert 'id="auth-language-select"' in index
    assert index.count("data-language-select") == 2


def test_status_page_and_feature_flags_use_semantic_i18n_keys():
    index = INDEX.read_text(encoding="utf-8")
    runtime = I18N.read_text(encoding="utf-8")
    status = re.search(
        r'<div class="page active" id="page-status">(.*?)'
        r'<div class="page" id="page-users">',
        index,
        re.S,
    )
    assert status is not None

    for key in (
        "status.title",
        "status.feature_switches",
        "status.registered_tools",
        "status.proxy.title",
        "status.context.title",
        "status.llm.title",
        "status.vision.title",
        "status.screen.title",
        "status.relay.title",
        "status.tts.title",
        "status.pronoun.title",
    ):
        assert f'data-i18n="{key}"' in status.group(1)

    assert "t('flag.' + name, item.label)" in index
    for flag in (
        "qq",
        "mail",
        "visual_perception",
        "spend",
        "practice",
        "action_trace",
        "intent_reflex",
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

    assert "https://aistudio.google.com/app/apikey" in status.group(1)
    assert "https://open.bigmodel.cn/usercenter/apikeys" in status.group(1)


def test_group_arbiter_private_exchange_and_prompt_inspector_are_localized():
    index = INDEX.read_text(encoding="utf-8")
    runtime = I18N.read_text(encoding="utf-8")
    page = re.search(
        r'<div class="page" id="page-observe-group-arbiter">(.*?)'
        r'<div class="page" id="page-observe-memory-summary">',
        index,
        re.S,
    )
    assert page is not None
    for key in ("group.title", "group.subtitle", "group.stage", "common.refresh"):
        assert f'data-i18n="{key}"' in page.group(1)

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
    index = INDEX.read_text(encoding="utf-8")
    runtime = I18N.read_text(encoding="utf-8")
    page = re.search(
        r'<div class="page" id="page-setup">(.*?)'
        r'<div class="page active" id="page-status">',
        index,
        re.S,
    )
    assert page is not None

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
        assert f'data-i18n="{key}"' in page.group(1)
        assert runtime.count(f"'{key}'") == 2

    assert page.group(1).count('data-i18n="common.save"') == 7
    assert 'data-i18n-placeholder="setup.secret.keep"' in page.group(1)
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


def test_every_static_visible_chinese_string_is_localized_or_authored_content():
    index = INDEX.read_text(encoding="utf-8")
    runtime = I18N.read_text(encoding="utf-8")
    parser = _VisibleChineseParser()
    parser.feed(index)
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


def test_legacy_bridge_localizes_dynamic_dom_and_protects_raw_content():
    index = INDEX.read_text(encoding="utf-8")
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
        "dynamic.pet.",
        "dynamic.routing.",
        "dynamic.scheduler.",
        "dynamic.tokens.",
        "dynamic.users.",
        "dynamic.vector.",
    ):
        assert runtime.count(f"'{family}") >= 2
