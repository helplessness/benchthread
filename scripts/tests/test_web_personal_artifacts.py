"""Two real Web login sessions, one SDK HTTP server, no deployment credentials."""
import asyncio
import base64
import json
import os
import secrets
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import uvicorn
import httpx

from tcode_agent.artifact_transport import _artifact_endpoint
from tcode_agent.server import artifact_services_api, auth, files_api, workdirs
from tcode_agent.session.manager import SessionManager
from tcode_agent.tools.base import ToolContext
from tcode_agent.tools.builtin.artifact_import import ArtifactUploadTool


def test_two_personal_bindings(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'packages/artifact-sdk/src'))
    from tcode_artifacts.personal_tokens import PersonalTokens
    from tcode_artifacts.server import _personal_artifact_app

    monkeypatch.chdir(tmp_path)
    for name, value in {'TCODE_WORKDIR': str(tmp_path/'runtime'), 'TCODE_CONFIG_ROOT':str(tmp_path/'config'),
                        'TCODE_USER_WORKDIRS_ROOT':str(tmp_path/'workspaces'), 'TCODE_ENTERPRISE_MODE':'1',
                        'TCODE_DESKTOP_MANAGED':'0','TMCP_ARTIFACT_TARGET_SERVICES':'{}',
                        'TCODE_BOOTSTRAP_PASSWORD':secrets.token_urlsafe(20),
                        'TMCP_ARTIFACT_SERVICE_ID':'lab'}.items():
        monkeypatch.setenv(name,value)
    for name,value in [('_sessions',{}),('_loaded',False),('_lock',None)]:
        monkeypatch.setattr(SessionManager,name,value)
    keyfile = tmp_path/'key'
    keyfile.write_bytes(base64.b64encode(os.urandom(32)))
    keyfile.chmod(0o600)
    monkeypatch.setenv('TCODE_ARTIFACT_BINDING_KEY_FILE',str(keyfile))
    store = tmp_path/'sdk'
    issuer = PersonalTokens(store)
    remote = FastAPI()
    remote.mount('/artifact', _personal_artifact_app(store,'lab',65536,1<<20))
    web = FastAPI()
    web.include_router(artifact_services_api.router)
    web.include_router(files_api.router)
    with socket.socket() as sock, TestClient(web) as alice, TestClient(web) as bob:
        sock.bind(('127.0.0.1',0))
        url = f'http://127.0.0.1:{sock.getsockname()[1]}/artifact'
        monkeypatch.setenv('FIXTURE_SHARED_TOKEN',secrets.token_urlsafe(32))
        monkeypatch.setenv('TMCP_ARTIFACT_SERVICES',json.dumps({
            'lab':{'url':url,'auth_mode':'personal-tokens'},
            'shared':{'url':'https://shared.example.invalid','token_env':'FIXTURE_SHARED_TOKEN'}}))
        server = uvicorn.Server(uvicorn.Config(remote,log_level='error',access_log=False))
        thread = threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started: break
                time.sleep(.02)
            assert server.started
            private = '/api/artifact-services/personal/lab'
            cfg = '/api/artifact-services/config'
            assert alice.put(private,json={}).status_code == 401
            users = [(alice,'alice'),(bob,'bob')]
            tokens = {}
            roots = {}
            tenant = workdirs.default_tenant_id()
            for client,name in users:
                pw = secrets.token_urlsafe(18)
                email = name+'@example.invalid'
                auth.create_user_account(tenant_id=tenant,email=email,password=pw,user_id=name)
                client.cookies.set(auth.AUTH_COOKIE_NAME,auth.create_login_session(auth.authenticate(email,pw)))
                # No body field, query parameter or owner header can switch the authenticated user.
                client.headers['X-TCode-User-Id'] = 'bob' if name=='alice' else 'alice'
                assert client.get(cfg).json()['personal_bound'] == []
                owner = dict(tenant_id=tenant,user_id=name)
                workdirs.create_user_workdir(str(tmp_path/'runtime'),'research',**owner)
                roots[name] = workdirs.resolve_user_workdir_path(str(tmp_path/'runtime'),'research',**owner)
                asyncio.run(SessionManager.create(session_id=name,workspace_id='research',
                    workdir=str(roots[name]),metadata={'config_snapshot_id':'fixture'},**owner))
                tokens[name] = issuer.manage('issue','remote-'+name)
                health = httpx.get(url+'/health',headers={'X-Artifact-Token':tokens[name]},trust_env=False)
                assert health.headers['cache-control'] == 'private, no-store'
                body = {'expected_url':url,'token':tokens[name]}
                assert client.put(private,json={**body,'expected_url':url+'/old'}).status_code == 400
                assert client.put(private,json=body,headers={'Origin':'https://other.invalid'}).status_code == 403
                if name=='bob':
                    assert client.put(private,json={**body,'token':tokens['alice']}).status_code == 400
                assert client.put(private,json=body).json() == {'bound':True}
                assert client.get(cfg).json()['personal_bound'] == ['lab']
                assert tokens[name] not in client.get(cfg).text
                assert tokens[name].encode() not in auth._db_path().read_bytes()
            with pytest.raises(ValueError,match='个人'):
                _artifact_endpoint('lab')  # No worker / shared fallback.
            assert _artifact_endpoint('shared')[2] == {'X-Artifact-Token':os.environ['FIXTURE_SHARED_TOKEN']}
            bundles = '/api/artifacts/bundles'
            browse = '/api/artifact-services/chat/browse'
            download = '/api/artifact-services/chat/import'
            ids = {}
            for client,name in users:
                params = {'session_id':name}
                result = client.post(bundles,json=params)
                result.raise_for_status()
                bundle = result.json()['bundle_id']
                if client is alice:
                    assert bob.put(f'{bundles}/{bundle}/files',params={'session_id':'bob','relative_path':'sample.txt'},
                                   files={'file':('sample.txt',b'wrong')}).status_code in (403,404)
                client.put(f'{bundles}/{bundle}/files',params={**params,'relative_path':'sample.txt'},
                           files={'file':('sample.txt',name.encode())}).raise_for_status()
                client.post(f'{bundles}/{bundle}/complete',json={**params,'expected_files':1}).raise_for_status()
                items = client.post(browse,json={**params,'service_id':'lab'}).json()['entries']
                assert len(items)==1 and items[0]['path']=='sample.txt'
                ids[name] = items[0]['artifact_id']
            assert ids['alice'] != ids['bob']
            for client,name in users:
                other = 'alice' if name=='bob' else 'bob'
                item = {'session_id':name,'service_id':'lab','path':'sample.txt','artifact_id':ids[name]}
                assert client.post(download,json={**item,'session_id':other}).status_code == 404
                assert client.post(download,json={**item,'artifact_id':ids[other]}).status_code == 400
                client.post(download,json=item).raise_for_status()
                assert (roots[name]/'imports'/'sample.txt').read_bytes() == name.encode()
                context = ToolContext(name,'message','build',None,extra={
                    'tenant_id':tenant,'user_id':name,'workspace_id':'research',
                    'workdir':str(roots[name]),'__permission_ask__':lambda *_: True})
                uploaded = asyncio.run(ArtifactUploadTool().execute({
                    'local_path':'imports/sample.txt','remote_path':'roundtrip.txt'},context))
                assert not uploaded.error, uploaded.error
            def read_back(pair):
                client,name = pair
                params = {'session_id':name,'service_id':'lab'}
                rows = client.post(browse,json=params).json()['entries']
                copy = next(item for item in rows if item['path']=='roundtrip.txt')
                client.post(download,json={**params,'path':copy['path'],'artifact_id':copy['artifact_id']}).raise_for_status()
                return (roots[name]/'imports'/'roundtrip.txt').read_bytes()
            with ThreadPoolExecutor(max_workers=2) as pool:
                assert list(pool.map(read_back,users)) == [b'alice',b'bob']
            issuer.manage('revoke','remote-alice')
            assert alice.post(browse,json={'session_id':'alice','service_id':'lab'}).status_code == 400
            replacement = issuer.manage('rotate','remote-alice')
            assert alice.put(private,json={'expected_url':url,'token':replacement}).json()=={'bound':True}
            assert alice.delete(private).json()=={'bound':False}
            assert alice.post(bundles,json={'session_id':'alice'}).status_code == 403
            rows = bob.post(browse,json={'session_id':'bob','service_id':'lab'}).json()['entries']
            assert any(r['artifact_id']==ids['bob'] for r in rows)
            monkeypatch.setenv('TMCP_ARTIFACT_SERVICES', '{}')
            monkeypatch.setenv('TMCP_ARTIFACT_SERVICE_ID', '')
            config = bob.get(cfg).json()
            assert config['personal_saved'] == ['lab']
            assert config['personal_bound'] == []
            assert alice.get(cfg).json()['personal_saved'] == []
            bob.delete(private).raise_for_status()
            assert bob.get(cfg).json()['personal_saved'] == []

            # The SDK now advertises 'lab'; simulate the binding persisted before it was renamed.
            from tcode_agent.server import personal_artifacts
            personal = {'url': url, 'auth_mode': 'personal-tokens'}
            monkeypatch.setenv('TMCP_ARTIFACT_SERVICES', json.dumps({'old-lab': personal}))
            personal_artifacts.save(tenant, 'bob', 'old-lab', url, issuer.owner(tokens['bob']), tokens['bob'])
            monkeypatch.setenv('TMCP_ARTIFACT_SERVICES', json.dumps({'lab': personal}))
            monkeypatch.setenv('TMCP_ARTIFACT_SERVICE_ID', 'lab')
            credentials = {'expected_url': url, 'token': tokens['bob']}
            assert bob.put(private, json=credentials).status_code == 400
            assert bob.get(cfg).json()['personal_saved'] == ['old-lab']
            bob.delete('/api/artifact-services/personal/old-lab').raise_for_status()
            bob.put(private, json=credentials).raise_for_status()
            assert bob.get(cfg).json()['personal_bound'] == ['lab']
        finally:
            server.should_exit = True
            thread.join(5)

