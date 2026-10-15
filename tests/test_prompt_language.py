"""Language changes framework annotations, never authored data or contracts."""
import re
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import prompt_language as locale


@pytest.fixture
def english(monkeypatch):
    config = {'prompt_language': 'en', 'scheduler': {'owner_id': 'owner'}}
    monkeypatch.setattr('core.config_loader.get_config', lambda: config)
    return config


def test_language_default_and_roundtrip(english):
    assert locale.text('真实数据') == 'Observed data'
    english['prompt_language'] = 'zh'
    assert locale.text('真实数据') == '真实数据'
    english['prompt_language'] = 'invalid'
    assert locale.language() == 'zh'


def test_primary_prompt_preserves_authored_sources(english, sandbox):
    from core import prompt_builder
    from core.character_loader import Character
    character = Character(name='Fixture', system_prompt='角色卡原文：不要翻译。', description='设定原文。')
    messages, _ = prompt_builder.build(
        character, 'owner', '用户原文：不要翻译。',
        [{'role': 'assistant', 'content': '历史原文。'}], {}, {}, [], tags=set(),
        current_time='2026-10-15 12:00',
    )
    identity = next(m['content'] for m in messages if m.get('_layer') == '1_system_prompt')
    assert 'You are Fixture' in identity
    assert '角色卡原文：不要翻译。' in identity
    assert any('用户原文：不要翻译。' in m['content'] for m in messages)
    assert any('历史原文。' in m['content'] for m in messages)
    assert any('设定原文。' in m['content'] for m in messages)
    assert any('[Current time]' in m['content'] for m in messages)
    authored = ('角色卡原文：不要翻译。', '用户原文：不要翻译。', '历史原文。', '设定原文。')
    for message in messages:
        content = message['content']
        for original in authored:
            content = content.replace(original, '')
        assert not re.search(r'[\u4e00-\u9fff]', content), (message.get('_layer'), content)


def test_tool_annotations_are_english_but_schema_values_unchanged(english):
    from core.tool_dispatcher import _TOOL_REGISTRY
    for name, info in _TOOL_REGISTRY.items():
        if name.startswith('mcp__'):
            continue
        original = info.get('description', '')
        row = locale._tools().get(name)
        assert row and row['source'] == original, f'{name}: translation needs review'
        assert not re.search(r'[\u4e00-\u9fff]', locale.tool_description(name, original))
        params = info.get('parameters') or {}
        translated = locale.tool_parameters(params)

        def strip_annotations(value):
            if isinstance(value, dict):
                return {k: strip_annotations(v) for k, v in value.items() if k != 'description'}
            if isinstance(value, list):
                return [strip_annotations(v) for v in value]
            return value

        assert strip_annotations(translated) == strip_annotations(params)

        def descriptions(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == 'description' and isinstance(item, str):
                        assert not re.search(r'[\u4e00-\u9fff]', item), (name, item)
                    else:
                        descriptions(item)
            elif isinstance(value, list):
                for item in value:
                    descriptions(item)
        descriptions(translated)


def test_external_tools_and_enum_values_are_preserved(english):
    schema = {'type': 'object', 'properties': {'mode': {
        'type': 'string', 'enum': ['中文值'], 'default': '中文值', 'description': '第三方原文。',
    }}}
    original = deepcopy(schema)
    assert locale.tool_parameters(schema) == original
    assert locale.tool_description('custom_external', '第三方原文。') == '第三方原文。'


def test_tool_discovery_and_grounding_in_english(english):
    from core.tool_discovery import ToolDiscovery
    from core.tools.tool_result import frame_tool_message
    from core.tool_grounding import has_successful_tool_message
    discovery = ToolDiscovery([{'function': {'name': 'fixture', 'description': 'a fixture'}}], {'fixture': {'category': 'memory'}})
    assert 'Load ' in discovery.schemas()[0]['function']['description']
    assert 'recall the past' in discovery.routing_hint()
    result = frame_tool_message('工具原文：保持不变。')
    assert '工具原文：保持不变。' in result
    assert 'untrusted' in result
    assert has_successful_tool_message([{'role': 'tool', 'content': result}])
    assert not has_successful_tool_message([{'role': 'tool', 'content': frame_tool_message('failed', validity='execution_failed')}])
    assert not has_successful_tool_message([{'role': 'tool', 'content': frame_tool_message('generated this turn', validity='execution_failed')}])


def test_dream_policies_are_english_without_changing_authored_world(english, sandbox, monkeypatch):
    from types import SimpleNamespace
    from core.dream.dream_prompt import build_dream_prompt
    monkeypatch.setattr('core.dream.world_loader.load_world', lambda _: SimpleNamespace(ruleset='世界原文。', mes_example='自写示例。'))
    character = SimpleNamespace(name='Fixture', gender='neutral', system_prompt='角色原文。', description='', personality='', presence_ext={})
    messages = build_dream_prompt(character, 'owner', '用户原文。', {}, [], {}, yexuan_tension=.4)
    body = '\n'.join(m['content'] for m in messages)
    assert 'Dream self-awareness' in body
    assert 'Dream direction:' in body
    assert '<dream_control>{"exit":"accept"}</dream_control>' in body
    for original in ('世界原文。', '自写示例。', '角色原文。', '用户原文。'):
        assert original in body
        body = body.replace(original, '')
    assert not re.search(r'[\u4e00-\u9fff]', body), body


def test_all_inner_voice_variants_keep_selection_but_translate_scaffolding(english):
    from core.thinking_voice import compose, _EMOTIONS
    for emotion in _EMOTIONS:
        for day in range(6):
            kwargs = {'char_id': 'fixture', 'mood': {'current': emotion, 'intensity': .5, 'pending': 'gentle'}, 'now': day * 86400 + 10000}
            en = compose(**kwargs)
            assert not re.search(r'[\u4e00-\u9fff]', en['prompt'])
            english['prompt_language'] = 'zh'
            zh = compose(**kwargs)
            assert (en['register'], en['variant']) == (zh['register'], zh['variant'])
            english['prompt_language'] = 'en'


def test_language_api_persists_validates_and_requires_admin(tmp_path, monkeypatch, sandbox):
    from admin.routers import settings_prompt_language as settings
    from core import config_loader
    from admin.auth import TokenInfo
    config = tmp_path / 'config.yaml'
    config.write_bytes(b'other_setting: keep\n')
    monkeypatch.setattr(config_loader, '_CONFIG_PATH', config)
    monkeypatch.setattr(config_loader, 'reload_config', lambda: None)
    monkeypatch.setattr(config_loader, 'get_config', lambda: __import__('yaml').safe_load(config.read_text()))
    app = FastAPI()
    app.include_router(settings.router)
    monkeypatch.setattr('admin.auth.resolve_token', lambda raw: TokenInfo('admin', frozenset({'admin'}), 'fixture'))
    client = TestClient(app, headers={'Authorization': 'Bearer fixture'})
    assert client.get('/settings/prompt-language').json()['effective_language'] == 'zh'
    assert client.post('/settings/prompt-language', json={'language': 'en'}).json()['effective_language'] == 'en'
    assert 'other_setting: keep' in config.read_text()
    assert client.post('/settings/prompt-language', json={'language': 'fr'}).status_code == 422
    monkeypatch.setattr('admin.auth.resolve_token', lambda raw: TokenInfo('reader', frozenset({'persona'}), 'fixture'))
    assert client.get('/settings/prompt-language').status_code == 200
    assert client.post('/settings/prompt-language', json={'language': 'zh'}).status_code == 403
