import asyncio
import json
from core import character_data, user_relation
from core.memory import user_facts
from admin.routers import user_data


def test_traits_are_portable_scoped_and_empty_for_new_character(sandbox, monkeypatch):
    monkeypatch.chdir(sandbox._base)
    assert character_data.load_traits('alpha') == []
    character_data.save_traits('alpha', [{'id':'patient', 'keywords':['listen']}])
    assert character_data.load_traits('alpha')[0]['id'] == 'patient'
    assert character_data.load_traits('beta') == []
    snapshot=asyncio.run(user_data.character_data_inventory('alpha', auth=None))
    assert snapshot['char_id']=='alpha'
    assert snapshot['traits'][0]['id']=='patient'
    assert any(row['id']=='trait_state' for row in snapshot['entries'])


def test_user_facts_prompt_and_pronoun_never_cross_characters(sandbox):
    user_facts.save_user_facts('owner', {'pronoun':'TA', 'known_projects':['alpha-only']}, char_id='alpha')
    assert 'alpha-only' in user_facts.format_for_prompt('owner', char_id='alpha')
    assert user_facts.format_for_prompt('owner', char_id='beta') == ''
    assert user_facts.get_user_pronoun('owner', char_id='alpha') == 'TA'
    assert user_facts.get_user_pronoun('owner', char_id='beta') != 'TA'


def test_relations_do_not_inherit_other_character_or_global_prompt(sandbox):
    path=sandbox.character_user_relations(char_id='alpha')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps({'relations':{'owner':{'nickname':'alpha-only','extra_prompt':'private'}}}).encode())
    assert user_relation.get_relation('owner', char_id='alpha')['nickname']=='alpha-only'
    assert user_relation.get_relation('owner', char_id='beta')['nickname'] is None
    assert not user_relation.has_configured_relation('owner', char_id='beta')


def test_legacy_facts_assignment_is_explicit_dry_run_and_preserves_source(sandbox):
    from scripts.migrate_character_user_facts import migrate
    source=sandbox.legacy_user_facts('owner')
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'{"timezone":"UTC"}')
    original=source.read_bytes()
    assert migrate('owner','alpha')['status']=='dry_run'
    assert user_facts.load_user_facts('owner', char_id='alpha')=={}
    assert migrate('owner','alpha',apply=True)['status']=='imported'
    assert migrate('owner','alpha',apply=True)['status']=='already_imported'
    assert user_facts.load_user_facts('owner', char_id='beta')=={}
    assert source.read_bytes()==original
