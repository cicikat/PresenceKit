from tests.fixtures.public_assets import TEST_CHAR_ID
from pathlib import Path
import re

from admin_static_assets import PAGES, read_admin_client_source


STATIC = Path(__file__).parents[1] / "admin" / "static"


def test_admin_html_is_a_shell_with_ordered_plain_static_assets():
    index = (STATIC / "index.html").read_text(encoding="utf-8")

    assert re.search(
        r'<link rel="stylesheet" href="/static/style\.css\?v=[^"]+">', index
    )
    assert "<style>" not in index
    assert re.search(r'<script src="/static/i18n\.js(?:\?[^" ]*)?"></script>', index)
    scripts = re.findall(r'<script src="/static/js/([^"?]+)(?:\?[^" ]*)?"></script>', index)
    assert scripts[0] == "core.js"
    assert len(scripts) >= 2
    assert all((STATIC / "js" / script).is_file() for script in scripts)
    assert "<script>" not in index
    assert "type=\"module\"" not in index


def test_every_page_is_a_lazy_fragment_with_its_original_placeholder():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    fragments = sorted(PAGES.glob("*.html"))
    fragment_names = {fragment.stem for fragment in fragments}
    placeholder_names = set(re.findall(r'data-page-fragment="([^"]+)"', index))
    nav_names = set(re.findall(r'<a[^>]+data-page="([^"]+)"', index))
    source = read_admin_client_source()
    loader_block = re.search(r"const loaders = \{(.*?)\n  \};", source, re.S)
    assert loader_block, "core.js must publish the page loader map"
    loader_names = set(re.findall(r"^\s*['\"]?([\w-]+)['\"]?\s*:", loader_block.group(1), re.M))

    assert fragment_names == placeholder_names
    assert nav_names <= fragment_names
    # integrations is an intentional frozen compatibility deep link; every
    # other fragment must have an explicit loader entry.
    assert fragment_names - loader_names == {"integrations"}
    assert loader_names <= fragment_names
    for fragment in fragments:
        page = fragment.stem
        assert f'id="page-{page}" data-page-fragment="{page}"' in index
        assert fragment.read_text(encoding="utf-8").strip()

    assert "async function loadPageFragment(page, {reload = false} = {})" in source
    assert "fetch(`/static/pages/${encodeURIComponent(page)}.html?v=${ADMIN_UI_FRAGMENT_VERSION}`)" in source
    assert "window.AdminI18n?.applyI18n(container)" in source


def test_tools_dynamic_preset_buttons_are_bound_after_rendering():
    tools_source = (STATIC / "js" / "tools.js").read_text(encoding="utf-8")
    assert "root.innerHTML =" in tools_source
    assert "bindPageActions(root);" in tools_source


def test_tools_page_exposes_editable_global_default_mode():
    page = (PAGES / "tools.html").read_text(encoding="utf-8")
    tools_source = (STATIC / "js" / "tools.js").read_text(encoding="utf-8")

    assert 'id="tools-save-preset"' in page
    assert "global_default_tools" in tools_source
    assert "saveGlobalToolDefault" in tools_source


def test_removed_legacy_pet_and_chat_panels_have_no_static_entrypoint():
    index = (STATIC / "index.html").read_text(encoding="utf-8")

    for page in ("pet", TEST_CHAR_ID):
        assert not (PAGES / f"{page}.html").exists()
        assert f'data-page="{page}"' not in index
        assert f'data-page-fragment="{page}"' not in index
    assert "pet-chat.js" not in index


def test_removed_legacy_pet_api_and_storage_module_have_no_entrypoint():
    from admin.routers.system import router

    route_paths = {route.path for route in router.routes}
    assert not route_paths.intersection({"/pet", "/pet/interact"})
    assert not (Path(__file__).parents[1] / "core" / "pet.py").exists()


def test_scheduler_split_keeps_its_runtime_constants_and_ledger_loader():
    scheduler = (STATIC / "js" / "scheduler.js").read_text(encoding="utf-8")

    assert "const SC_TRIGGER_LABELS = {" in scheduler
    assert "const SC_BOOL_FIELDS = [" in scheduler
    assert "async function loadProactiveLedger()" in scheduler
    assert "api('GET', '/scheduler/proactive-ledger')" in scheduler


def test_page_fragments_are_served_as_static_html():
    from fastapi.testclient import TestClient

    from admin.admin_server import app

    client = TestClient(app)
    for page in ("status", "observe-tools", "observe-resource-completeness", "observe-runtime-signals"):
        response = client.get(f"/static/pages/{page}.html")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")


def test_admin_shell_and_static_assets_revalidate_on_refresh():
    from fastapi.testclient import TestClient

    from admin.admin_server import app

    client = TestClient(app)
    for path in ("/", "/static/js/core.js", "/static/pages/model-routing.html"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache, must-revalidate"


def test_split_pages_have_no_inline_style_or_onclick_and_actions_are_bound():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    html = "\n".join([index, *(path.read_text(encoding="utf-8") for path in PAGES.glob("*.html"))])
    source = read_admin_client_source()
    css = (STATIC / "style.css").read_text(encoding="utf-8")

    assert 'onclick="' not in html
    setup_source = (STATIC / "js" / "setup.js").read_text(encoding="utf-8")
    assert 'onclick="' not in setup_source
    assert 'style="' not in html
    assert "function bindPageActions(scope)" in source
    assert "element.addEventListener('click', _runAction)" in source
    assert "function bindShellActions()" in source

    actions = set(re.findall(r'data-action="([^"]+)"', html))
    assert actions
    for action in actions - {"focus-element"}:
        assert re.search(rf"(?:async )?function {re.escape(action)}\(", source), action

    utility_classes = set(re.findall(r"\b(admin-inline-\d+)\b", html))
    assert utility_classes
    assert all(f".{name}{{" in css for name in utility_classes)


def test_migrated_utility_css_has_no_orphaned_rules():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    source = read_admin_client_source()

    defined = set(re.findall(r"\.((?:admin-inline)-\d+)\{", css))
    used = set(re.findall(r"\b(admin-inline-\d+)\b", source))
    assert defined <= used
