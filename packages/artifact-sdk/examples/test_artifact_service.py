"""Real HTTP acceptance for the optional, one-process Artifact service."""
import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path

import httpx
import pytest

from tcode_artifacts import ArtifactIO
from tcode_artifacts.server import run_with_artifact

Client = pytest.importorskip('fastmcp').Client
ToolError = pytest.importorskip('fastmcp.exceptions').ToolError
pytest.importorskip('fastapi')
pytest.importorskip('uvicorn')
pytest.importorskip('python_multipart')

PACKAGE = Path(__file__).resolve().parents[1]
TOKEN = 'test-only-artifact-token-not-for-production'
HEADERS = {'X-Artifact-Token': TOKEN}
ECHO = [str(PACKAGE / 'examples/mcp_server.py'), '--with-artifact']
STANDALONE = ['-m', 'tcode_artifacts.server', 'serve']


@pytest.fixture(autouse=True)
def direct_loopback(monkeypatch):
    # Not dependent on OS proxy settings or the operator's configured gateway.
    monkeypatch.setenv('NO_PROXY', '127.0.0.1')
    monkeypatch.setenv('no_proxy', '127.0.0.1')


@contextmanager
def launch(tmp_path, command, options, *, has_artifact=True, **config):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {key: val for key, val in os.environ.items()
           if not key.startswith(('TMCP_ARTIFACT_', 'FAST_MCP_', 'FASTMCP_'))}
    env.update(PYTHONPATH=str(PACKAGE / 'src'), TMCP_ARTIFACT_API_TOKEN=TOKEN, **config)
    base = f'http://127.0.0.1:{port}'
    with (tmp_path / f'process-{port}.log').open('w+', encoding='utf-8') as log:
        proc = subprocess.Popen([sys.executable, *command, '--port', str(port), *options],
                                env=env, cwd=tmp_path, stdout=log, stderr=subprocess.STDOUT)
        try:
            with httpx.Client(timeout=0.6, trust_env=False) as http:
                deadline = time.monotonic() + 20
                while proc.poll() is None and time.monotonic() < deadline:
                    try:
                        response = http.get(base + '/artifact/health', headers=HEADERS)
                        if response.status_code == (200 if has_artifact else 404):
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(0.05)
                else:
                    log.seek(0)
                    pytest.fail(log.read())
            yield base
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


def test_standalone_prints_persisted_service_id_without_credentials(tmp_path):
    options = ['--data-dir', str(tmp_path / 'store')]
    proxy = 'test-standalone-proxy-credential-not-for-production'
    ids = []
    for _ in range(2):
        with launch(tmp_path, STANDALONE, options, TMCP_ARTIFACT_PROXY_TOKEN=proxy) as base:
            with httpx.Client(trust_env=False) as http:
                service_id = http.get(f'{base}/artifact/health', headers=HEADERS).raise_for_status().json()['service_id']
            ids.append(service_id)
            log = (tmp_path / f"process-{base.rsplit(':', 1)[1]}.log").read_text(encoding='utf-8')
            assert f'Artifact base URL: {base}/artifact' in log
            assert f'Artifact service ID: {service_id}' in log
            assert 'backend credential environment variable name' in log
            assert TOKEN not in log and proxy not in log
    assert ids[0].startswith('artifact-') and ids[0] == ids[1]


async def echo(base, reference):
    async with Client(base + '/mcp') as client:
        assert 'echo_file' in {tool.name for tool in await client.list_tools()}
        result = await client.call_tool('echo_file', {'input_file': reference})
        return result.structured_content['output_bundle']


