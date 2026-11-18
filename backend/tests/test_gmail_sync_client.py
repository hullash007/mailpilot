"""Mocked Google discovery surface: argument contracts, no real account reads."""
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from app.integrations.gmail.client import GmailSyncClient


def client():
    instance = object.__new__(GmailSyncClient)
    instance.service = MagicMock()
    return instance


def test_message_page_returns_continuation_and_excludes_spam_trash():
    c = client()
    execute = c.service.users.return_value.messages.return_value.list.return_value.execute
    execute.return_value = {'messages': [], 'nextPageToken': 'opaque'}
    assert c.list_message_page('prior', 25)['nextPageToken'] == 'opaque'
    c.service.users.return_value.messages.return_value.list.assert_called_once_with(
        userId='me', maxResults=25, pageToken='prior', includeSpamTrash=False)
    execute.assert_called_once_with(num_retries=0)


def test_history_api_retains_original_start_id():
    c = client()
    c.list_history_page('123', 'next')
    c.service.users.return_value.history.return_value.list.assert_called_once_with(
        userId='me', startHistoryId='123', pageToken='next', maxResults=100)
    c.service.users.return_value.history.return_value.list.return_value.execute.assert_called_once_with(num_retries=0)


def test_thread_snapshot_preserves_provider_version_and_messages():
    c = client()
    c.service.users.return_value.threads.return_value.get.return_value.execute.return_value = {
        'id': 't1', 'historyId': '500', 'messages': [dict(id='m1', threadId='t1')]}
    result = c.get_thread_snapshot('t1')
    assert result['history_id'] == '500'
    assert result['messages'][0]['gmail_message_id'] == 'm1'
    c.service.users.return_value.threads.return_value.get.assert_called_once_with(userId='me', id='t1', format='full')


@pytest.mark.parametrize('page_size', [0, 101, True, 1.5])
def test_bad_budget_refused_without_network(page_size):
    c = client()
    with pytest.raises(ValueError): c.list_message_page(page_size=page_size)
    c.service.users.assert_not_called()
