"""Regression coverage for persisted admin navigation groups."""

from pathlib import Path
from html.parser import HTMLParser
import json

import pytest


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
    assert nav.targets == pages - {'integrations', 'agent-runtime-browser'}
    guide = Links()
    guide.feed((static / 'pages/guide.html').read_text(encoding='utf-8'))
    assert guide.targets == pages - {'integrations', 'guide', 'overview'}


@pytest.mark.browser
def test_admin_navigation_login_configuration_and_translations_in_browser():
    from tests.run_admin_browser import main

    assert main() == 0
