"""No real settings or credentials: two accounts and tenants share one desktop config."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import web
from fastapi import HTTPException
import pytest

from tcode_agent.mcp.manager_auto import MCPManager
from tcode_agent.runtime.workspace_registry import registry_workspace
from tcode_agent.server import mcp_config_api as api
from tcode_agent.server.job_protocol import JobProtocol


def test_connections_follow_workspace_owner(tmp_path, monkeypatch):
    config = tmp_path / 'mcp.json'
    monkeypatch.setenv('TCODE_MCP_CONFIG', str(config))
    monkeypatch.setenv('TCODE_USER_WORKDIRS_ROOT', str(tmp_path))
    monkeypatch.setenv('TCODE_DESKTOP_PAUSE_REMOTE_SERVICES', '0')
    calls = []

    async def handle(req):
        msg = await req.json()
        calls.append((req.path, req.headers.get('Authorization'), msg['method']))
        if 'id' not in msg:
            return web.Response(status=202)
        result = {'initialize': {'protocolVersion': '2025-11-25', 'capabilities': {}},
                  'tools/list': {'tools': []}, 'ping': {}}[msg['method']]
        return web.json_response({'jsonrpc': '2.0', 'id': msg['id'], 'result': result})

    async def run():
        app = web.Application()
        app.router.add_post('/{name}', handle)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            base = f'http://127.0.0.1:{runner.addresses[0][1]}'
            config.write_text(json.dumps({'mine': {'url': base+'/mine', 'personal_artifact_id': 'files',
                'personal_artifact_owner': ['lab','alice'], 'headers': {'Authorization':'Bearer fixture'}},
                'common': {'url':base+'/common'}}))
            owners = [('lab','alice'), ('lab','bob'), ('other','alice'), ('lab','alice')]
            try:
                for tenant, user in owners:
                    with registry_workspace(tmp_path/tenant/user/'same'):
                        calls.clear()
                        await MCPManager.initialize()
                        await MCPManager.reload_config()
                        await MCPManager.ensure_session('common')
                        if (tenant,user) == owners[0]:
                            await MCPManager.ensure_session('mine')
                            assert ('/mine','Bearer fixture','ping') in calls
                        else:
                            with pytest.raises((RuntimeError, PermissionError)):
                                await MCPManager.ensure_session('mine')
                            assert 'mine' not in MCPManager._server_configs
                            assert all(path == '/common' and token is None for path,token,_ in calls)
            finally:
                for tenant,user in set(owners):
                    with registry_workspace(tmp_path/tenant/user/'same'):
                        await MCPManager.close()
        finally:
            await runner.cleanup()
    asyncio.run(run())


def test_save_and_discover_check_original_owner_before_using_headers(tmp_path, monkeypatch):
    path = tmp_path/'desktop.json'
    entry = {'url':'https://fixture.invalid/mcp','personal_artifact_id':'files',
             'personal_artifact_owner':['lab','alice'],'headers':{'Authorization':'Bearer fixture'}}
    path.write_text(json.dumps({'mine':entry}))
    monkeypatch.setenv('TCODE_DESKTOP_MANAGED','1')
    monkeypatch.setenv('TCODE_DESKTOP_MCP_CONFIG',str(path))
    monkeypatch.delenv('TCODE_MCP_CONFIG',raising=False)
    monkeypatch.setenv('TMCP_ARTIFACT_SERVICES','{}')
    monkeypatch.setattr(api,'_require_admin',lambda _:None)
    monkeypatch.setattr(api,'require_current_user',lambda r:r.user)
    probe = AsyncMock(return_value='files')
    monkeypatch.setattr(api,'_personal_artifact_id',probe)
    monkeypatch.setattr(api,'_reload',AsyncMock())
    req = SimpleNamespace(json=AsyncMock(return_value={'name':'mine','url':entry['url']}))
    for tenant,user in [('lab','bob'),('other','alice')]:
        req.user = SimpleNamespace(tenant_id=tenant,user_id=user)
        for action in (api._put_mcp_config('mine',req), api.discover_mcp_files(req), api.test_mcp_service('mine', req)):
            with pytest.raises(HTTPException) as denied:
                asyncio.run(action)
            assert denied.value.status_code == 403
        assert json.loads(path.read_text()) == {'mine':entry}
    probe.assert_not_awaited()
    req.user = SimpleNamespace(tenant_id='lab',user_id='alice')
    assert asyncio.run(api._put_mcp_config('mine',req))['saved']
    probe.assert_awaited_once_with(entry['url'],entry['headers'])
    req.json.return_value['personal_artifact'] = False
    asyncio.run(api._put_mcp_config('mine',req))
    assert 'headers' not in json.loads(path.read_text())['mine']


@pytest.mark.parametrize('tenant,user',[('lab','bob'),('other','alice'),('','')])
def test_static_personal_target_denies_before_connect(tenant,user):
    protocol = SimpleNamespace(scope=JobProtocol.scope, static_targets=lambda:{'mine':{
        'personal_artifact_id':'files','personal_artifact_owner':['lab','alice']}})
    with pytest.raises(ValueError,match='不属于'):
        asyncio.run(JobProtocol.tool(protocol,dict(server='mine',tool='echo',tenant_id=tenant,user_id=user)))
