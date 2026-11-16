"""Temporal evidence must reach the real fetch -> prompt boundary, not just a report."""
import asyncio

from tests.test_pipeline_user_facts_wiring import chars_tree, registry, _make_pipeline, _write_active
from tests.fixtures.public_assets import TEST_CHAR_ID


def test_real_fetch_and_prompt_use_latest_user_evidence(chars_tree,sandbox,registry,monkeypatch):
    from core.memory import event_store,user_profile
    from core.memory.scope import MemoryScope
    from core import llm_client
    from unittest.mock import AsyncMock
    scope=MemoryScope.reality_scope('temporal_fixture',TEST_CHAR_ID)
    # Only external embedding/model boundaries are mocked. All memory retrieval,
    # fact resolution, profile budgets and prompt assembly remain real.
    monkeypatch.setattr(llm_client,'get_embedding',AsyncMock(return_value=None),raising=False)
    user_profile.save(scope.uid,{'location':'青旅'},char_id=TEST_CHAR_ID)
    assert event_store.append_event(scope,{'event_id':'housing-new','occurred_at':1700100000,'actor':'user','kind':'user_message',
        'source':'user_chat','raw_text':'我现在住在出租屋','memory_text':'我现在住在出租屋'}).ok
    pipeline=_make_pipeline(TEST_CHAR_ID,registry);_write_active(sandbox,TEST_CHAR_ID)
    context=asyncio.run(pipeline.fetch_context(scope.uid,'我现在住在哪里',frozen_scope=scope))
    messages,_=pipeline.build_prompt(scope.uid,'我现在住在哪里',context,char_id=TEST_CHAR_ID,consume_pending_perception=False)
    layer=next(message for message in messages if message.get('_layer')=='5_profile')
    assert '出租屋' in layer['content'] and '青旅' not in layer['content']
    assert context['_temporal_fact_decisions'][0]['winner_id']
    assert user_profile.load(scope.uid,char_id=TEST_CHAR_ID)['location']=='青旅', 'no implicit production migration'
