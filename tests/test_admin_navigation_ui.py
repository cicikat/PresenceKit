"""Regression coverage for persisted admin navigation groups."""

from pathlib import Path
from html.parser import HTMLParser
import json

from admin_static_assets import read_admin_client_source


STYLE = Path(__file__).parents[1] / "admin" / "static" / "style.css"


def test_sidebar_and_guide_cover_all_current_pages():
    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.targets = set()

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if attrs.get('data-action') == 'goto':
                self.targets.add(json.loads(attrs['data-action-args'])[0])

    static = STYLE.parent
    shell = (static / 'index.html').read_text(encoding='utf-8')
    nav = Links()
    nav.feed(shell.split('<nav id="admin-navigation">')[1].split('</nav>')[0])
    pages = {p.stem for p in (static / 'pages').glob('*.html')}
    # Integrations is frozen; it remains a compatibility deep link only.
    assert nav.targets == pages - {'integrations'}
    guide = Links()
    guide.feed((static / 'pages/guide.html').read_text(encoding='utf-8'))
    assert guide.targets == pages - {'integrations', 'guide', 'overview'}


def test_navigation_restore_discovers_all_rendered_groups():
    source = read_admin_client_source()

    assert "document.querySelectorAll('[id^=\"navgroup-\"]')" in source
    assert "const key = group.id.slice('navgroup-'.length);" in source
    assert "for(const key of ['create','ops','state','observe'])" not in source
    for page in ('feature-center', 'service-center', 'creation-center', 'observation-center', 'operations-center'):
        assert f'data-action-args=\'["{page}"]\'' in source


def test_navigation_restores_the_current_page_within_a_browser_tab_session():
    source = read_admin_client_source()

    assert "const ACTIVE_PAGE_SESSION_KEY = 'admin_active_page';" in source
    assert 'sessionStorage.setItem(ACTIVE_PAGE_SESSION_KEY, page);' in source
    assert "getRememberedPage() || 'overview'" in source
    assert 'clearRememberedPage();' in source
    assert "setupStatus && setupStatus.needs_setup\n      ? 'setup'" in source


def test_control_center_is_the_default_page_and_keeps_settings_contextual():
    source = read_admin_client_source()
    index = (Path(__file__).parents[1] / "admin" / "static" / "index.html").read_text(encoding="utf-8")
    overview = (Path(__file__).parents[1] / "admin" / "static" / "pages" / "overview.html").read_text(encoding="utf-8")
    i18n = (Path(__file__).parents[1] / "admin" / "static" / "i18n.js").read_text(encoding="utf-8")

    assert 'id="page-overview" data-page-fragment="overview"' in index
    assert 'data-page="overview"' in index
    assert "const ADMIN_PAGE_CONTEXT = Object.freeze({" in source
    assert "function decoratePageContext(page, container)" in source
    assert "'page_context.related': 'Related settings'" in i18n
    assert 'data-action-args=\'["setup"]\'' in overview
    assert 'data-action-args=\'["auth-tokens"]\'' in overview


def test_mobile_navigation_uses_a_shell_managed_drawer():
    source = read_admin_client_source()
    style = STYLE.read_text(encoding="utf-8")

    assert 'id="nav-menu-toggle"' in source
    assert 'aria-controls="admin-navigation"' in source
    assert 'id="nav-backdrop"' in source
    assert "scope.matches?.('[data-action]') ? [scope] : []" in source
    assert "const MOBILE_NAV_MEDIA = window.matchMedia('(max-width: 767px)');" in source
    assert "app.classList.toggle('admin-sidebar-open', shouldOpen);" in source
    assert "document.body.classList.toggle('admin-sidebar-open', shouldOpen);" in source
    assert "if (event.target.closest('a[data-page]')) closeSidebar();" in source
    assert "if (event.key === 'Escape') closeSidebar();" in source
    assert "MOBILE_NAV_MEDIA.addEventListener('change', () => closeSidebar());" in source
    assert '@media (max-width: 767px)' in style
    assert 'transform: translateX(-105%);' in style
    assert 'width: min(82vw, 320px);' in style
