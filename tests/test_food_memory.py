import json
import time
from unittest.mock import AsyncMock

import pytest
from core import food_memory as food


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setattr(food, 'get_config', lambda: {'food_memory': {'enabled': True}})


def job(message_id, text, ts=1000):
    return {'message_id': message_id, 'text': text, 'ts': ts}


def event(name, quote, kind='rating', value='like'):
    return {'name': name, 'quote': quote, 'kind': kind, 'value': value}


def test_explicit_overwrite_is_ordered_by_source_not_processing(sandbox):
    food.apply('u', 'c', job('new', '我不喜欢水饺', 2000), [event('水饺', '我不喜欢水饺', value='dislike')])
    food.apply('u', 'c', job('old', '我喜欢水饺', 1000), [event('水饺', '我喜欢水饺')])
    view = food.snapshot('u', 'c')
    assert view['items'][0]['evaluation'] == 'dislike'
    assert view['items'][0]['evaluation_source'] == 'new'
    assert food.snapshot('u', 'other')['items'] == []
    assert food.snapshot('other', 'c')['items'] == []


@pytest.mark.parametrize('quote', ['我想吃水饺', '我点了水饺', '朋友吃了水饺', '我没吃过水饺', '如果吃了水饺会怎样'])
def test_plans_third_party_and_negation_do_not_count(quote):
    assert food.validate(event('水饺', quote, 'ate'), quote) is None


def test_meal_counts_are_idempotent_and_conservative(sandbox):
    first = job('first', '午餐吃了水饺')
    ate = event('水饺', first['text'], 'ate')
    food.apply('u', 'c', first, [ate, ate])
    food.apply('u', 'c', first, [ate])
    food.apply('u', 'c', job('retell', '午餐吃了水饺', 1001), [event('水饺', '午餐吃了水饺', 'ate')])
    food.apply('u', 'c', job('dinner', '晚餐吃了水饺', 1002), [event('水饺', '晚餐吃了水饺', 'ate')])
    item = food.snapshot('u', 'c')['items'][0]
    assert item['recorded_eaten_count'] == 2
    assert item['evaluation'] == 'unknown'


def test_temporary_refusal_does_not_change_rating(sandbox):
    food.apply('u', 'c', job('a', '我喜欢水饺'), [event('水饺', '我喜欢水饺')])
    food.apply('u', 'c', job('b', '今天不想吃水饺', 2000), [event('水饺', '今天不想吃水饺', 'temporary')])
    item = food.snapshot('u', 'c')['items'][0]
    assert item['evaluation'] == 'like'
    assert item['temporary_refusal_at'] == 2000


def test_taste_is_explicit_and_projection_has_no_food_names(sandbox):
    assert food.validate(event('辣', '水饺太辣', 'taste', 'dislike'), '水饺太辣') is None
    text = '我不喜欢辣的，但我喜欢水饺'
    food.apply('u', 'c', job('a', text), [event('辣', text, 'taste', 'dislike')])
    summary = food.taste_summary('u', 'c')
    assert '不喜欢' in summary and '水饺' not in summary


def test_hallucinated_quote_cannot_write():
    assert food.validate(event('水饺', '我喜欢水饺'), '今天吃了米饭') is None


def test_evaluation_must_belong_to_same_clause():
    text = '我不喜欢米饭，但我喜欢水饺'
    assert food.validate(event('水饺', text, value='dislike'), text) is None
    assert food.validate(event('水饺', text, value='like'), text)['value'] == 'like'


def test_migration_is_dry_run_first_and_idempotent(sandbox, monkeypatch):
    from scripts import migrate_food_memory as migration
    monkeypatch.setattr('core.memory.short_term.load', lambda *a, **k: [
        {'role': 'assistant', 'content': '吃了水饺', 'timestamp': 1000},
        {'role': 'user', 'content': '吃了水饺', 'timestamp': 1000},
        {'role': 'user', 'content': '吃了米饭', 'timestamp': 1001, 'asr_low_confidence': True}])
    preview = migration.migrate('u', 'c')
    assert preview['candidate_count'] == 1 and not preview['backup_created']
    assert food.snapshot('u', 'c')['pending'] == 0
    assert migration.migrate('u', 'c', apply=True)['backup_created']
    migration.migrate('u', 'c', apply=True)
    assert food.snapshot('u', 'c')['pending'] == 1


@pytest.mark.asyncio
async def test_durable_inbox_retry_and_route(sandbox, monkeypatch):
    food.enqueue('u', 'c', 'turn1', '我不喜欢水饺')
    call = AsyncMock(side_effect=[RuntimeError('offline'), json.dumps([event('水饺', '我不喜欢水饺', value='dislike')], ensure_ascii=False)])
    monkeypatch.setattr('core.llm_client.chat', call)
    await food.process_pending('u', 'c')
    assert food.snapshot('u', 'c')['pending'] == 1
    await food.process_pending('u', 'c')
    assert food.snapshot('u', 'c')['pending'] == 0
    assert food.snapshot('u', 'c')['items'][0]['evaluation'] == 'dislike'
    assert call.call_args.kwargs['call_category'] == 'food_extract'
    assert call.call_args.kwargs['char_id'] == 'c'


def test_food_facts_are_not_ambient_profile(sandbox):
    from core.memory.user_profile import select_for_prompt
    profile = {'important_facts': [{'text': '常点某种水饺', 'tag': 'pref.food', 'ts': time.time()}]}
    assert '水饺' not in select_for_prompt(profile, ['food'])['pref_text']


def test_controls_scope_and_hot_reload(sandbox, tmp_path, monkeypatch):
    import yaml
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from admin import auth
    from admin.routers import food_memory as routes
    path = tmp_path / 'config.yaml'
    path.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(routes, 'CONFIG_FILE', path)
    monkeypatch.setattr('core.config_loader.reload_config', lambda: None)
    monkeypatch.setattr(food, 'get_config', lambda: yaml.safe_load(path.read_text(encoding='utf-8')))
    monkeypatch.setattr('core.model_registry.resolve_category_info', lambda *a, **k: {'effective_preset': 'tiny'})
    monkeypatch.setattr(auth, 'resolve_token', lambda token: auth.TokenInfo('fixture', frozenset({token})))
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    assert client.put('/settings/food-memory', headers={'Authorization': 'Bearer state.read'}, json={'enabled': False}).status_code == 403
    result = client.put('/settings/food-memory', headers={'Authorization': 'Bearer admin'}, json={'enabled': False})
    assert result.status_code == 200 and result.json()['effective'] is False
    assert food.enqueue('u', 'c', 'new', '吃了水饺') is False
