"""Sidebar pagination and metadata remain independent of conversation mutations."""
from fastapi.testclient import TestClient

from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.sessions import SessionRepository


def seed(repo, count=45):
    for index in range(count):
        repo.save({'id': f'chat-{index:03}', 'title': f'对话 {index}', 'updated_at': '2026-10-22T00:00:00+00:00',
                   'messages': [{'role': 'user', 'content': f'做菜 {index}'}], 'history': [], 'workspace': {},
                   'running': False, 'pinned': index in (4, 24)})


def test_cursor_pages_cover_ties_pins_and_deleted_anchor(tmp_path):
    repo = SessionRepository(tmp_path / 'sessions.db')
    seed(repo)
    repo.create()  # An unsent chat never occupies a sidebar slot.
    with TestClient(create_app(PlaygroundConfig(sessions_database=repo.path), repository=repo)) as client:
        first = client.get('/api/sessions').json()
        assert len(first['items']) == 20
        assert [item['id'] for item in first['items'][:2]] == ['chat-004', 'chat-024']
        assert 'history' not in first['items'][0]
        anchor = first['items'][-1]['id']
        repo.delete(anchor)
        second = client.get('/api/sessions', params={'cursor': first['next_cursor']}).json()
        third = client.get('/api/sessions', params={'cursor': second['next_cursor']}).json()
        items = first['items'] + second['items'] + third['items']
        assert len(items) == len({item['id'] for item in items}) == 45
        assert len(second['items']) == 20 and len(third['items']) == 5
        assert third['next_cursor'] is None
        for params in ({'cursor': 'invalid'}, {'cursor': 'e30='}, {'limit': 0}, {'limit': 101}):
            assert client.get('/api/sessions', params=params).status_code == 422


def test_metadata_is_persisted_without_changing_activity_or_workspace(tmp_path):
    repo = SessionRepository(tmp_path / 'sessions.db')
    seed(repo, 3)
    original = repo.get('chat-001')
    with TestClient(create_app(PlaygroundConfig(sessions_database=repo.path), repository=repo)) as client:
        response = client.patch('/api/sessions/chat-001', json={'title': '  今晚吃什么  ', 'pinned': True})
        assert response.status_code == 200
        assert response.json()['title'] == '今晚吃什么'
        repo.mutate('chat-001', lambda state: state.update(running=True, last_status='running'))
        saved = client.get('/api/sessions/chat-001').json()
        assert saved['title'] == '今晚吃什么' and saved['pinned'] and saved['title_custom']
        assert saved['running'] and saved['updated_at'] == original['updated_at']
        assert repo.get('chat-001')['workspace'] == original['workspace']
        assert client.get('/api/sessions').json()['items'][0]['id'] == 'chat-001'
        assert client.patch('/api/sessions/chat-001', json={'pinned': False}).status_code == 200
        for payload in ({'title': '   '}, {'title': None}, {'title': '长' * 81}, {'pinned': 'true'}, {}, {'running': False}):
            assert client.patch('/api/sessions/chat-001', json=payload).status_code == 422
        assert client.patch('/api/sessions/missing', json={'title': '名称'}).status_code == 404
    restored = SessionRepository(repo.path)
    assert restored.get('chat-001')['title'] == '今晚吃什么'
    assert restored.get('chat-001')['title_custom'] is True
