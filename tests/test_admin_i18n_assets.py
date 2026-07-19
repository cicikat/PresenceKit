from pathlib import Path
import re


ROOT = Path(__file__).parents[1]
INDEX = ROOT / "admin" / "static" / "index.html"
I18N = ROOT / "admin" / "static" / "i18n.js"


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