def test_optional_upload_default_and_explicit_mcp_routes(monkeypatch):
    from tcode_agent.artifact_services import artifact_service
    from tcode_agent.server.desktop_config import artifact_configuration
    services = {key: {'url': f'https://example.invalid/{key}'} for key in ('home', 'lab')}
    entry = {'artifact_services': services, 'artifact_default_service_id': '', 'artifact_target_services': {}}
    assert artifact_configuration(entry, saving=True)['artifact_default_service_id'] == ''
    monkeypatch.setenv('TMCP_ARTIFACT_SERVICES', json.dumps(services))
    monkeypatch.setenv('TMCP_ARTIFACT_SERVICE_ID', '')
    with pytest.raises(ValueError, match='选择文件服务'):
        artifact_service(default_url='https://stale.invalid')
    assert artifact_service('lab')[0] == 'lab'
    monkeypatch.setenv('TMCP_ARTIFACT_SERVICE_ID', 'home')
    monkeypatch.setenv('TMCP_ARTIFACT_TARGET_SERVICES', '{"compute":"lab","*":"home"}')
    assert artifact_service()[0] == 'home'
    assert artifact_service(target_server='compute')[0] == 'lab'
    with pytest.raises(ValueError, match='绑定文件服务'):
        artifact_service(target_server='unbound')
    with pytest.raises(ValueError, match='来源与绑定'):
        artifact_service('home', target_server='compute')
    with pytest.raises(ValueError, match='具体的 MCP'):
        artifact_configuration({**entry, 'artifact_target_services': {'*': 'lab'}}, saving=True)


