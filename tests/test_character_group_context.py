from core.memory import group_context


def test_group_cache_has_character_ownership_and_no_legacy_fallback(sandbox):
    legacy = sandbox.legacy_group_context() / 'group.json'
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('[{"content":"old shared"}]', encoding='utf-8')
    assert group_context.get_recent('group', char_id='fresh') == []
    assert not sandbox.group_context(char_id='fresh').exists()
    group_context.append('group', 'sender', 'alpha only', char_id='alpha')
    assert group_context.get_recent('group', char_id='alpha')[0]['content'] == 'alpha only'
    assert group_context.get_recent('group', char_id='beta') == []
    group_context.clear('group', char_id='beta')
    assert group_context.get_recent('group', char_id='alpha')[0]['content'] == 'alpha only'
    assert 'old shared' in legacy.read_text(encoding='utf-8')
