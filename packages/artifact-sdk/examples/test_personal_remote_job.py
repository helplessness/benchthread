"""Real loopback personal MCP with a restart; only echoes fixture bytes."""
import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from tcode_artifacts.personal_tokens import PersonalTokens


def test_personal_job_restart(tmp_path):
    asyncio.run(_round_trip(tmp_path))


async def _round_trip(data):
    root = Path(__file__).resolve().parents[3]
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    tokens = PersonalTokens(data)
    a, b = (tokens.manage('issue', name) for name in ('alice', 'bob'))
    env = dict(PATH=os.environ['PATH'], HOME=str(data), EXAMPLE_DATA_ROOT=str(data),
               EXAMPLE_PORT=str(port), EXAMPLE_AUTH_MODE='personal-tokens',
               TMCP_ARTIFACT_SERVICE_ID='test', PYTHONPATH=str(root / 'packages/artifact-sdk/src'))
    def start():
        return subprocess.Popen([sys.executable, str(root / 'examples/third_party_mcp/remote_server.py')],
                                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    async def ready():
        async with httpx.AsyncClient(trust_env=False) as client:
            for _ in range(150):
                assert p.poll() is None
                try:
                    if (await client.get(url+'/artifact/health')).status_code == 401:
                        return
                except httpx.ConnectError:
                    pass
                await asyncio.sleep(.05)
        raise AssertionError('loopback service not ready')
    async def tool(token, name, args):
        async with Client(StreamableHttpTransport(url+'/mcp', headers={'Authorization':'Bearer '+token})) as c:
            return await c.call_tool(name, args, raise_on_error=False)
    def upload(token, content):
        r = httpx.post(url+'/artifact/api/v1/artifacts', headers={'X-Artifact-Token': token},
                       files={'file':('same.txt', content)}, trust_env=False)
        r.raise_for_status()
        return r.json()['ref']
    def args(ref):
        return dict(request_id='same-request', tool='predict', arguments={'input_file':ref, 'delay_seconds':2 if ref==ref_a else 30})

    p = start()
    try:
        await ready()
        ref_a, ref_b = upload(a, b'A'), upload(b, b'B')
        assert (await tool(b, 'submit_prediction', args(ref_a))).is_error
        ja = (await tool(a, 'submit_prediction', args(ref_a))).data['job_id']
        jb = (await tool(b, 'submit_prediction', args(ref_b))).data['job_id']
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(data/'personal-jobs'/tokens.owner(b)/'jobs.sqlite')) as db:
            job_token, = db.execute('SELECT credential FROM worker_files WHERE id=?',(jb,)).fetchone()
        worker_headers = {'X-Artifact-Token':job_token}
        for route in ('files','artifacts/'+ref_a['artifact_id']+'/content'):
            assert httpx.get(url+'/artifact/api/v1/'+route,headers=worker_headers,trust_env=False).status_code == 403
        assert ja != jb
        assert (await tool(a, 'submit_prediction', args(ref_a))).data['job_id'] == ja
        p.terminate(); p.wait(10)
        p = start()
        await ready()
        for name in ('get_prediction_status','cancel_prediction','get_prediction_result'):
            assert (await tool(b, name, {'job_id':ja})).is_error
        owner = tokens.owner(a)
        dbpath = data/'personal-jobs'/owner/'jobs.sqlite'
        with closing(sqlite3.connect(dbpath)) as db:
            for _ in range(120):
                state, = db.execute('SELECT status FROM jobs WHERE id=?',(ja,)).fetchone()
                if state in ('completed','failed'): break
                await asyncio.sleep(.1)
            assert state == 'completed'
            assert db.execute('SELECT count(*) FROM worker_files').fetchone()[0] == 0
        a = tokens.manage('rotate', 'alice')
        result = await tool(a, 'get_prediction_result', {'job_id':ja})
        assert not result.is_error
        bundle = result.data['output_bundle']['bundle_id']
        endpoint = url+'/artifact/api/v1/bundles/'+bundle
        meta = httpx.get(endpoint, headers={'X-Artifact-Token':a}, trust_env=False).json()
        file = next(f for f in meta['files'] if f['relative_path']=='inputs/input.dat')
        content = url+'/artifact/api/v1/artifacts/'+file['artifact_id']+'/content'
        assert httpx.get(content, headers={'X-Artifact-Token':a}, trust_env=False).content == b'A'
        assert httpx.get(content, headers={'X-Artifact-Token':b}, trust_env=False).status_code == 404
        assert (await tool(b, 'cancel_prediction', {'job_id':jb})).data['status']=='cancelled'
        assert httpx.get(url+'/artifact/api/v1/artifacts/'+ref_b['artifact_id'],headers=worker_headers,trust_env=False).status_code == 401
    finally:
        if p.poll() is None:
            p.terminate(); p.wait(10)