def test_one_port_file_round_trip_and_restart(tmp_path):
    data = tmp_path / 'store'
    source = tmp_path / 'sample.dat'
    content = b'BenchThread file echo\x00\xff\n'
    source.write_bytes(content)
    with launch(tmp_path, ECHO, ['--data-dir', str(data), '--service-id', 'shared-files']) as base:
        files = ArtifactIO(base + '/artifact', token=TOKEN, service_id='shared-files')
        original = files.upload_file(source)
        result = asyncio.run(echo(base, original))
        assert original['service_id'] == result['service_id'] == 'shared-files'
        assert (files.materialize(result, tmp_path / 'received') / 'echo.dat').read_bytes() == content
        with httpx.Client(base_url=base, trust_env=False) as http:
            path = f"/artifact/api/v1/artifacts/{original['artifact_id']}/content"
            assert http.get(path).status_code == 401
            assert http.get(path, headers={'X-Artifact-Token': 'wrong'}).status_code == 401
            assert http.post('/artifact/api/v1/artifacts', files={'file': ('x', b'x')}).status_code == 401
            named_path = '/artifact/api/v1/files/by-path'
            query = {'path': 'exports/report.dat'}
            assert http.put(named_path, params=query, files={'file': ('x', content)}).status_code == 401
            assert http.put(named_path, headers=HEADERS, params={'path': '../report.dat'},
                            files={'file': ('x', content)}).status_code == 400
            created = files.upload_named_file(source, query['path'])
            assert http.put(named_path, headers=HEADERS, params=query,
                            files={'file': ('x', b'different')}).status_code == 409
            named = files.file_ref_by_path(query['path'])
            assert named == created
            assert http.get(named_path, headers=HEADERS, params={'path': 'report.dat'}).status_code == 404
            assert http.put(named_path, headers=HEADERS, params={'path': 'report.dat'},
                            files={'file': ('x', b'root file')}).status_code == 201
            if os.name == 'posix':
                visible = data / 'files' / query['path']
                assert visible.read_bytes() == content
                assert not visible.samefile(data / named['artifact_id'])
                visible.chmod(0o600)
                visible.write_bytes(b'edited copy')
                outside = tmp_path / 'outside'
                outside.mkdir()
                (data / 'files' / 'escape').symlink_to(outside, target_is_directory=True)
                denied = {'path': 'escape/report.dat'}
                assert http.put(named_path, headers=HEADERS, params=denied,
                                files={'file': ('x', content)}).status_code == 409
                assert http.get(named_path, headers=HEADERS, params=denied).status_code == 404
                assert not list(outside.iterdir())
        named_result = asyncio.run(echo(base, named))
        assert (files.materialize(named_result, tmp_path / 'named-result') / 'echo.dat').read_bytes() == content

    # Restart with the original data and without explicitly setting its service ID.
    with launch(tmp_path, ECHO, ['--data-dir', str(data)]) as base:
        files = ArtifactIO(base + '/artifact', token=TOKEN, service_id='shared-files')
        assert files.materialize(original, tmp_path / 'input-after-restart').read_bytes() == content
        assert files.file_ref_by_path(query['path']) == named
        assert files.materialize(named, tmp_path / 'named-after-restart').read_bytes() == content
        assert (files.materialize(result, tmp_path / 'result-after-restart') / 'echo.dat').read_bytes() == content
        with httpx.Client(base_url=base, headers=HEADERS, trust_env=False) as http:
            assert http.get(named_path, params=query).raise_for_status().json()['ref'] == named
            path = f"/artifact/api/v1/bundles/{result['bundle_id']}/files"
            assert http.put(path, params={'relative_path': 'other'}, files={'file': ('other', b'')}).status_code == 409


@pytest.mark.parametrize('use_service_map', [False, True])
def test_external_standalone_service_has_priority_and_no_failover(tmp_path, use_service_map):
    external_store = tmp_path / 'external'
    unneeded_store = tmp_path / 'must-not-exist'
    source = tmp_path / 'sample.txt'
    source.write_text('external store', encoding='utf-8')
    # Keep MCP alive after explicitly stopping its external Artifact service.
    with ExitStack() as stack:
        external = stack.enter_context(launch(tmp_path, STANDALONE, [
            '--data-dir', str(external_store), '--service-id', 'external-files',
        ]))
        files = ArtifactIO(external + '/artifact', token=TOKEN, service_id='external-files')
        reference = files.upload_file(source)
        if use_service_map:
            config = {'TMCP_ARTIFACT_SERVICES': json.dumps({
                'external-files': {'url': external + '/artifact', 'token_env': 'TMCP_ARTIFACT_API_TOKEN'},
            })}
        else:
            config = {'TMCP_ARTIFACT_SERVICE_URL': external + '/artifact'}
        with launch(tmp_path, ECHO, ['--data-dir', str(unneeded_store)], has_artifact=False,
                    TMCP_ARTIFACT_SERVICE_ID='external-files', **config) as mcp:
            result = asyncio.run(echo(mcp, reference))
            assert (files.materialize(result, tmp_path / 'external-result') / 'echo.dat').read_bytes() == b'external store'
            stack.close()
            with pytest.raises(ToolError, match='[Cc]onnect'):
                asyncio.run(echo(mcp, reference))
            assert not unneeded_store.exists()
            with httpx.Client(base_url=mcp, trust_env=False) as http:
                assert http.get('/artifact/health', headers=HEADERS).status_code == 404
    with launch(tmp_path, STANDALONE, ['--data-dir', str(external_store)]) as base:
        files = ArtifactIO(base + '/artifact', token=TOKEN, service_id='external-files')
        assert (files.materialize(result, tmp_path / 'external-after-restart') / 'echo.dat').read_bytes() == b'external store'


def test_external_launcher_rejects_explicit_builtin_token(tmp_path, monkeypatch):
    monkeypatch.setenv('TMCP_ARTIFACT_SERVICE_URL', 'http://127.0.0.1:18081/artifact')
    monkeypatch.delenv('TMCP_ARTIFACT_SERVICES', raising=False)
    data = tmp_path / 'not-created'
    with pytest.raises(ValueError, match='token= is only for built-in Artifact'):
        run_with_artifact(object(), data_dir=data, token=TOKEN)
    assert not data.exists()


