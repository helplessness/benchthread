"""Exercise the actual goal supervisor, draft storage, harness and verifier.

Only model answers and transport setup are scripted; no online model or MCP required.
"""
import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tcode_agent.agent.agent import Agent
from tcode_agent.providers.base import ChatResponse
from tcode_agent.server import goal_mode, native_execution
from tcode_agent.session.manager import SessionManager
from tcode_agent.types import AgentInfo, Message, ToolCall, ToolState


@pytest.mark.parametrize('case', [
    'fixed', 'second_rejection', 'attempted_tool', 'pseudo_tool', 'missing_binding', 'foreign_source',
    'missing_data', 'blocked', 'unavailable', 'last_round', 'light', 'pause', 'stop', 'interrupted',
    'deadline', 'disabled', 'guidance', 'strict', 'paraphrased_binding', 'fabricated_quote',
])
def test_bounded_chat_revision(monkeypatch, tmp_path, case):
    monkeypatch.chdir(tmp_path)
    for key in ('WORKDIR', 'CONFIG_ROOT'):
        monkeypatch.setenv('TCODE_' + key, str(tmp_path))
    monkeypatch.setattr(SessionManager, '_sessions', {})
    monkeypatch.setattr(SessionManager, '_lock', None)
    monkeypatch.setattr('tcode_agent.server.auth.session_result_verification_settings', AsyncMock(
        return_value={'enabled': True, 'mode': case if case in {'light','strict'} else 'standard'}))
    quote = 'Batch A finished the mixed workload in exactly twelve seconds.'
    hint = {'call_id': 'other' if case == 'foreign_source' else 'owned', 'quote': quote}
    reviews, turns = [], []

    async def judge(*, messages, tools, **_):
        assert tools is None
        reviews.append(messages[-1].content)
        if case == 'unavailable':
            return ChatResponse(content='not a verifier JSON response')
        verdict = ('blocked' if case == 'blocked' else 'repair') if len(reviews) == 1 or case == 'second_rejection' else 'pass'
        if case in ('paraphrased_binding', 'fabricated_quote'):
            verdict = 'pass'  # A semantic pass cannot override the host's private citation check.
        assessments = {}
        if case == 'strict':
            start = reviews[-1].index('{"contract":')
            payload, _end = json.JSONDecoder().raw_decode(reviews[-1][start:])
            assessments = {c['criterion_id']: 'pass' if verdict=='pass' else 'fail'
                           for c in payload['contract']['success_criteria']}
        return ChatResponse(content=json.dumps(dict(verdict=verdict, confidence=0.92, criterion_assessments=assessments,
            missing_deliverables=['new measurement'] if case == 'missing_data' else [],
            repair_instructions=['Include the observed source quote.'] if verdict == 'repair' else [],
            goal_repair_sources=[hint])))

    async def run():
        goal = dict(id='g', objective='Explain the observed timing in chat.', criteria='Quote the original result.',
            status='ready', updated_at=time.time(), deadline=time.time()+90, rounds_used=0,
            max_rounds=1 if case=='last_round' else 4, steps_per_round=3)
        session = await SessionManager.create(workdir=str(tmp_path), agent_name='build', metadata={'goal_mode':goal})
        service = goal_mode.GoalModeService()
        agent = Agent(AgentInfo(name='build', steps=3), SimpleNamespace(name='fixture', chat=judge), session.id,str(tmp_path))
        monkeypatch.setattr(native_execution,'open_native_agent',AsyncMock(return_value=(agent,None)))
        engine = agent._engine
        look_up_tools = AsyncMock(side_effect=AssertionError('tool discovery during revision'))
        monkeypatch.setattr(engine, '_safe_get_tool_definitions', look_up_tools)
        async def model(tools, **_):
            assert tools == []
            if case == 'attempted_tool':
                return ('',[{'id':'x','function':{'name':'write','arguments':{'filePath':'unwanted','content':'x'}}}],
                        ChatResponse(content='')),[]
            if case == 'pseudo_tool':
                return ('<|DSML|calls>write</|DSML|calls>',None,ChatResponse(content='')),[]
            text = quote
            if case != 'missing_binding':
                text += '\n<goal_evidence>'+json.dumps([dict(hint, claim=quote)])+'</goal_evidence>'
            return (text,None,ChatResponse(content=text)),[]
        monkeypatch.setattr(engine,'_call_llm_with_guidance_race',model)
        async def turn(*, run_input, discover_workflows, **_):
            turns.append(agent._goal_text_repair)
            if turns[-1]:
                assert not discover_workflows
                assert quote in run_input.latest_user_text()
                await engine.run(run_input.latest_user_text())
                return
            draft = Message(id='draft',role='assistant',content='Batch A finished the test.')
            if case in ('paraphrased_binding','fabricated_quote'):
                draft.metadata['goal_evidence'] = [dict(hint,claim='A paraphrase absent from the draft',
                    quote=quote if case=='paraphrased_binding' else 'No source returned this invented timing data.')]
            draft._goal_chat_candidate = True
            def source(ref):
                return Message(id=ref,role='tool',content=quote,tool_calls=[ToolCall(tool_name='read',
                    arguments={'filePath':'timing.txt'},call_id=ref,state=ToolState.COMPLETED)])
            agent.messages.extend([Message(id='prev',role='user',content='different goal'),source('other'),
                Message(id='anchor',role='user',content=run_input.latest_user_text(),metadata=agent._next_user_message_metadata),
                source('owned'),draft])
            await SessionManager.mutate(session.id, lambda s: setattr(s,'messages',list(agent.messages)))
        monkeypatch.setattr(native_execution,'run_native_turn',turn)
        async def current():
            return goal_mode.public_goal(await SessionManager.get(session.id))
        await service._round(session,goal)
        first = await current()
        if case in ('foreign_source','fabricated_quote','missing_data','blocked','unavailable','last_round','light'):
            assert first['status']=='needs_input' and not first.get('pending_text_repair'), first
            assert turns==[False] and len(reviews)==1
            return
        assert first['status']=='ready' and first['pending_text_repair']['sources']==[hint], first
        from tcode_agent.server.goal_api import goal_response
        assert 'pending_text_repair' not in goal_response(session, first)['goal']
        if case=='guidance':
            await SessionManager.mutate(session.id, lambda s: goal_mode.record_goal_guidance(s,s.metadata['goal_mode'],'New constraint'))
            assert not (await current()).get('pending_text_repair')
            return
        if case in ('deadline','disabled'):
            if case=='deadline':
                await goal_mode.change_goal(session.id,goal_mode.owner(session),'g',deadline=time.time()-1)
            else:
                monkeypatch.setattr('tcode_agent.server.auth.session_result_verification_settings', AsyncMock(
                    return_value={'enabled':False,'mode':'standard'}))
            await service._round(session,first)
            assert (await current())['status']==('budget_exhausted' if case=='deadline' else 'needs_input')
            assert turns==[False]
            return
        if case in ('pause','stop'):
            state = 'paused' if case == 'pause' else 'stopped'
            await goal_mode.change_goal(session.id,goal_mode.owner(session),'g',status=state)
            await service._round(session,first)
            assert (await current())['status']==state and turns==[False]
            return
        if case=='interrupted':
            await goal_mode.change_goal(session.id,goal_mode.owner(session),'g', status='recovering',
                text_repair_run_id='repair-run', pending_text_repair=None,run_ids=['repair-run'])
            await service.tick((session.id,'g'))
            assert (await current())['status']=='needs_input' and turns==[False]
            return
        await service._round(session,first)
        last = await current()
        assert turns==[False,True] and last['rounds_used']==2
        assert last['text_repair_run_id'] and not last.get('pending_text_repair')
        assert last['status']==('completed' if case in {'fixed','strict','paraphrased_binding'} else 'needs_input'), last
        assert len(reviews)==(1 if case in ('attempted_tool','pseudo_tool') else 2)
        hidden = next(m for m in agent.messages if m.role=='user' and m.metadata.get('goal_round_id')==last['text_repair_run_id'])
        assert hidden.presentation=='async_hidden_prompt'
        assert not (tmp_path/'unwanted').exists() and not agent._goal_text_repair
        look_up_tools.assert_not_called()
        if case=='fixed':
            final = next(m for m in (await SessionManager.get(session.id)).messages if m.id==last['validation_message_id'])
            assert final.content==quote and final.id!='draft'
    asyncio.run(run())
