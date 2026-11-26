"""Exercise the real Streamable HTTP broker and durable echo recovery."""
import asyncio
from contextlib import ExitStack, asynccontextmanager, contextmanager, nullcontext, suppress
import json
import os
from pathlib import Path
import socket
import sys

import httpx
import pytest
import uvicorn

from tcode_agent.server.managed_job_protocol import (
    LOCAL_TOKEN, WORKER_HEADER, WORKER_TOKEN, bridge_worker_environment, managed_job_protocol,
)


@pytest.mark.parametrize("receipt", [
    {"job_id": "tcode-job-fixture", "job_ref": {"broker": "tcode-local"}},
    {"job_ref": {"broker": "tcode-local", "job_id": "tcode-job-fixture"}},
    {"job_ref": {"job_id": "tcode-job-fixture"}},
])
def test_built_in_issue_carries_tracker_details_before_runtime_projection(monkeypatch, receipt):
    from unittest.mock import AsyncMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from tcode_agent.server import symphony_api

    issues = [
        {"identifier": "LOCAL-BUILTIN", "state": "In Progress", "created_at": "2026-01-01T00:00:00Z",
         "workspace": receipt.copy()},
        {"identifier": "LOCAL-EXTERNAL", "state": "Done", "created_at": "2026-01-01T00:00:00Z",
         "workspace": {"job_id": "external-fixture"}},
        {"identifier": "LOCAL-EXTERNAL-REF", "state": "Done",
         "workspace": {"job_ref": {"job_id": "external-ref-only"}}},
    ]
    monkeypatch.setattr(symphony_api, "_load_visible_symphony_issues", AsyncMock(return_value=(issues, len(issues), [])))
    monkeypatch.setattr(symphony_api, "_reconcile_terminal_symphony_runtimes", AsyncMock())
    monkeypatch.setattr("tcode_agent.server.job_workspace._find_matching_reviews_for_workspace", lambda _: [])
    app = FastAPI()
    app.include_router(symphony_api.router)
    with TestClient(app) as client:
        built_in, external, external_ref = client.get("/api/symphony/local-issues").json()["issues"]
    assert built_in["workspace"]["job_id"] == "tcode-job-fixture"
    assert built_in["workspace"]["issue_identifier"] == "LOCAL-BUILTIN"
    assert built_in["workspace"]["timeline"] == built_in["timeline"]
    assert any(item["kind"] == "submitted" for item in built_in["timeline"])
    assert "timeline" not in external["workspace"]
    assert "issue_identifier" not in external["workspace"]
    assert "job_id" not in external_ref["workspace"] and not external_ref["timeline"]


@pytest.mark.parametrize("managed,legacy,override,expected", [
    (True, False, "http://127.0.0.1:10121", "builtin"),
    (None, True, "http://127.0.0.1:10121/mcp/", "legacy"),
    (True, True, "http://127.0.0.1:10122", "external"),
    (None, True, "https://gateway.example", "external"),
    (False, True, "http://127.0.0.1:10122", "external"),
])
def test_desktop_owned_gateway_priority(tmp_path, monkeypatch, managed, legacy, override, expected):
    from tcode_agent.server import desktop_launcher as launcher
    from tcode_agent.server import managed_job_protocol as broker

    path = tmp_path / "desktop-mcp.json"
    url = "http://127.0.0.1:10121/mcp"
    if legacy:
        db = tmp_path / ".tcode/job-protocol/jobs.sqlite3"
        db.parent.mkdir(parents=True)
        db.touch()
    path.write_text(json.dumps({"$desktop": {} if managed is None else {"managed_job_protocol": managed},
                                "job-protocol-mcp": {"url": url}}))
    monkeypatch.setenv("TCODE_DESKTOP_MCP_CONFIG", str(path))
    monkeypatch.setenv("TCODE_DESKTOP_PAUSE_REMOTE_SERVICES", "0")
    monkeypatch.setenv("TCODE_PLUGIN_JOB_PROTOCOL_URL", override)
    monkeypatch.setattr(launcher, "start_managed_job_protocol", lambda *_: (None, "legacy"))
    monkeypatch.setattr(broker, "managed_job_protocol", lambda *a, **kw: nullcontext("builtin"))
    with ExitStack() as stack:
        assert launcher.start_optional_job_protocol(tmp_path, tmp_path, stack) == (None, "" if expected == "external" else expected)
    if expected == "external":
        assert os.environ["TCODE_PLUGIN_JOB_PROTOCOL_URL"] == (url.removesuffix("/mcp") if managed is False else override)


