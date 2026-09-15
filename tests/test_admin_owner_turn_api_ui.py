from admin_static_assets import STATIC, read_admin_client_source, read_admin_page


def test_owner_turn_page_is_registered_and_cache_busted():
    source = read_admin_client_source()
    page = read_admin_page("owner-turn-api")
    for marker in (
        'data-page="owner-turn-api"',
        'id="page-owner-turn-api"',
        'data-page-fragment="owner-turn-api"',
        '/static/js/owner-turn-api.js?v=brief-173-owner-turn-1',
        "ADMIN_UI_FRAGMENT_VERSION = 'brief-251-thinking-cache-mcp-1'",
        'data-action="ownerTurnSelectTab"',
        'owner-turn-tab-api',
        'owner-turn-tab-observability',
        'owner-turn-tab-deployment',
    ):
        assert marker in source or marker in page


def test_owner_turn_page_uses_existing_redacted_contract_surfaces():
    source = read_admin_client_source()
    page = read_admin_page("owner-turn-api")
    for marker in (
        '/v1/owner/turns',
        '/auth/tokens',
        '/observability/owner-turns',
        '/observability/deployment-capabilities',
        '/system/deployment-preflight',
        '/integrations/diary/sync/status',
        'copyOwnerTurnTemplate',
        'owner-input',
        'hash_prefix',
        'data-action="goto"',
    ):
        assert marker in source or marker in page
    assert 'onclick=' not in page
    assert 'localStorage' not in (STATIC / 'js' / 'owner-turn-api.js').read_text(encoding='utf-8')


def test_owner_turn_page_has_bilingual_keys_for_visible_controls():
    runtime = (STATIC / 'i18n.js').read_text(encoding='utf-8')
    for key in (
        'owner_turn.title',
        'owner_turn.api.token_title',
        'owner_turn.obs.title',
        'owner_turn.deploy.title',
        'nav.owner_turn_api',
        'page_context.owner-turn-api.purpose',
    ):
        assert key in runtime