def test_bundle_writes_require_the_same_trusted_workspace_and_owner(tmp_path):
    data = tmp_path / 'scoped-store'
    source = tmp_path / 'legacy'
    source.mkdir()
    (source / 'old.dat').write_bytes(b'old bytes')
    workspace = {'workspace_id': 'work-a', 'owner_id': 't' * 128 + ':' + 'u' * 128}
    proxy = 'test-only-distinct-trusted-proxy-credential'
    proxy_headers = {'X-Artifact-Token': proxy}
    options = ['--data-dir', str(data), '--service-id', 'scoped-files']
    with launch(tmp_path, STANDALONE, options) as base:
        with httpx.Client(base_url=base + '/artifact/', headers=HEADERS, trust_env=False) as http:
            assert http.post('api/v1/bundles', json=workspace).status_code == 403
        legacy = ArtifactIO(base + '/artifact', token=TOKEN, service_id='scoped-files').upload_directory(source)
    # Simulate the previous schema in the disposable store. Its completed bundle has no owner row.
    with closing(sqlite3.connect(data / 'artifacts.sqlite3')) as db, db:
        db.execute('DROP TABLE bundle_owners')

    with launch(tmp_path, STANDALONE, options, TMCP_ARTIFACT_PROXY_TOKEN=proxy) as base:
        url = base + '/artifact/'
        with httpx.Client(base_url=url, headers=HEADERS, trust_env=False) as http:
            io = ArtifactIO(url, token=TOKEN, service_id='scoped-files')
            assert (io.materialize(legacy, tmp_path / 'old-download') / 'old.dat').read_bytes() == b'old bytes'
            assert http.post('api/v1/bundles', json=workspace).status_code == 403
            assert http.post('api/v1/bundles', headers=proxy_headers,
                             json={'owner_id': workspace['owner_id']}).status_code == 400
            result = http.post('api/v1/bundles', headers=proxy_headers, json=workspace).raise_for_status().json()
            bundle = 'api/v1/bundles/' + result['bundle_id']
            files = {'file': ('new.dat', b'new bytes')}
            for headers, identity in (
                (HEADERS, workspace),
                (proxy_headers, {}),
                (proxy_headers, {**workspace, 'workspace_id': 'work-b'}),
                (proxy_headers, {**workspace, 'owner_id': 'tenant-a:user-b'}),
            ):
                assert http.put(bundle + '/files', headers=headers, files=files,
                                params={**identity, 'relative_path': 'new.dat'}).status_code == 403
                assert http.post(bundle + '/complete', headers=headers,
                                 json={**identity, 'expected_files': 0}).status_code == 403
            assert not http.get(bundle).raise_for_status().json()['files']
            http.put(bundle + '/files', headers=proxy_headers, files=files,
                     params={**workspace, 'relative_path': 'new.dat'}).raise_for_status()

    # Owner binding survives a restart, even for an unfinished upload.
    with launch(tmp_path, STANDALONE, options, TMCP_ARTIFACT_PROXY_TOKEN=proxy) as base:
        url = base + '/artifact/'
        with httpx.Client(base_url=url, headers=HEADERS, trust_env=False) as http:
            assert http.post(bundle + '/complete', json={'expected_files': 1}).status_code == 403
            completed = http.post(bundle + '/complete', headers=proxy_headers,
                                  json={**workspace, 'expected_files': 1}).raise_for_status().json()
            assert completed['ref'] == result['ref']
        io = ArtifactIO(url, token=TOKEN, service_id='scoped-files')
        assert (io.materialize(result['ref'], tmp_path / 'owned-download') / 'new.dat').read_bytes() == b'new bytes'


def test_server_enforces_path_and_upload_limits(tmp_path):
    data = tmp_path / 'limits'
    with launch(tmp_path, STANDALONE, ['--data-dir', str(data), '--max-file-bytes', '16',
                                    '--max-store-bytes', '32']) as base:
        with httpx.Client(base_url=base + '/artifact/', headers=HEADERS, trust_env=False) as http:
            assert http.post('api/v1/artifacts', files={'file': ('too-big.bin', b'0' * 17)}).status_code == 413
            # Chunked uploads cannot bypass the HTTP request limit.
            boundary = 'demo'
            response = http.post('api/v1/artifacts',
                                 headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
                                 content=iter([b'--demo\r\nContent-Disposition: form-data; name="file"; filename="x"\r\n\r\n',
                                               b'x' * 70_000, b'\r\n--demo--\r\n']))
            assert response.status_code == 413
            identifier = http.post('api/v1/bundles', json={}).raise_for_status().json()['bundle_id']
            path = f'api/v1/bundles/{identifier}'

            def put(relative, content):
                return http.put(path + '/files', params={'relative_path': relative}, files={'file': ('x', content)})

            assert put('../escape', b'x').status_code == 400
            assert put('a/b.dat', b'x' * 16).status_code == 200
            assert put('a', b'x').status_code == 400
            assert http.post(path + '/complete', json={'expected_files': 2}).status_code == 409
            assert http.get(path).json()['state'] == 'draft'
            assert put('c', b'c' * 16).status_code == 200
            assert put('d', b'd').status_code == 507
            assert http.post(path + '/complete', json={'expected_files': 2}).json()['state'] == 'ready'
    assert len(list(data.glob('art-*'))) == 2
