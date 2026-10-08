"""Reply scopes reject before any QQ conversation side effects."""
from unittest.mock import AsyncMock, Mock

import pytest

from core import qq_admission


@pytest.mark.parametrize('qq,user,group,expected', [
    ({}, 'owner', None, True), ({}, 'guest', None, False),
    ({}, 'owner', 'group', False), ({'group_enabled': True}, 'guest', 'group', True),
    ({'group_enabled': True}, 'guest', None, False),
    ({'allow_other_users': True}, 'guest', None, True),
    ({'allow_other_users': True}, 'owner', 'group', False),
    ({'group_enabled': 'false'}, 'owner', 'group', False),
])
def test_scopes(monkeypatch, qq, user, group, expected):
    monkeypatch.setattr(qq_admission, 'get_config', lambda: {'qq': qq, 'scheduler': {'owner_id': 'owner'}})
    assert qq_admission.allowed(user, group) is expected


def test_missing_owner_does_not_admit_private(monkeypatch):
    monkeypatch.setattr(qq_admission, 'get_config', lambda: {})
    assert not qq_admission.allowed('guest')


@pytest.mark.asyncio
@pytest.mark.parametrize('user,group', [('owner', 'group'), ('guest', None)])
async def test_dequeued_message_rechecks_hot_config(monkeypatch, user, group):
    import main
    from core import presence
    monkeypatch.setattr(qq_admission, 'get_config', lambda: {'scheduler': {'owner_id': 'owner'}})
    state = Mock()
    group_handler = AsyncMock()
    monkeypatch.setattr(presence, 'update_last_message', state)
    monkeypatch.setattr(main, '_handle_group_message', group_handler)
    await main.handle_message({'user_id': user, 'group_id': group, 'content': 'hello'})
    state.assert_not_called()
    group_handler.assert_not_called()


def test_parse_rejects_before_media(monkeypatch):
    from core import qq_adapter
    monkeypatch.setattr(qq_admission, 'get_config', lambda: {})
    extract = Mock(side_effect=AssertionError('media must not be parsed'))
    monkeypatch.setattr(qq_adapter, '_extract_images', extract)
    assert qq_adapter._parse_event({'post_type': 'message', 'message_type': 'private', 'user_id': 'guest'}) is None
    extract.assert_not_called()
