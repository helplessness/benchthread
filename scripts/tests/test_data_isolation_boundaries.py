"""Regression coverage for owner checks and mutable workspace filesystem boundaries."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from tcode_agent.server import files_api, managed_files, mcp_config_api, session_lifecycle, sessions_api, skills_api
from tcode_agent.server import websearch_config_api, workdirs


def run(awaitable):
    return asyncio.run(awaitable)


def request(payload=None):
    req = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': []})
    req.json = AsyncMock(return_value=payload or {})
    return req


def test_foreign_session_delete_has_no_side_effects(monkeypatch):
    monkeypatch.setattr(session_lifecycle.SessionManager, 'get', AsyncMock(return_value=None))
    cancel = AsyncMock()
    schedule = Mock()
    monkeypatch.setattr(session_lifecycle.ServerState, 'cancel_session', cancel)
    monkeypatch.setattr(sessions_api, '_schedule_session_delete', schedule)
    owner = workdirs.WorkspaceContext('a', 'alice', 'w')
    monkeypatch.setattr(sessions_api, '_workspace_context_from_request', lambda _: owner)
    with pytest.raises(HTTPException) as error:
        run(sessions_api.delete_session('bob-session', request()))
    assert error.value.status_code == 404
    schedule.assert_not_called()
    with pytest.raises(HTTPException):
        run(session_lifecycle._delete_session_tree_background('bob-session', owner_kwargs={'tenant_id':'a','user_id':'alice'}))
    cancel.assert_not_called()


def test_delete_status_remains_owner_scoped_after_row_deletion(monkeypatch):
    monkeypatch.setattr(sessions_api, '_SESSION_DELETE_OWNERS', {'gone': {'tenant_id':'a','user_id':'alice'}})
    monkeypatch.setattr(sessions_api, '_SESSION_DELETE_ERRORS', {'gone': 'private error'})
    monkeypatch.setattr(sessions_api, '_workspace_context_from_request', lambda _: workdirs.WorkspaceContext('b','bob','w'))
    with pytest.raises(HTTPException) as error:
        run(sessions_api.get_session_delete_status('gone', request()))
    assert error.value.status_code == 404
    monkeypatch.setattr(sessions_api, '_workspace_context_from_request', lambda _: workdirs.WorkspaceContext('a','alice','w'))
    assert run(sessions_api.get_session_delete_status('gone', request()))['error'] == 'private error'


@pytest.mark.parametrize('role,tenant,allowed', [('admin','foreign',False),('owner','foreign',False),('member','default',False),('admin','default',True)])
def test_shared_mcp_requires_instance_admin(monkeypatch, role, tenant, allowed):
    monkeypatch.setattr(mcp_config_api, 'auth_required', lambda: True)
    monkeypatch.setattr(mcp_config_api.workdir_utils, 'default_tenant_id', lambda: 'default')
    monkeypatch.setattr(mcp_config_api, 'require_current_user', lambda _: SimpleNamespace(role=role,tenant_id=tenant))
    if allowed:
        mcp_config_api._require_admin(request())
    else:
        with pytest.raises(HTTPException) as error:
            mcp_config_api._require_admin(request())
        assert error.value.status_code == 403


@pytest.mark.parametrize('new_url,expected', [('https://old.invalid/mcp',{'Authorization':'secret'}),('https://new.invalid/mcp',{})])
def test_mcp_url_change_drops_old_credentials(monkeypatch, new_url, expected):
    monkeypatch.setattr(mcp_config_api, '_require_admin', lambda _: None)
    monkeypatch.setattr(mcp_config_api, '_read', lambda: {'remote': {'url':'https://old.invalid/mcp','headers':{'Authorization':'secret'}}})
    saved = Mock()
    monkeypatch.setattr(mcp_config_api, '_write', saved)
    monkeypatch.setattr(mcp_config_api, '_reload', AsyncMock())
    run(mcp_config_api._put_mcp_config('remote', request({'url':new_url})))
    assert saved.call_args.args[0]['remote'].get('headers',{}) == expected


def test_mcp_discovery_does_not_send_previous_credentials_to_new_url(monkeypatch):
    from tcode_agent.mcp import custom_targets
    monkeypatch.setattr(mcp_config_api, '_require_admin', lambda _: None)
    monkeypatch.setattr(mcp_config_api, '_read', lambda: {'remote':{'url':'https://old.invalid/mcp','headers':{'Authorization':'secret'}}})
    discover = AsyncMock(return_value=[])
    monkeypatch.setattr(custom_targets, 'discover', discover)
    run(mcp_config_api.discover_mcp_files(request({'name':'remote','url':'https://new.invalid/mcp'})))
    assert discover.call_args.args[0]['headers'] == {}


@pytest.mark.parametrize('payload', [{}, {'workspace_id':'w','scope':'home'}])
def test_tenant_skill_install_cannot_write_global_roots(monkeypatch, payload):
    monkeypatch.delenv('TCODE_DESKTOP_MANAGED', raising=False)
    monkeypatch.setattr(skills_api, '_require_tenant_admin', lambda _: None)
    monkeypatch.setattr(websearch_config_api, 'auth_required', lambda: True)
    monkeypatch.setattr(websearch_config_api, 'require_current_user', lambda _: SimpleNamespace(role='admin',tenant_id='foreign'))
    monkeypatch.setattr(websearch_config_api.workdir_utils, 'default_tenant_id', lambda: 'default')
    installer = AsyncMock()
    monkeypatch.setattr(skills_api, 'install_skill_from_github', installer)
    with pytest.raises(HTTPException) as error:
        run(skills_api.install_skill(payload, request()))
    assert error.value.status_code == 403
    installer.assert_not_called()


def test_tenant_skill_install_uses_owned_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(skills_api, '_require_tenant_admin', lambda _: None)
    owner = workdirs.WorkspaceContext('t','u','w')
    monkeypatch.setattr(skills_api, '_workspace_context_from_request', lambda *a, **k: owner)
    monkeypatch.setattr(skills_api.workdirs, 'assert_workspace_active', lambda *a: None)
    monkeypatch.setattr(skills_api, '_resolve_user_workdir_path', lambda *a: tmp_path)
    installer = AsyncMock(return_value={'ok':True})
    monkeypatch.setattr(skills_api, 'install_skill_from_github', installer)
    assert run(skills_api.install_skill({'workspace_id':'w'}, request())) == {'ok':True}
    assert installer.call_args.kwargs['workdir'] == str(tmp_path)


@pytest.mark.parametrize('operation', ['read','download','rename','delete','upload'])
def test_workspace_parent_swap_cannot_cross_boundary(tmp_path, operation):
    root = tmp_path.resolve() / 'a'; other = tmp_path.resolve() / 'b'
    (root/'dir').mkdir(parents=True); other.mkdir()
    path = root/'dir'/'file.txt'; path.write_text('A'); (other/'file.txt').write_text('B private')
    incoming = tmp_path/'incoming'; incoming.write_text('new')
    (root/'dir').rename(root/'original'); (root/'dir').symlink_to(other, target_is_directory=True)
    with pytest.raises(HTTPException):
        if operation == 'read': managed_files.read(root,path)
        elif operation == 'download': managed_files.response(root,path)
        elif operation == 'rename': managed_files.change(root,path,'renamed.txt')
        elif operation == 'delete': managed_files.change(root,path)
        else:
            with managed_files.publish(root,path,incoming): pass
    assert (other/'file.txt').read_text() == 'B private'
    assert sorted(p.name for p in other.iterdir()) == ['file.txt']


def test_download_uses_private_snapshot_and_cleans_it(tmp_path):
    root=tmp_path.resolve(); path=root/'file.txt'; path.write_text('approved')
    response=managed_files.response(root,path,filename=path.name)
    copy=Path(response.path)
    path.unlink(); path.symlink_to(root/'private.txt'); (root/'private.txt').write_text('private')
    assert copy.read_text() == 'approved'
    run(response.background())
    assert not copy.exists()


def test_managed_upload_and_rename_preserve_existing_files(tmp_path):
    root=tmp_path.resolve(); source=root/'incoming'; source.write_text('new')
    path=root/'upload'/'nested'/'file.txt'
    with managed_files.publish(root,path,source) as first:
        assert first == path
    source.write_text('second')
    with managed_files.publish(root,path,source) as second:
        assert second != first
    assert managed_files.read(root,first) == b'new'
    managed_files.change(root,second,'renamed.txt')
    assert not second.exists()
    renamed=second.with_name('renamed.txt')
    assert renamed.read_text() == 'second'
    managed_files.change(root,renamed)
    assert not renamed.exists()


def test_file_http_upload_download_preview_and_delete(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from tcode_agent.research import ResearchEventSink
    root = tmp_path.resolve()
    owner = workdirs.WorkspaceContext('t','u','w')
    monkeypatch.setattr(files_api, '_workspace_context_from_request', lambda *a, **k: owner)
    monkeypatch.setattr(files_api, '_resolve_user_workdir_path', lambda *a: root)
    monkeypatch.setattr(files_api.project_roots, 'inventory', lambda *a: None)
    monkeypatch.setattr(files_api.project_files, 'binding', lambda *a: None)
    monkeypatch.setattr(files_api, '_require_visible_workspace_file', lambda path, _: (root, root/path))
    monkeypatch.setattr(files_api, '_workdir_name_from_path', lambda *a: 'w')
    monkeypatch.setattr(ResearchEventSink, 'append', lambda *a, **k: None)
    app = FastAPI(); app.include_router(files_api.router)
    with TestClient(app) as client:
        uploaded = client.post('/api/files/upload?workspace_id=w', files={'file':('note.txt',b'hello')})
        assert uploaded.status_code == 200, uploaded.text
        path = uploaded.json()['relative_path']
        duplicate = client.post('/api/files/upload?workspace_id=w', files={'file':('note.txt',b'second')})
        assert duplicate.status_code == 200
        assert duplicate.json()['relative_path'] != path
        assert client.get('/api/files/content',params={'path':path}).json()['content'] == 'hello'
        response=client.get('/api/files/download',params={'path':path},headers={'Range':'bytes=1-3'})
        assert response.status_code == 206 and response.content == b'ell'
        assert client.get('/api/files/inline',params={'path':path}).content == b'hello'
        (root/'preview.html').write_text('<h1>Preview</h1>')
        preview=client.get('/api/files/html-preview',params={'path':'preview.html'})
        assert preview.status_code == 200 and 'sandbox' in preview.headers['content-security-policy']
        assert client.patch('/api/files/entry',json={'path':path,'name':'renamed.txt'}).status_code == 200
        assert client.delete('/api/files/entry',params={'path':'upload/renamed.txt'}).status_code == 200
        assert not (root/'upload/renamed.txt').exists()


def test_upload_mirror_collision_retries_without_overwriting(tmp_path, monkeypatch):
    from io import BytesIO
    from starlette.datastructures import UploadFile
    from tcode_agent.research import ResearchEventSink
    from tcode_agent.runtime.sandbox.macos_native import WorkspaceFileExists
    root=tmp_path.resolve()
    owner=workdirs.WorkspaceContext('t','u','w')
    monkeypatch.setattr(files_api, '_workspace_context_from_request', lambda *a, **k: owner)
    monkeypatch.setattr(files_api, '_resolve_user_workdir_path', lambda *a: root)
    monkeypatch.setattr(files_api.project_files, 'binding', lambda *a: {'access':'rw'})
    mirror=Mock(side_effect=[WorkspaceFileExists('existing project file'),None])
    monkeypatch.setattr(files_api.project_files, 'mirror_upload', mirror)
    monkeypatch.setattr(files_api, '_workdir_name_from_path', lambda *a: 'w')
    monkeypatch.setattr(ResearchEventSink, 'append', lambda *a, **k: None)
    result=run(files_api.upload_file(request(),UploadFile(BytesIO(b'hello'),filename='note.txt'),session_id='',workdir_name='',workspace_id='w',relative_path=''))
    assert result['stored_filename'] != 'note.txt'
    assert Path(result['path']).read_bytes() == b'hello'
    assert not (root/'upload/note.txt').exists()
    assert mirror.call_count == 2


def test_delete_owned_session_still_cleans_up(monkeypatch):
    from tcode_agent.checkpoint.manager import CheckpointManager
    session=SimpleNamespace(tenant_id='t',user_id='u',workspace_id='w',metadata={},workdir='/unused')
    monkeypatch.setattr(session_lifecycle.SessionManager,'get',AsyncMock(return_value=session))
    delete=AsyncMock(); cancel=AsyncMock(); cleanup=AsyncMock()
    monkeypatch.setattr(session_lifecycle.SessionManager,'delete',delete)
    monkeypatch.setattr(session_lifecycle.ServerState,'get_turn_task',lambda _: None)
    monkeypatch.setattr(session_lifecycle.ServerState,'cancel_session',cancel)
    monkeypatch.setattr(session_lifecycle.ServerState,'cleanup_session',cleanup)
    for name in ('_delete_child_sessions','_cancel_symphony_issues_for_session','_terminate_session_processes'):
        monkeypatch.setattr(session_lifecycle,name,AsyncMock())
    monkeypatch.setattr(session_lifecycle.WorkerRegistry,'delete',AsyncMock())
    monkeypatch.setattr(session_lifecycle.SessionArchiveManager,'delete_bundle',Mock())
    monkeypatch.setattr(CheckpointManager,'delete',Mock())
    run(session_lifecycle._delete_session_tree_background('mine',owner_kwargs={'tenant_id':'t','user_id':'u'}))
    delete.assert_awaited_once_with('mine',tenant_id='t',user_id='u')
    cancel.assert_awaited_once_with('mine')
    cleanup.assert_awaited_once_with('mine')


def test_interrupted_download_removes_private_copy(tmp_path):
    root=tmp_path.resolve(); path=root/'file.txt'; path.write_text('data')
    response=managed_files.response(root,path)
    copy=Path(response.path)
    async def broken_send(message):
        raise OSError('client disconnected')
    with pytest.raises(OSError):
        run(response({'type':'http','method':'GET','headers':[]},AsyncMock(),broken_send))
    assert not copy.exists()