def test_text_calls_and_existing_file_references_use_separate_routes(monkeypatch):
    from tcode_agent.artifact_transport import ArtifactMaterializationError
    from tcode_agent.mcp.artifact_boundary import prepare_mcp_artifact_arguments

    monkeypatch.setenv('TMCP_ARTIFACT_SERVICES', '{"store":{"url":"https://fixture.invalid"}}')
    monkeypatch.setenv('TMCP_ARTIFACT_SERVICE_ID', 'store')
    monkeypatch.setenv('TMCP_ARTIFACT_TARGET_SERVICES', '{}')
    text = {'message': 'hello'}
    assert asyncio.run(prepare_mcp_artifact_arguments('test-mcp', 'echo', text, None)) == (text, [])
    reference = {'file': {'kind': 'artifact', 'artifact_id': 'art_fixture', 'service_id': 'store'}}
    with pytest.raises(ArtifactMaterializationError, match='绑定文件服务'):
        asyncio.run(prepare_mcp_artifact_arguments('test-mcp', 'echo', reference, None))
    monkeypatch.setenv('TMCP_ARTIFACT_TARGET_SERVICES', '{"test-mcp":"store"}')
    assert asyncio.run(prepare_mcp_artifact_arguments('test-mcp', 'echo', reference, None)) == (reference, [])
