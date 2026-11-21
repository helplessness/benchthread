from fastapi.testclient import TestClient

from tcode_artifacts.personal_tokens import PersonalTokens
from tcode_artifacts.server import _personal_artifact_app


def test_abandoned_worker_grant_expires_without_a_restart(tmp_path, monkeypatch):
    from tcode_artifacts import personal_tokens as auth
    users = PersonalTokens(tmp_path)
    binding = auth.request_identity.set(('fixture', '', '', ''))
    try:
        monkeypatch.setattr(auth.time, 'time', lambda: 100)
        grant = users.delegate_files(['/one'], 'output')
        assert users.job_grant(grant) == ('fixture', {'/one'}, 'output')
        assert users.job_grant(grant.replace('86500','99999')) is None
        monkeypatch.setattr(auth.time, 'time', lambda: 86500)
        assert users.job_grant(grant) is None
    finally:
        auth.request_identity.reset(binding)


def test_private_files_survive_restart_and_rotation(tmp_path):
    users = PersonalTokens(tmp_path)
    def issue(action, name):
        return {'Authorization': 'Bearer ' + users.manage(action, name)}

    def start():
        return TestClient(_personal_artifact_app(tmp_path, 'local', 4096, 32768))

    client = start()
    alice, bob = issue('issue', 'Alice'), issue('issue', 'Bob')
    assert client.get('/health', headers=alice).json()['auth_mode'] == 'personal-tokens'
    assert client.get('/health').status_code == 401
    paths = []
    for headers, content in ((alice, b'first'), (bob, b'second')):
        result = client.put('/api/v1/files/by-path', params={'path': 'note.txt'},
                            files={'file': ('note.txt', content)}, headers=headers)
        assert result.status_code == 201
        paths.append('/api/v1/artifacts/' + result.json()['artifact_id'] + '/content')
    assert paths[0] != paths[1]
    client = start()
    for headers, own, other, content in ((alice, 0, 1, b'first'), (bob, 1, 0, b'second')):
        listing = client.get('/api/v1/files', headers=headers).json()['entries']
        assert [entry['path'] for entry in listing] == ['note.txt']
        assert listing[0]['artifact_id'] in paths[own]
        assert client.get(paths[own], headers=headers).content == content
        assert client.get(paths[other], headers=headers).status_code == 404
    replacement = issue('rotate', 'Alice')
    assert client.get(paths[0], headers=alice).status_code == 401
    assert client.get(paths[0], headers=replacement).content == b'first'
    users.manage('revoke', 'Alice')
    assert client.get('/api/v1/files', headers=replacement).status_code == 401
    assert client.get(paths[1], headers=bob).content == b'second'
