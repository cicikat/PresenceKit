import copy
from types import SimpleNamespace

import pytest

from core import thinking, thinking_voice as voice


def test_variants_hold_across_turns_mood_strength_and_restart():
    mood = {'current': 'sad', 'intensity': .2}
    # Away from the deliberately staggered daily boundary.
    now = 1789100000
    first = voice.compose(char_id='fixture', mood=mood, now=now)
    for second in range(1, 60):
        other = voice.compose(char_id='fixture', mood={**mood, 'intensity': .8, 'updated_at': now + second}, now=now + second)
        assert (other['register'], other['variant']) == (first['register'], first['variant'])
    assert voice.compose(char_id='fixture', mood=mood, now=now) == first
    selections = {voice.compose(char_id='fixture', mood=mood, now=now + day * 86400)['variant'] for day in range(30)}
    assert selections == {0, 1, 2}


def test_emotion_changes_tone_without_changing_register():
    quiet = voice.compose(char_id='fixture', mood={'current': 'neutral'}, now=100000)
    angry = voice.compose(char_id='fixture', mood={'current': 'angry'}, now=100000)
    assert quiet['register'] == angry['register']
    assert quiet['prompt'] != angry['prompt']
    assert '我' in angry['prompt'] and '不出现‘用户’' in angry['prompt']
    assert '自己' in angry['prompt'] and '不照抄' in angry['prompt']


def test_unknown_mood_and_no_name_have_natural_fallback():
    result = voice.compose(char_id='fixture', mood={'current': 'unknown'}, now=0)
    assert result['emotion'] == 'neutral'
    assert '称呼以‘你’为主' in result['prompt']


def test_preview_reads_explicit_scope_not_active_role(monkeypatch):
    from core.memory import mood_state
    calls = []
    monkeypatch.setattr(mood_state, 'load', lambda *, char_id: calls.append(char_id) or {'current': 'happy'})
    monkeypatch.setattr(voice, 'get_user_display_name', lambda: '小伙伴')
    result = voice.preview('other-character')
    assert calls == ['other-character']
    assert result['emotion'] == 'happy'
    assert '小伙伴' in result['prompt']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['native', 'auto'])
async def test_native_guidance_is_local_idempotent_and_gated(monkeypatch, mode):
    cfg = {'enabled': True, 'mode': mode, 'character_voice': True}
    monkeypatch.setattr(thinking, 'get_config', lambda: {'thinking': cfg})
    monkeypatch.setattr(voice, 'preview', lambda char_id: {'prompt': 'fixture voice'})
    async def no_monologue(*args, **kwargs):
        pytest.fail('native guidance must not call another model')
    monkeypatch.setattr(thinking, '_run_monologue_call', no_monologue)
    messages = [{'role': 'system', 'content': 'persona'}, {'role': 'user', 'content': 'hello'}]
    original = copy.deepcopy(messages)
    mc = SimpleNamespace(reasoning_native=True)
    out = await thinking.maybe_apply(messages, call_category='chat', mc=mc)
    assert messages == original
    assert out[-1] == messages[-1]
    assert out[-2]['_layer'] == voice.LAYER
    assert await thinking.maybe_apply(out, call_category='chat', mc=mc) is out
    for category in ['monologue', 'intent', 'summary']:
        assert await thinking.maybe_apply(messages, call_category=category, mc=mc) is messages
    assert await thinking.maybe_apply(messages, call_category='chat', is_proactive=True, mc=mc) is messages
    cfg['character_voice'] = False
    assert await thinking.maybe_apply(messages, call_category='chat', mc=mc) is messages
    cfg.update(character_voice=True, enabled=False)
    assert await thinking.maybe_apply(messages, call_category='chat', mc=mc) is messages


@pytest.mark.asyncio
async def test_monologue_receives_frozen_persona_and_only_returns_prose(monkeypatch):
    from core import llm_client
    monkeypatch.setattr(thinking, 'get_config', lambda: {'thinking': {'enabled': True, 'mode': 'monologue'}})
    monkeypatch.setattr(voice, 'preview', lambda char_id: {'prompt': 'fixture voice'})
    captured = []
    async def chat(messages, **kwargs):
        captured.extend(messages)
        assert kwargs['call_category'] == 'monologue'
        return '<think>not visible</think>我有点舍不得。'
    monkeypatch.setattr(llm_client, 'chat', chat)
    messages = [{'role': 'system', '_layer': '2_char_desc', 'content': 'frozen quiet personality'},
                {'role': 'assistant', '_layer': '9_history', 'content': 'earlier reply'},
                {'role': 'user', 'content': '晚安'}]
    result = await thinking._run_monologue_call(messages, char_id='fixture')
    assert result == '我有点舍不得。'
    assert 'frozen quiet personality' in captured[0]['content']
    assert '我：earlier reply' in captured[1]['content']


@pytest.mark.asyncio
async def test_voice_failure_does_not_block_native_reply(monkeypatch):
    monkeypatch.setattr(thinking, 'get_config', lambda: {'thinking': {'enabled': True, 'mode': 'native'}})
    monkeypatch.setattr(voice, 'native_message', lambda _: (_ for _ in ()).throw(ValueError()))
    messages = [{'role': 'user', 'content': 'hello'}]
    assert await thinking.maybe_apply(messages, call_category='chat') is messages


@pytest.mark.asyncio
async def test_settings_expose_effective_preview_and_toggle(monkeypatch, tmp_path):
    from admin.routers import settings_thinking as routes
    from core import config_loader
    config = {'thinking': {'enabled': False}}
    monkeypatch.setattr(routes, 'get_config', lambda: config)
    monkeypatch.setattr(routes, '_chat_preset_reasoning_native', lambda: True)
    monkeypatch.setattr(routes, 'read_config_file', lambda _: config)
    monkeypatch.setattr(routes, 'write_config_file', lambda *args: None)
    monkeypatch.setattr(config_loader, 'reload_config', lambda: None)
    monkeypatch.setattr(voice, 'get_user_display_name', lambda: '')
    state = await routes.get_thinking()
    assert state['character_voice'] is True
    assert state['voice_preview']['effective'] is False
    await routes.update_thinking(routes.ThinkingUpdate(enabled=True, character_voice=False))
    state = await routes.get_thinking()
    assert state['voice_preview']['blocking_reason'] == 'voice_disabled'
    await routes.update_thinking(routes.ThinkingUpdate(character_voice=True))
    state = await routes.get_thinking()
    assert state['voice_preview']['effective'] is True
    assert state['voice_preview']['output_guaranteed'] is False