@asynccontextmanager
async def echo_server(*, before_echo_return=None):
    from fastmcp import FastMCP

    server = FastMCP("Disposable echo")
    calls = {}
    remote = {}

    @server.tool
    async def echo_text(text: str) -> dict:
        calls[text] = calls.get(text, 0) + 1
        if before_echo_return is not None:
            await before_echo_return()
        return {"text": text}

    @server.tool
    async def slow_echo(text: str) -> dict:
        calls[text] = calls.get(text, 0) + 1
        await asyncio.sleep(10)
        return {"text": text}

    @server.tool
    def submit_echo(request_id: str, tool: str, arguments: dict) -> dict:
        calls[request_id] = calls.get(request_id, 0) + 1
        remote.setdefault(request_id, {"status": "running", "text": arguments["text"]})
        if arguments["text"] == "lost echo receipt":
            raise RuntimeError("echo ran, but no job receipt was returned")
        return {"job_id": "remote-" + request_id}

    @server.tool
    def echo_status(job_id: str) -> dict:
        assert job_id.startswith("remote-")
        job_id = job_id.removeprefix("remote-")
        if remote[job_id].get("offline"):
            remote[job_id]["outage_observed"] = True
            raise RuntimeError("temporary status outage")
        remote[job_id]["polled"] = True
        return {"status": remote[job_id]["status"]}

    @server.tool
    def echo_result(job_id: str) -> dict:
        assert job_id.startswith("remote-")
        record = remote[job_id.removeprefix("remote-")]
        if record.get("result_unavailable"):
            raise RuntimeError("temporary result outage")
        return {"text": record["text"]}

    @server.tool
    def lookup_echo(request_id: str) -> dict:
        return {"job_id": "remote-" + request_id} if request_id in remote else {}

    @server.tool
    def cancel_echo(job_id: str) -> dict:
        assert job_id.startswith("remote-")
        remote[job_id.removeprefix("remote-")]["status"] = "cancelled"
        return {"status": "cancelled"}

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        http = uvicorn.Server(uvicorn.Config(server.http_app(), log_level="error"))
        task = asyncio.create_task(http.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(5):
                while not http.started:
                    await asyncio.sleep(0.02)
            yield f"http://127.0.0.1:{sock.getsockname()[1]}/mcp", calls, remote
        finally:
            http.should_exit = True
            with suppress(asyncio.CancelledError):
                await task


@pytest.mark.parametrize("prepare", [True, False], ids=["prepare-before-result", "submit-only"])
def test_chat_prepares_check_before_real_echo_returns(tmp_path, monkeypatch, prepare):
    """Script the model, not the clock: hold the actual remote response until its check exists."""
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from tcode_agent.agent.agent import Agent
    from tcode_agent.mcp import _proxy
    from tcode_agent.mcp.manager_auto import MCPManager
    from tcode_agent.providers.base import ChatResponse, ToolDefinition
    from tcode_agent.tools.base import ToolContext
    from tcode_agent.types import AgentInfo, ToolState

    monkeypatch.chdir(tmp_path)
    for key,value in {'TCODE_WORKDIR':str(tmp_path),'TCODE_CONFIG_ROOT':str(tmp_path),
                      'TCODE_MCP_CONFIG':str(tmp_path/'mcp.json'),'TCODE_ASYNC_EXECUTION_MODE':'bridge',
                      'TCODE_ASYNC_ACCEPT_END_TURN':'0','TCODE_SANDBOX_ENABLED':'false',
                      'TCODE_DESKTOP_MANAGED':'0','TCODE_AUTH_REQUIRED':'0','TCODE_RESEARCH_ENABLED':'false'}.items():
        monkeypatch.setenv(key,value)
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        script = tmp_path/'verify_echo.py'
        log = []
        async def hold():
            started.set()
            await asyncio.wait_for(release.wait(), 15)
            assert script.is_file() == prepare
            log.append('remote returned')
        async with echo_server(before_echo_return=hold) as (url,calls,_):
            config = tmp_path/'mcp.json'
            config.write_text(json.dumps({'simple-demo-mcp': {'url':url,'invocation_mode':'async'}}))
            with managed_job_protocol(tmp_path,configs=[config]) as gateway:
                headers = {'Authorization':'Bearer '+os.environ[LOCAL_TOKEN],WORKER_HEADER:bridge_worker_environment()[WORKER_TOKEN]}
                async with Client(StreamableHttpTransport(gateway,headers=headers)) as worker:
                    async def describe(server, tool, args):
                        assert (server,tool)==('job-protocol-mcp','describe_target_tool')
                        return {'structuredContent':(await worker.call_tool(tool,args)).structured_content}
                    monkeypatch.setattr(MCPManager,'is_initialized',lambda:True)
                    monkeypatch.setattr(MCPManager,'call_tool',describe)
                    submit = 'mcp_job-protocol-mcp_submit_job'
                    poll = 'mcp_job-protocol-mcp_get_job_status'
                    agent = Agent(AgentInfo(name='build',steps=6),SimpleNamespace(name='fixture'),'chat-echo',str(tmp_path))
                    agent.set_permission_handler(AsyncMock(return_value=True))
                    engine = agent._engine
                    defs = [ToolDefinition(n,n,{},provider_id='tcode.builtin' if n=='write' else 'mcp:job-protocol-mcp')
                            for n in [submit,poll,'write']]
                    monkeypatch.setattr(engine,'_safe_get_tool_definitions',AsyncMock(return_value=defs))
                    monkeypatch.setattr(engine,'_safe_preload_recommended_skill',AsyncMock(return_value=None))
                    monkeypatch.setattr(agent,'_try_async_followup_collect',AsyncMock(side_effect=AssertionError('inline poll')))
                    receipt = None
                    normal_execute = engine._execute_turn_tool
                    async def execute(call, **step):
                        nonlocal receipt
                        ctx = ToolContext('chat-echo','msg','build',None,extra={'workdir':str(tmp_path),
                            'tenant_id':'test','user_id':'alice','workspace_id':'work'})
                        if call.tool_name==submit:
                            assert receipt is None
                            result = await _proxy._submit_authorized_job(_proxy._ProxyTool(server_name='job-protocol-mcp',
                                tool_name='submit_job'),ctx,'simple-demo-mcp','echo_text','simple-demo-mcp',
                                {'text':'preparation echo'},'echo_text', 'notify')
                            assert result.metadata.get('accepted_async'), result
                            receipt = (await worker.call_tool('submit_job',{'input':result.metadata['submit_input']})).structured_content
                            await asyncio.wait_for(started.wait(),5)
                            log.append('accepted')
                        else:
                            assert call.tool_name=='write' and not release.is_set()
                            result = await normal_execute(call, **step)
                            assert not result.error and script.is_file()
                            log.append('script written')
                        call.metadata,call.output,call.state = result.metadata,result.output,ToolState.COMPLETED
                        return result
                    monkeypatch.setattr(engine,'_execute_turn_tool',execute)
                    def tc(name,args,id):
                        return {'function':{'name':name,'arguments':args},'id':id}
                    async def model(tools, *, iteration, **_):
                        if iteration==1:
                            calls_ = [tc(submit,{'arguments':{'text':'preparation echo'}},'first'),
                                      tc(poll,{},'premature'),tc(submit,{'arguments':{'text':'preparation echo'}},'duplicate')]
                        elif iteration==2:
                            assert not any('LOCAL-' in m.content for m in agent.messages if m.role == 'tool')
                            assert {t.name for t in tools}=={'write'} and '不依赖' in next(
                                m.content for m in agent.messages if m.role=='tool' and '受理' in m.content)
                            if not prepare:
                                return ('已受理，等待回传。',None,ChatResponse(content='已受理，等待回传。')), []
                            calls_ = [tc('write',{'filePath':script.name,'content':
                                'import sys, json\nassert json.load(sys.stdin)["text"] == "preparation echo"\n'},'write')]
                        else:
                            assert iteration == 3
                            calls_ = [tc(poll,{},'hallucinated-poll')]
                        return ('',calls_,ChatResponse(content='')), []
                    monkeypatch.setattr(engine,'_call_llm_with_guidance_race',model)
                    try:
                        answer = await engine.run('提交一次 echo_text，文本为 preparation echo。'
                                                  + ('同时准备正式的回显核对脚本。' if prepare else '只提交。'))
                        assert '等待' in answer.content and log == (['accepted','script written'] if prepare else ['accepted'])
                    finally:
                        release.set()
                    async with asyncio.timeout(8):
                        while (await worker.call_tool('get_job_status',{'input':receipt})).structured_content['status']!='completed':
                            await asyncio.sleep(.05)
                    result = (await worker.call_tool('collect_job_result',{'input':receipt})).structured_content['target_result']
                    if prepare:
                        proc = await asyncio.create_subprocess_exec(sys.executable,'-I',str(script),stdin=asyncio.subprocess.PIPE)
                        await proc.communicate(json.dumps(result).encode())
                        assert proc.returncode == 0
                    assert result == {'text':'preparation echo'} and calls=={'preparation echo':1} and log[-1]=='remote returned'
    asyncio.run(scenario())


@pytest.mark.parametrize("prepare,return_during_round,model_timeout,control", [
    (True, False, False, ''), (True, True, False, ''), (False, False, False, ''), (False, False, True, ''),
    (True, False, False, 'paused'), (True, False, False, 'stopped')])
def test_approved_goal_prepares_once_then_resumes_on_echo(tmp_path, monkeypatch, prepare, return_during_round, model_timeout, control):
    import time
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    from tcode_agent.agent.agent import Agent
    from tcode_agent.providers.base import ChatResponse, ToolDefinition
    from tcode_agent.runtime import async_watcher as watcher
    from tcode_agent.runtime.shared_tracker import SharedTracker
    from tcode_agent.session.manager import SessionManager
    from tcode_agent.server import _shared, goal_mode, native_execution
    from tcode_agent.server.goal_computation import resume_approved_computation
    from tcode_agent.types import AgentInfo

    monkeypatch.chdir(tmp_path)
    config = tmp_path / 'mcp.json'
    root = str(tmp_path)
    for key, value in dict(WORKDIR=root, CONFIG_ROOT=root, MCP_CONFIG=str(config), ASYNC_EXECUTION_MODE='bridge',
                           SANDBOX_ENABLED='false', DESKTOP_MANAGED='0', AUTH_REQUIRED='0', RESEARCH_ENABLED='false').items():
        monkeypatch.setenv('TCODE_' + key, value)
    monkeypatch.setattr(SessionManager, '_sessions', {})
    monkeypatch.setattr(SessionManager, '_lock', None)  # each fixture has its own server event loop
    monkeypatch.setattr(watcher, '_registry', {})
    monkeypatch.setattr(watcher, '_injected', set())
    monkeypatch.setattr(watcher, 'start_watcher', AsyncMock())  # drive its real delivery step at the barrier
    script = tmp_path / 'check_echo.py'
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        async def hold():
            started.set()
            await asyncio.wait_for(release.wait(), 20)
            assert script.exists() == prepare
        async with echo_server(before_echo_return=hold) as (url, calls, _):
            config.write_text(json.dumps({'simple-demo-mcp': {'url': url, 'invocation_mode': 'async'}}))
            with managed_job_protocol(tmp_path, configs=[config]) as gateway:
                headers = {'Authorization':'Bearer '+os.environ[LOCAL_TOKEN], WORKER_HEADER:bridge_worker_environment()[WORKER_TOKEN]}
                async with Client(StreamableHttpTransport(gateway,headers=headers)) as worker:
                    goal = dict(id='echo-goal', objective='核对回显' if prepare else '等待回显', criteria='收到真实回显',
                                status='needs_input', updated_at=time.time(), deadline=time.time()+90, rounds_used=1,
                                max_rounds=6, steps_per_round=4, run_ids=['goal-first'])
                    session = await SessionManager.create(workdir=root, agent_name='build', metadata={'goal_mode':goal})
                    service = goal_mode.GoalModeService()
                    monkeypatch.setattr(goal_mode, 'goal_service', service)
                    # This integration must be driven by the actual producers,
                    # never by a fallback scan discovering the test session.
                    monkeypatch.setattr(SessionManager, 'list', AsyncMock(return_value=[]))
                    agent = Agent(AgentInfo(name='build', steps=4, permission={'write':'allow'}),
                                  SimpleNamespace(name='fixture'),session.id,root)
                    monkeypatch.setattr(goal_mode.ServerState,'get_or_create_agent',lambda *a,**kw:agent)
                    monkeypatch.setattr(native_execution,'open_native_agent',AsyncMock(return_value=(agent,None)))
                    monkeypatch.setattr('tcode_agent.mcp.route_permissions.get_agent_allowed_routed_servers',lambda _: {'simple-demo-mcp'})
                    async def describe(server, tool, args):
                        assert (server,tool)==('job-protocol-mcp','describe_target_tool')
                        return {'structuredContent':(await worker.call_tool(tool,args)).structured_content}
                    monkeypatch.setattr('tcode_agent.mcp.manager_auto.MCPManager.is_initialized',lambda:True)
                    monkeypatch.setattr('tcode_agent.mcp.manager_auto.MCPManager.call_tool',describe)
                    review = SharedTracker.upsert_review(dict(id='echo-review', identifier='REVIEW-echo', status='approved',
                        tool_server='simple-demo-mcp', tool_name='echo_text',tool_args={'text':'goal echo'},
                        review_data={'session_id':session.id,'origin_request_id':'goal-first','proxy_source':'job_protocol_submit',
                                     'goal_computation':{'goal_id':goal['id'],'max_submissions':1}}),root)
                    async def deliver():
                        release.set()
                        async with asyncio.timeout(8):
                            while (await worker.call_tool('get_job_status',{'input':ref})).structured_content['status'] != 'completed':
                                await asyncio.sleep(.05)
                        result = (await worker.call_tool('collect_job_result',{'input':ref})).structured_content
                        SharedTracker.patch_task(identifier=receipt['identifier'], status='completed', result_summary=json.dumps(result),
                            workspace_patch={'job_id':ref['job_id'], 'job_ref':ref,'transfer_state':'ready'}, workdir=root)
                        await watcher._check_completed_jobs(workdir=root)
                        result_key = (session.id, goal['id'], 'result:' + receipt['identifier'])
                        assert (result_key in service._seen) == (not control)
                        # Replay the same completion, independently of the watcher's own dedup.
                        stored = await SessionManager.get(session.id)
                        service.result_delivered(stored, 'goal-first', receipt['identifier'])

                    engine = agent._engine
                    submit = 'mcp_job-protocol-mcp_submit_job'
                    poll = 'mcp_job-protocol-mcp_get_job_status'
                    defs = [ToolDefinition(n,n,{},provider_id='tcode.builtin' if n=='write' else 'mcp:broker') for n in ('write',poll,submit)]
                    monkeypatch.setattr(engine,'_safe_get_tool_definitions',AsyncMock(return_value=defs))
                    monkeypatch.setattr(agent,'_discoverable_tool_definitions',lambda: defs)
                    def tc(name,args):
                        return {'id':name,'function':{'name':name,'arguments':args}}
                    async def model(tools, *, iteration, **_):
                        assert {t.name for t in tools} == {'write'}
                        if iteration==1 and prepare:
                            return ('',[tc(submit,{}),tc('write',{'filePath':script.name,'content':
                                'import json,sys\nassert json.load(sys.stdin)["target_result"]["text"] == "goal echo"\n'}),tc(poll,{})],
                                    ChatResponse(content='')),[]
                        return ('等待回传。',None,ChatResponse(content='等待回传。')),[]
                    monkeypatch.setattr(engine,'_call_llm_with_guidance_race',model)
                    phases = []
                    async def turn(*, run_input, **_):
                        phases.append(agent._goal_queue_preparation)
                        if agent._goal_queue_preparation:
                            await asyncio.wait_for(started.wait(),5)
                            assert '不依赖' in run_input.latest_user_text() and not release.is_set()
                            if model_timeout:
                                raise TimeoutError('optional preparation unavailable')
                            await engine.run(run_input.latest_user_text())
                            assert script.exists() == prepare
                            if prepare:
                                assert {c.tool_name for m in agent.messages if m.role=='tool' for c in (m.tool_calls or [])
                                        if (c.metadata or {}).get('reason')=='async_preparation'} == {submit,poll}
                            if return_during_round:
                                await deliver()
                        else:
                            events = (await SessionManager.get(session.id)).metadata['async_completion_events']
                            event = next(e for e in events if e['identifier']==receipt['identifier'])
                            assert event['status']=='completed'
                            if prepare:
                                verified = await asyncio.create_subprocess_exec(sys.executable,'-I',str(script),stdin=asyncio.subprocess.PIPE)
                                await verified.communicate(SharedTracker.get_task(identifier=event['identifier'],workdir=root)['result_summary'].encode())
                                assert verified.returncode==0
                            agent._goal_checkpoint_callback({'state':'blocked','progress':'已收到并核对回显','next_step':'等待用户确认'})
                    monkeypatch.setattr(native_execution,'run_native_turn',turn)
                    async def until(status, rounds):
                        async with asyncio.timeout(6):
                            while True:
                                current = goal_mode.public_goal(await SessionManager.get(session.id))
                                if current['status'] == status and current['rounds_used'] == rounds:
                                    return current
                                await asyncio.sleep(.01)
                    await service.start()
                    try:
                        admitted = await _shared._resume_session_after_human_review(review)
                        assert admitted['started'], admitted
                        receipt = admitted['approved_action']
                        ref = (await worker.call_tool('submit_job',{'input':receipt['submit_input']})).structured_content
                        if not return_during_round:
                            interim = await until('waiting_external',2)
                            assert phases==[True] and not interim.get('validation')
                            if control:
                                await goal_mode.change_goal(session.id,goal_mode.owner(session),goal['id'],status=control)
                            await deliver()
                        if control:
                            # Drain a wake queued just before a user stop/pause as well.
                            await service.tick((session.id,goal['id']))
                            assert phases==[True] and (await until(control,2))['status']==control
                            assert (await SessionManager.get(session.id)).metadata['async_completion_events']
                        else:
                            resumed = await until('needs_input',3)
                            assert phases==[True,False] and resumed['progress']=='已收到并核对回显', resumed
                        assert not (await resume_approved_computation(review))['started'] and calls=={'goal echo':1}
                    finally:
                        release.set()
                        await service.stop()
    asyncio.run(run())


def test_preparation_requires_spare_round_and_own_live_job(monkeypatch):
    from types import SimpleNamespace
    from tcode_agent.server.goal_mode import _can_prepare_while_waiting, TaskRuntimeStore
    session = SimpleNamespace(id='mine', workdir='unused')
    pending = {'pending_preparation_job':'LOCAL-one', 'status':'waiting_external','rounds_used':1,'max_rounds':3}
    row = {'session_id':session.id,'runtime_kind':'mcp_job','status':'queued'}
    monkeypatch.setattr(TaskRuntimeStore,'get',lambda *a,**kw:row)
    def allowed(waiting='waiting_external', **changes):
        return _can_prepare_while_waiting(session,pending | changes,waiting)
    assert allowed()
    assert not allowed(max_rounds=2) and not allowed(questions=[{'status':'pending'}])
    assert not allowed(waiting='needs_input') and not allowed(pending_preparation_job='')
    for status in ('paused','stopped','recovering','budget_exhausted'):
        assert not allowed(status=status)
    for status in ('completed','collecting','cancelled','failed'):
        row['status'] = status
        assert not allowed()
    row.update(session_id='other',status='running')
    assert not allowed()


def test_goal_guidance_wakes_only_owner_and_scan_recovers_missed_signal(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from tcode_agent.server import goal_api, goal_mode
    from tcode_agent.session.manager import SessionManager

    async def scenario():
        service = goal_mode.GoalModeService()
        service.fallback_seconds = .25
        sessions = {sid: SimpleNamespace(id=sid, workdir=str(tmp_path), user_id='user', tenant_id='tenant',
            workspace_id='ws', messages=[], metadata={'goal_mode': dict(id='goal-'+sid, status='needs_input',
            objective='echo', criteria='echo', updated_at=time.time(), deadline=time.time()+60, rounds_used=1,
            max_rounds=5, steps_per_round=3)}) for sid in ('a','b')}
        async def get(sid, **_): return sessions[sid]
        async def mutate(sid, fn, **_): return fn(sessions[sid])
        monkeypatch.setattr(SessionManager,'get',get)
        monkeypatch.setattr(SessionManager,'mutate',mutate)
        listed = []
        monkeypatch.setattr(SessionManager,'list', AsyncMock(side_effect=lambda **_: list(listed)))
        monkeypatch.setattr(goal_api,'goal_service',service)
        monkeypatch.setattr(goal_api,'scoped_session',AsyncMock(return_value=(sessions['a'],{})))
        seen = []
        ran = {sid:asyncio.Event() for sid in sessions}
        async def round_(session, goal):
            seen.append(session.id)
            ran[session.id].set()
        monkeypatch.setattr(service,'_round',round_)
        await service.start()
        try:
            body = goal_api.GoalAction(workspace_id='ws', goal_id='goal-a', action='guide', note='补充目标说明')
            await goal_api._apply_goal_action('a',body,None)
            await asyncio.wait_for(ran['a'].wait(),2)
            for _ in range(3):
                service.wake('a','goal-a','guidance:1')
                service.wake('a','replaced-goal','late-result')
            sessions['b'].metadata['goal_mode']['status'] = 'ready'
            listed.append(sessions['b'])  # simulate a missed notification, recovered by scan
            await asyncio.wait_for(ran['b'].wait(),2)
            assert seen == ['a','b']
            sessions['a'].metadata['goal_mode']['status'] = 'paused'
            await goal_api._apply_goal_action('a',body,None)
            assert sessions['a'].metadata['goal_mode']['status'] == 'paused'
            assert ('a','goal-a','guidance:2') not in service._seen
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_only_ordinary_accepted_submission_can_prepare(monkeypatch):
    from types import SimpleNamespace
    from tcode_agent.harness.engine import _ordinary_queue_receipt
    from tcode_agent.runtime.async_resume import active_resume
    from tcode_agent.types import ToolCall, ToolResult
    monkeypatch.setenv('TCODE_ASYNC_ACCEPT_END_TURN','0')
    submit = ToolCall(tool_name='mcp_job-protocol-mcp_submit_job',arguments={},call_id='submit')
    data = dict(identifier='LOCAL-test',identifier_kind='local_acceptance_id',tracker='sqlite',
                status='queued',accepted_async=True)
    def eligible(agent, **extra):
        return _ordinary_queue_receipt(agent,submit,ToolResult(title='receipt',output='',metadata=data|extra))
    agent = SimpleNamespace()
    assert eligible(agent)
    from tcode_agent.harness.engine import AgentHarnessEngine
    receipt = ToolResult(title='receipt', output='accepted LOCAL-test', metadata=data)
    public = AgentHarnessEngine._build_terminal_turn_response(None, receipt)
    assert '已受理' in public and 'LOCAL-test' not in public
    assert receipt.metadata['identifier'] == 'LOCAL-test'
    for overrides in [{'human_review_required':True},{'status':'unknown'},{'accepted_async':False},
                      {'identifier_kind':'remote_job_id'},{'tool_loop_terminal':True}]:
        assert not eligible(agent,**overrides)
    for override in [dict(_active_tool_trace_context={'workflow_run_id':'workflow'}),
                     dict(_goal_checkpoint_callback=lambda:None),dict(_active_async_completion_receipt={'id':'receipt'})]:
        assert not eligible(SimpleNamespace(**override))
    token = active_resume.set({'identifier':'LOCAL-old'})
    try:
        assert not eligible(agent)
    finally:
        active_resume.reset(token)
    monkeypatch.setenv('TCODE_ASYNC_ACCEPT_END_TURN','1')
    assert not eligible(agent)


@pytest.mark.parametrize("launcher_kind", ["desktop", "web"])
def test_real_echo_and_remote_job_restart(tmp_path, monkeypatch, launcher_kind):
    pytest.importorskip("fastmcp")
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    from tcode_agent.runtime.remote_cancel import cancellation_receipt

    monkeypatch.delenv("TMCP_TARGET_REGISTRY_TOKEN", raising=False)
    config = tmp_path / "mcp.json"
    scope = {"tenant_id": "test", "user_id": "alice", "workspace_id": "work"}
    name = "plugin-" + "a" * 32

    @contextmanager
    def start_gateway(config):
        if launcher_kind == "web":
            # The Web launcher uses this same managed service (the actual
            # launcher + worker path is exercised in the Web Tracker test).
            with managed_job_protocol(tmp_path, configs=[config]) as url:
                yield url
        else:
            from tcode_agent.server.desktop_launcher import start_optional_job_protocol

            with monkeypatch.context() as env, ExitStack() as stack:
                env.setenv("TCODE_DESKTOP_MCP_CONFIG", str(config))
                env.setenv("TCODE_DESKTOP_PAUSE_REMOTE_SERVICES", "0")
                # A previous launch may have persisted its address in both places.
                previous = json.loads(config.read_text()).get("job-protocol-mcp")
                if previous:
                    env.setenv("TCODE_PLUGIN_JOB_PROTOCOL_URL", previous["url"].removesuffix("/mcp"))
                else:
                    env.delenv("TCODE_PLUGIN_JOB_PROTOCOL_URL", raising=False)
                worker, url = start_optional_job_protocol(tmp_path, tmp_path, stack)
                assert worker is None and url
                yield url

    async def call(client, tool, data):
        result = await client.call_tool(tool, {"input": data})
        return result.structured_content

    def client(url, *, worker=True):
        headers = {"Authorization": "Bearer " + os.environ[LOCAL_TOKEN]}
        if worker:
            headers[WORKER_HEADER] = bridge_worker_environment()[WORKER_TOKEN]
        return Client(StreamableHttpTransport(url, headers=headers))

    async def check():
        async with echo_server() as (endpoint, calls, remote), echo_server() as (new_endpoint, new_calls, _):
            config.write_text(json.dumps({"simple-demo-mcp": {"url": endpoint, "invocation_mode": "async"},
                                          "boltz-mcp": {"url": endpoint, "invocation_mode": "async"}}))
            data = {**scope, "run_id": "run-echo", "target_server": "simple-demo-mcp",
                    "target_tool": "echo_text", "arguments": {"text": "local echo"}}
            with start_gateway(config) as url:
                with pytest.raises(BlockingIOError):
                    with managed_job_protocol(tmp_path, configs=[config]):
                        pytest.fail("started a second gateway for the same state")
                async with httpx.AsyncClient() as http:
                    base = url.removesuffix("/mcp")
                    registry = base + "/api/v1/target-registrations"
                    assert (await http.get(registry)).status_code == 401
                    auth = {"X-Target-Registry-Token": os.environ["TMCP_TARGET_REGISTRY_TOKEN"]}
                    assert (await http.get(registry, headers=auth)).json()["configured_servers"] == ["simple-demo-mcp"]
                    target = {"server": name, "owner": "test/alice", "plugin": "demo", "url": endpoint,
                              "execution": dict(mode="remote-job", submitTool="submit_echo", statusTool="echo_status",
                                                resultTool="echo_result", cancelTool="cancel_echo"),
                              "tools": [{"name": "echo_text", "inputSchema": {"type": "object", "required": ["text"],
                                         "properties": {"text": {"type": "string"}}}, "output": {"contract": "json"}}]}
                    registered = await http.put(registry + "/" + name, headers=auth, json=target)
                    assert registered.status_code == 200
                    version = registered.json()["version"]
                    assert (await http.get(url)).status_code == 401
                async with client(url, worker=False) as mcp:
                    assert (await mcp.call_tool("submit_job", {"input": data}, raise_on_error=False)).is_error
                    assert (await mcp.call_tool("lookup_job_submission", {"input": data}, raise_on_error=False)).is_error
                    assert "target_version" in await call(mcp, "describe_target_tool", data)
                async with client(url) as mcp:
                    bad = await call(mcp, "describe_target_tool", {**data, "arguments": {}})
                    assert bad["validation"]["errors"][0]["field"] == "text"
                    data["target_version"] = bad["target_version"]
                    assert (await call(mcp, "submit_job", {**data, "command": ""}))["submission_state"] == "not_sent"
                    assert not calls
                    receipt = await call(mcp, "submit_job", data)
                    assert (await call(mcp, "submit_job", data))["job_id"] == receipt["job_id"]
                    invalid = await call(mcp, "submit_job", {**data, "arguments": {"text": "changed"}})
                    assert invalid["submission_state"] == "not_sent"
                    async with asyncio.timeout(5):
                        while (await call(mcp, "get_job_status", receipt))["status"] != "completed":
                            await asyncio.sleep(0.05)
                    assert (await call(mcp, "collect_job_result", receipt))["target_result"]["text"] == "local echo"
                    base_data = {**data, "target_server": name, "target_version": version}
                    remote_receipt = await call(mcp, "submit_job", {**base_data, "run_id": "remote-echo"})
                    cancel_receipt = await call(mcp, "submit_job", {**base_data, "run_id": "cancel-echo"})
                    lost = await call(mcp, "submit_job", {**base_data, "run_id": "lost-echo",
                                                         "arguments": {"text": "lost echo receipt"}})
                    async with asyncio.timeout(5):
                        while not all(calls.get(r["job_id"]) for r in (remote_receipt, cancel_receipt)):
                            await asyncio.sleep(0.05)
                        # Wait for the broker to persist the original remote ID.
                        while not all(remote[r["job_id"]].get("polled") for r in (remote_receipt, cancel_receipt)):
                            for item in (remote_receipt, cancel_receipt):
                                await call(mcp, "get_job_status", item)
                            await asyncio.sleep(0.05)
                        while (await call(mcp, "get_job_status", lost)).get("submission_state") != "unknown":
                            await asyncio.sleep(0.05)
                lookup_name = "plugin-" + "b" * 32
                lookup_target = {**target, "server": lookup_name, "execution": {
                    key: value for key, value in target["execution"].items() if key != "cancelTool"
                } | {"lookupTool": "lookup_echo"}}
                async with httpx.AsyncClient() as http:
                    registered = await http.put(registry + "/" + lookup_name, headers=auth, json=lookup_target)
                    assert registered.is_success, registered.text
                lookup_data = {**base_data, "run_id": "recover-receipt", "target_server": lookup_name,
                               "target_version": registered.json()["version"], "timeout_seconds": 3 * 86400,
                               "arguments": {"text": "lost echo receipt"}}
                async with client(url) as mcp:
                    blocking = await call(mcp, "submit_job", {
                        **data, "run_id": "interrupted-blocking", "target_tool": "slow_echo",
                        "arguments": {"text": "blocking only once"},
                    })
                    await call(mcp, "submit_job", lookup_data)
                    recovered_id = (await call(mcp, "lookup_job_submission", lookup_data))["job_id"]
                    wrong_scope = await call(mcp, "lookup_job_submission", {**lookup_data, "user_id": "different"})
                    assert wrong_scope == {"submission_state": "unknown"}
                    async with asyncio.timeout(5):
                        while recovered_id not in remote or not calls.get("blocking only once"):
                            await asyncio.sleep(0.05)
                async with httpx.AsyncClient() as http:
                    changed = await http.put(registry + "/" + name, headers=auth, json={**target, "url": new_endpoint})
                    assert changed.is_success and changed.json()["version"] != version
            if launcher_kind == "desktop":
                config.write_text(json.dumps({**json.loads(config.read_text()),
                    "$desktop": {"managed_job_protocol": True}, "job-protocol-mcp": {"url": url}}))
            with start_gateway(config) as url:
                async with client(url) as mcp:
                    assert (await call(mcp, "collect_job_result", receipt))["target_result"]["text"] == "local echo"
                    unknown = await call(mcp, "get_job_status", blocking)
                    assert unknown["job_id"] == blocking["job_id"] and unknown["remote_outcome"] == "unknown"
                    remote[remote_receipt["job_id"]]["offline"] = True
                    assert (await mcp.call_tool("get_job_status", {"input": remote_receipt}, raise_on_error=False)).is_error
                    remote[remote_receipt["job_id"]]["offline"] = False
                    assert (await call(mcp, "get_job_status", remote_receipt))["status"] == "running"
                    recovered = await call(mcp, "lookup_job_submission", lookup_data)
                    assert recovered["job_id"] == recovered_id
                    remote[recovered_id]["status"] = "completed"
                    assert (await call(mcp, "collect_job_result", recovered))["target_result"]["text"] == "lost echo receipt"
                    remote[remote_receipt["job_id"]]["status"] = "completed"
                    remote[remote_receipt["job_id"]]["result_unavailable"] = True
                    assert (await mcp.call_tool("collect_job_result", {"input": remote_receipt}, raise_on_error=False)).is_error
                    remote[remote_receipt["job_id"]]["result_unavailable"] = False
                    assert (await call(mcp, "collect_job_result", remote_receipt))["target_result"]["text"] == "local echo"
                    cancelled = await call(mcp, "terminate_job", cancel_receipt)
                    assert cancellation_receipt(cancelled, cancel_receipt)["cancelled"]
                    stale = await call(mcp, "submit_job", {**base_data, "run_id": "queued-before-change"})
                    assert stale["submission_state"] == "not_sent"
                    unpinned = {key: value for key, value in base_data.items() if key != "target_version"}
                    assert (await call(mcp, "submit_job", {**unpinned, "run_id": "old-unpinned"}))["submission_state"] == "not_sent"
                    assert (await call(mcp, "get_job_status", lost))["submission_state"] == "unknown"
                    wrong = await mcp.call_tool("get_job_status", {"input": {**remote_receipt, "job_token": "wrong"}}, raise_on_error=False)
                    assert wrong.is_error
            assert calls == {"local echo": 1, "blocking only once": 1, remote_receipt["job_id"]: 1,
                             cancel_receipt["job_id"]: 1, lost["job_id"]: 1, recovered_id: 1}
            assert not new_calls

    asyncio.run(check())


def test_recovery_finds_lost_worker_receipt_without_submission(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from tcode_agent.runtime import remote_recovery
    from tcode_agent.runtime.shared_tracker import SharedTracker

    root = str(tmp_path)
    approved = {"run_id": "original", "tenant_id": "tenant", "user_id": "user", "workspace_id": "workspace",
                "workspace": {"job_protocol_backend": "tcode-local"}}
    SharedTracker.upsert_issue({"id": "test", "identifier": "LOCAL-TEST", "state": "In Progress",
        "title": "Recover a receipt", "workspace": {"execution_channel": "bridge", "local_submission": True},
        "description": "submit_job.input: " + json.dumps(approved)}, workdir=root)
    ref = {"job_id": "tcode-job-original", "process_id": "original", "job_token": "test-only", "broker": "tcode-local"}
    lookup = AsyncMock(side_effect=[ConnectionError("offline"), ref])
    poll = AsyncMock(return_value={**ref, "status": "completed", "target_result": {"text": "recovered"}})
    monkeypatch.setattr(remote_recovery, "_lookup_local_submission", lookup)
    monkeypatch.setattr(remote_recovery, "call_job_tool", poll)
    asyncio.run(remote_recovery.reconcile_task("test", root))
    task = SharedTracker.get_task(task_id="test", workdir=root)
    assert task["workspace"]["recovery"]["state"] == "submission_unknown"
    SharedTracker.patch_task(task_id="test", workspace_patch={"recovery": {
        **task["workspace"]["recovery"], "next_retry_at": 0,
    }}, workdir=root)
    asyncio.run(remote_recovery.reconcile_task("test", root))
    task = SharedTracker.get_task(task_id="test", workdir=root)
    assert task["job_ref"]["job_id"] == ref["job_id"]
    assert task["workspace"]["transfer_state"] == "ready"
    assert lookup.await_args.args[0] == {key: value for key, value in approved.items() if key != "workspace"}
    assert [entry.args[1] for entry in poll.await_args_list] == ["get_job_status", "collect_job_result"]


def test_local_submit_keeps_third_party_arguments(tmp_path, monkeypatch):
    from tcode_agent.mcp._symphony import build_job_protocol_submit_input
    from tcode_agent.tools.base import ToolContext

    monkeypatch.setenv("TCODE_INTERNAL_JOB_PROTOCOL_URL", "http://127.0.0.1:10121/mcp")
    monkeypatch.setenv("TCODE_ASYNC_EXECUTION_MODE", "bridge")
    monkeypatch.setenv("SYMPHONY_ROUTED_MCP_TIMEOUT_SECONDS", "259200")
    context = ToolContext(session_id="test", message_id="message", agent="test", abort=None,
                          extra={"workdir": str(tmp_path)})
    data = build_job_protocol_submit_input(server_name="echo-mcp", tool_name="echo_text",
        tool_args={"text": "plain echo"}, context=context, issue_id="echo", issue_identifier="LOCAL-ECHO",
        description="test", input_schema=None)
    assert data["arguments"] == {"text": "plain echo"}
    assert data["timeout_seconds"] == 259200


def test_agent_proxy_does_not_forward_empty_command(monkeypatch):
    from tcode_agent.mcp._proxy import _simplified_submit_args

    monkeypatch.setenv("TCODE_INTERNAL_JOB_PROTOCOL_URL", "http://127.0.0.1:10121/mcp")
    parsed, error = _simplified_submit_args({"input": {"command": "", "target_tool": "echo_text"}})
    assert parsed is None and "command" in error.error


def test_enqueue_pins_approved_plugin_version_without_dynamic_headers(tmp_path, monkeypatch):
    from tcode_agent.mcp._symphony import enqueue_symphony_local_issue
    from tcode_agent.plugins.async_targets import save_binding
    from tcode_agent.runtime.shared_tracker import SharedTracker
    from tcode_agent.tools.base import ToolContext

    workdir = str(tmp_path)
    server = "plugin-" + "a" * 32
    monkeypatch.setenv("TCODE_INTERNAL_JOB_PROTOCOL_URL", "http://127.0.0.1:10121/mcp")
    monkeypatch.setenv("TCODE_ASYNC_EXECUTION_MODE", "bridge")
    monkeypatch.setenv("TCODE_WORKDIR", workdir)
    monkeypatch.setenv("TCODE_CONFIG_ROOT", workdir)
    save_binding(workdir, "test/alice", "mcp-config", "echo",
                 {"active": True, "origin": "mcp-config", "server": server, "version": "approved-v1",
                  "declaration": {"tools": []}})
    context = ToolContext(session_id="echo-session", message_id="message", agent="test", abort=None,
                          extra={"workdir": workdir, "tenant_id": "test", "user_id": "alice", "workspace_id": "echo"})
    accepted = asyncio.run(enqueue_symphony_local_issue(
        server_name=server, tool_name="echo_text", normalized_args={"text": "queued echo"}, context=context,
    ))
    row = SharedTracker.get_task(identifier=accepted["identifier"], workdir=workdir)
    assert row["submit_input"]["target_version"] == "approved-v1"
    assert row["submit_input"]["arguments"] == {"text": "queued echo"}
    assert row["submit_input"]["workspace"]["job_protocol_backend"] == "tcode-local"


def test_bridge_reports_missing_echo_receipt_without_reposting(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from tcode_agent.runtime import bridge_worker as bridge

    monkeypatch.setenv("TCODE_WORKDIR", str(tmp_path))
    monkeypatch.setenv("TCODE_INTERNAL_JOB_PROTOCOL_URL", "http://127.0.0.1:10121/mcp")
    monkeypatch.setattr(bridge, "_local_worker_token", "test-only")
    monkeypatch.setattr(bridge, "_init_mcp", AsyncMock())
    monkeypatch.setattr(bridge.MCPManager, "get_status", AsyncMock(return_value={
        "job-protocol-mcp": SimpleNamespace(status="connected"),
    }))
    ref = {"job_id": "tcode-job-echo", "process_id": "tcode-job-echo"}
    call = AsyncMock(side_effect=[ref, {**ref, "status": "running", "submission_state": "unknown"}])
    monkeypatch.setattr(bridge.MCPManager, "call_tool", call)
    for name in ("_attach_job_ref", "_heartbeat", "_get_task", "_fail", "_complete"):
        monkeypatch.setattr(bridge, name, AsyncMock(return_value={}))
    asyncio.run(bridge._run_task(None, {"task_id": "echo", "claim_token": "test", "submit_input": {
        "target_server": "plugin-" + "a" * 32, "target_tool": "echo_text", "arguments": {"text": "echo"},
    }}))
    assert [entry.args[1] for entry in call.await_args_list] == ["submit_job", "get_job_status"]
    assert bridge._heartbeat.await_args.kwargs["workspace_patch"]["recovery"]["state"] == "submission_unknown"
    bridge._fail.assert_not_awaited()
    bridge._complete.assert_not_awaited()


def test_recovery_never_sends_reference_to_different_broker(monkeypatch):
    from unittest.mock import AsyncMock
    from tcode_agent.mcp.manager_auto import MCPManager
    from tcode_agent.runtime.remote_cancel import call_job_tool

    call = AsyncMock()
    monkeypatch.setattr(MCPManager, "call_tool", call)
    monkeypatch.setenv("TCODE_INTERNAL_JOB_PROTOCOL_URL", "http://127.0.0.1:10121/mcp")
    with pytest.raises(ValueError, match="原任务"):
        asyncio.run(call_job_tool({"job_id": "old-tmcp-job"}, "get_job_status"))
    monkeypatch.delenv("TCODE_INTERNAL_JOB_PROTOCOL_URL")
    with pytest.raises(ValueError, match="原任务"):
        asyncio.run(call_job_tool({"job_id": "tcode-job-demo"}, "terminate_job"))
    call.assert_not_awaited()


def test_tracker_token_without_posix_fchmod(tmp_path, monkeypatch):
    from tcode_agent.runtime.tracker_token import ensure_tracker_token

    monkeypatch.delattr(os, "fchmod", raising=False)
    env = {"TCODE_TRACKER_API_TOKEN_FILE": str(tmp_path / "tracker.token")}
    token = ensure_tracker_token(env)
    assert (tmp_path / "tracker.token").read_text().strip() == token


def test_json_only_example_via_job_protocol(tmp_path, monkeypatch):
    pytest.importorskip("fastmcp")
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    from tcode_agent.plugins.async_targets import declarations, target_payload

    example = Path(__file__).resolve().parents[2] / "examples/third_party_mcp/json-async"
    monkeypatch.delenv("TMCP_TARGET_REGISTRY_TOKEN", raising=False)

    async def check():
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        endpoint = f"http://127.0.0.1:{port}/mcp"
        server = await asyncio.create_subprocess_exec(
            sys.executable, str(example / "server.py"),
            env={**os.environ, "JSON_ASYNC_PORT": str(port), "JSON_ASYNC_DB": str(tmp_path / "remote.sqlite")},
        )
        try:
            async with asyncio.timeout(8):
                while True:
                    try:
                        async with Client(endpoint, init_timeout=1) as client:
                            assert len(await client.list_tools()) == 3
                        break
                    except RuntimeError:
                        if server.returncode is not None:
                            raise
                        await asyncio.sleep(0.1)
            target = declarations(example / "plugin", ["async-targets.json"])[0]
            payload = target_payload(tmp_path, "test/alice", "json-async-sum", target, {"compute_url": endpoint})
            with managed_job_protocol(tmp_path) as url:
                async with httpx.AsyncClient() as http:
                    registered = await http.put(
                        url.removesuffix("/mcp") + "/api/v1/target-registrations/" + payload["server"],
                        headers={"X-Target-Registry-Token": os.environ["TMCP_TARGET_REGISTRY_TOKEN"]}, json=payload,
                    )
                    assert registered.is_success, registered.text
                headers = {"Authorization": "Bearer " + os.environ[LOCAL_TOKEN],
                           WORKER_HEADER: bridge_worker_environment()[WORKER_TOKEN]}
                async with Client(StreamableHttpTransport(url, headers=headers)) as client:
                    submitted = await client.call_tool("submit_job", {"input": {
                        "tenant_id": "test", "user_id": "alice", "workspace_id": "work", "run_id": "sum-demo",
                        "target_server": payload["server"], "target_tool": "sum_numbers",
                        "target_version": registered.json()["version"], "arguments": {"numbers": [1, 2, 3]},
                    }})
                    ref = {"input": submitted.structured_content}
                    async with asyncio.timeout(8):
                        while (await client.call_tool("get_job_status", ref)).structured_content["status"] != "completed":
                            await asyncio.sleep(0.1)
                    result = (await client.call_tool("collect_job_result", ref)).structured_content
                    assert result["target_result"] == {"sum": 6}
                    assert result["output_bundle"] is None
        finally:
            if server.returncode is None:
                server.terminate()
                await asyncio.wait_for(server.wait(), 8)

    asyncio.run(check())


def test_single_web_launcher_runs_echo_through_tracker(tmp_path):
    pytest.importorskip("fastmcp")

    async def check():
        async with echo_server() as (endpoint, calls, remote):
            config = tmp_path / "mcp.json"
            config.write_text(json.dumps({"simple-demo-mcp": {"url": endpoint, "invocation_mode": "async"}}))
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            root = Path(__file__).resolve().parents[2]
            env = {key: value for key, value in os.environ.items() if not key.startswith(("TCODE_", "TMCP_", "SYMPHONY_"))}
            env.update(TCODE_CONFIG_ROOT=str(tmp_path), TCODE_WORKDIR=str(tmp_path),
                       TCODE_MCP_CONFIG=str(config), TCODE_ENV_FILE=os.devnull, TCODE_AUTH_REQUIRED="0",
                       TCODE_ENTERPRISE_MODE="0", TCODE_SANDBOX_ENABLED="false",
                       TCODE_RESEARCH_ENABLED="false", TCODE_TRACKER_API_TOKEN="disposable-test-token",
                       TCODE_ASYNC_EXECUTION_MODE="bridge", TCODE_BRIDGE_POLL_SECONDS="1",
                       TCODE_CLI_SHOW_TOOL_INIT_LOGS="0")
            log = tmp_path / "web.log"
            with log.open("wb") as output:
                async def start_web():
                    return await asyncio.create_subprocess_exec(sys.executable, "-m", "tcode_agent.server.launcher",
                        "--port", str(port), "--workdir", str(tmp_path), cwd=root, env=env, stdout=output, stderr=output)

                async def stop_web(process):
                    if process.returncode is None:
                        process.terminate()
                        try:
                            await asyncio.wait_for(process.wait(), 10)
                        except asyncio.TimeoutError:
                            process.kill()
                            await process.wait()

                web = await start_web()
                try:
                    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                                                 headers={"X-Tracker-Token": "disposable-test-token"}) as http:
                        async with asyncio.timeout(20):
                            while True:
                                try:
                                    if (await http.get("/api/health")).is_success:
                                        break
                                except httpx.ConnectError:
                                    pass
                                if web.returncode is not None:
                                    raise AssertionError("Web stopped before startup")
                                await asyncio.sleep(0.1)
                        assert (tmp_path / ".tcode/job-protocol/web-bridge.pid").read_text() == str(web.pid)
                        payload = {"run_id": "echo-via-web", "target_server": "simple-demo-mcp", "target_tool": "echo_text",
                                   "arguments": {"text": "echo through Web Bridge"},
                                   "workspace": {"job_protocol_backend": "tcode-local"}}
                        from tcode_agent.server.job_protocol import _digest
                        payload["target_version"] = _digest(json.loads(config.read_text())["simple-demo-mcp"])[:24]
                        assert (await http.post("/api/tracker/tasks", headers={"X-Tracker-Token": ""},
                                                json={"task": {"id": "unauthorized"}})).status_code == 401
                        assert (await http.post("/api/tracker/claim-next", headers={"X-Tracker-Token": ""},
                                                json={"worker_id": "unauthorized", "channel": "bridge"})).status_code == 401
                        old = await http.post("/api/tracker/tasks", json={"task": {
                            "id": "old-echo", "identifier": "LOCAL-OLD", "kind": "symphony_local_issue",
                            "state": "Todo", "execution_channel": "bridge",
                            "description": "submit_job.input: " + json.dumps({**payload, "workspace": {}})}})
                        assert old.is_success
                        created = await http.post("/api/tracker/tasks", json={"task": {
                            "id": "echo-via-web", "identifier": "LOCAL-ECHOTEST", "title": "Disposable echo",
                            "kind": "symphony_local_issue", "state": "Todo", "execution_channel": "bridge",
                            "description": "submit_job.input: " + json.dumps(payload)}})
                        assert created.is_success, created.text
                        async with asyncio.timeout(15):
                            while True:
                                snapshot = (await http.get("/api/tracker/tasks/echo-via-web")).json()
                                task = snapshot["task"]
                                if task["status"] in {"completed", "failed"}:
                                    break
                                await asyncio.sleep(0.2)
                        assert task["status"] == "completed", task
                        assert task["result_summary"].find("echo through Web Bridge") >= 0
                        assert calls == {"echo through Web Bridge": 1}
                        assert (await http.get("/api/tracker/tasks/old-echo")).json()["task"]["status"] == "queued"
                        # The live Web worker dispatches a real remote job, then
                        # a new Web process must recover it from the original store.
                        from tcode_agent.server.job_protocol import JobProtocol
                        name = "plugin-" + "e" * 32
                        target = {"server": name, "owner": "test/alice", "plugin": "demo", "url": endpoint,
                                  "execution": {"mode": "remote-job", "submitTool": "submit_echo",
                                                "statusTool": "echo_status", "resultTool": "echo_result"},
                                  "tools": [{"name": "echo_text", "inputSchema": {"type": "object"},
                                             "output": {"contract": "json"}}]}
                        target["version"] = _digest(target)[:24]
                        store = JobProtocol(tmp_path / ".tcode/job-protocol")
                        with store.db() as db:
                            db.execute("INSERT INTO targets VALUES (?, ?, 1, ?)", (name, target["version"], json.dumps(target)))
                        remote_payload = {**payload, "run_id": "restart-web", "target_server": name,
                                          "target_version": target["version"], "tenant_id": "test", "user_id": "alice",
                                          "arguments": {"text": "recovered via Web"}}
                        queued = await http.post("/api/tracker/tasks", json={"task": {
                            "id": "restart-web", "identifier": "LOCAL-RESTARTWEB", "title": "Remote echo",
                            "kind": "symphony_local_issue", "state": "Todo", "execution_channel": "bridge",
                            "description": "submit_job.input: " + json.dumps(remote_payload)}})
                        assert queued.is_success, queued.text
                        async with asyncio.timeout(15):
                            while True:
                                task = (await http.get("/api/tracker/tasks/restart-web")).json()["task"]
                                job_id = (task.get("job_ref") or {}).get("job_id")
                                if job_id and (remote.get(job_id) or {}).get("polled"):
                                    break
                                assert task["status"] != "failed", task
                                await asyncio.sleep(0.2)
                        remote[job_id]["offline"] = True
                        async with asyncio.timeout(10):
                            while not remote[job_id].get("outage_observed"):
                                await asyncio.sleep(0.05)
                        await stop_web(web)
                        web = await start_web()
                        remote[job_id].update(offline=False, status="completed")
                        async with asyncio.timeout(45):
                            while True:
                                try:
                                    response = await http.get("/api/tracker/tasks/restart-web")
                                    task = response.json()["task"] if response.is_success else {}
                                except httpx.TransportError:
                                    task = {}
                                if (task.get("workspace") or {}).get("transfer_state") == "ready":
                                    break
                                await asyncio.sleep(0.2)
                        assert task["job_ref"]["job_id"] == job_id
                        assert "recovered via Web" in task["result_summary"]
                        assert calls == {"echo through Web Bridge": 1, job_id: 1}
                except Exception as exc:
                    diagnostics = [line for line in log.read_text().splitlines() if "GET /api/tracker/tasks/" not in line]
                    raise AssertionError("\n".join(diagnostics)[-6000:]) from exc
                finally:
                    await stop_web(web)
            assert not (tmp_path / ".tcode/job-protocol/web-bridge.pid").exists()

    asyncio.run(check())
