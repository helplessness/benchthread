import os

import pytest
from fastapi.testclient import TestClient

from tcode_artifacts.personal_tokens import PersonalTokens
from tcode_artifacts.server import _personal_artifact_app
from tcode_artifacts.shared_directory import shared_directory_app

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX directory export')


def test_live_reader_list_controls_existing_references(tmp_path):
    state, source, acl = tmp_path / 'state', tmp_path / 'published', tmp_path / 'readers.txt'
    source.mkdir()
    (source / 'report.csv').write_bytes(b'hello')
    tokens = PersonalTokens(state)
    users = {n: {'X-Artifact-Token': tokens.manage('issue', n)} for n in ('alice', 'bob', 'fixed')}
    app = TestClient(shared_directory_app(state, 'library', source, ['fixed'], 4096, readers_file=acl))
    def get(name, route='health'):
        return app.get('/' + route, headers=users[name])
    assert get('fixed').status_code == 503
    acl.write_bytes(b'alice\r\n')
    ref = get('alice', 'api/v1/files').json()['entries'][0]['artifact_id']
    content = 'api/v1/artifacts/' + ref + '/content'
    assert get('bob', content).status_code == 401
    replacement = tmp_path / 'new-list'
    replacement.write_text('bob\n')
    replacement.replace(acl)
    assert get('alice', content).status_code == 401
    assert get('bob', content).content == b'hello'
    acl.write_bytes(b'')
    assert get('bob', content).status_code == 401
    assert get('fixed', content).content == b'hello'
    for invalid in (b'fixed\n\xff', b' alice', b'x' * (1024**2 + 1)):
        acl.write_bytes(invalid)
        assert get('fixed', content).status_code == 503
    acl.unlink()
    os.mkfifo(acl)
    assert get('fixed', content).status_code == 503
    acl.unlink()
    acl.write_text('bob\n')
    assert get('bob', content).status_code == 200


def test_shared_directory_permissions_and_references(tmp_path):
    state, source = tmp_path / 'state', tmp_path / 'published'
    source.mkdir()
    tokens = PersonalTokens(state)
    alice, bob, eve = [{'X-Artifact-Token': tokens.manage('issue', who)} for who in ('alice', 'bob', 'eve')]
    def start():
        return TestClient(shared_directory_app(state, 'library', source, ['alice', 'bob'], 4096))
    share = start()
    personal = TestClient(_personal_artifact_app(state, 'private', 4096, 8192))
    endpoint = '/api/v1/files'
    private = personal.post('/api/v1/artifacts', headers=alice, files={'file': ('private.txt', b'secret')})
    private_url = '/api/v1/artifacts/' + private.json()['artifact_id'] + '/content'
    library = source / 'results'
    library.mkdir()
    path = library / 'report.csv'
    path.write_bytes(b'one,two\n')
    (source / 'outside').symlink_to(state)
    nested = source / 'accidental-private-store'
    PersonalTokens(nested)
    (nested / 'private-note').write_bytes(b'not published')
    (source / '.hidden').touch()
    os.link(tokens.db, source / 'not-public.db')
    os.mkfifo(source / 'pipe')
    (source / 'second.txt').touch()
    page = share.get(endpoint, params={'limit': 1}, headers=alice).json()
    assert page == {'entries': [{'kind': 'directory', 'path': 'results'}], 'next_offset': 1}
    assert [e['path'] for e in share.get(endpoint, params={'offset': 1}, headers=bob).json()['entries']] == ['second.txt']
    row, = share.get(endpoint, params={'directory': 'results'}, headers=alice).json()['entries']
    ref = '/api/v1/artifacts/' + row['artifact_id']
    assert share.get(endpoint+'/by-path', params={'path': row['path'], 'artifact_id': row['artifact_id']},
                     headers=bob).json()['ref']['service_id'] == 'library'
    assert start().get(ref+'/content', headers=bob).content == path.read_bytes()
    assert share.get(private_url, headers=alice).status_code == 404
    assert personal.get(ref+'/content', headers=alice).status_code == 404
    assert personal.get(private_url, headers=eve).status_code == 404
    for headers in ({}, eve):
        for url in ('/health', endpoint, endpoint+'/by-path?path=results/report.csv', ref, ref+'/content'):
            assert share.get(url, headers=headers).status_code == 401
    for bad in ('outside/.auth/users.sqlite3', 'accidental-private-store/private-note',
                'not-public.db', '../state', '.hidden', 'pipe'):
        assert share.get(endpoint+'/by-path', params={'path': bad}, headers=alice).status_code == 404
    assert share.put(endpoint+'/by-path', params={'path': row['path']}, headers=alice).status_code == 405
    assert share.post('/api/v1/bundles', headers=alice).status_code == 405
    assert path.read_bytes() == b'one,two\n'
    path.write_bytes(b'new revision')
    assert share.get(ref+'/content', headers=alice).status_code == 404
    path.unlink()
    path.symlink_to(tokens.db)
    assert share.get(ref+'/content', headers=alice).status_code == 404
    tokens.manage('revoke', 'alice')
    assert share.get(endpoint, headers=alice).status_code == 401
    assert share.get(endpoint, headers=bob).status_code == 200


def test_refuse_overlapping_storage_and_missing_grants(tmp_path):
    state, source = tmp_path / 'private', tmp_path / 'public'
    PersonalTokens(state)
    source.mkdir()
    for root, readers in [(tmp_path, ['alice']), (state / '.auth', ['alice']), (source, []), (source, ['unknown'])]:
        with pytest.raises(ValueError):
            shared_directory_app(state, 'library', root, readers, 4096)
