import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tcode_agent.harness.engine import AgentHarnessEngine
from tcode_agent.runtime.evidence_ledger import EvidenceLedger
from tcode_agent.runtime.goal_evidence import resolve_goal_evidence, split_goal_evidence
from tcode_agent.types import Message, ToolCall, ToolResult, ToolState


def record(ref, content, *, view='evidence', state=ToolState.COMPLETED):
    return Message(role='tool', content=content, tool_calls=[
        ToolCall(tool_name='webfetch', arguments={'view': view}, call_id=ref, state=state),
    ])


def test_progress_uses_new_results_artifacts_and_verdicts_not_call_ids_or_narration():
    from tcode_agent.server.goal_mode import _round_progress
    goal = {}
    session = SimpleNamespace(id='s', metadata={})

    def step(messages=(), verdict=None, state='ready', preparation=False):
        seen, count = _round_progress(goal, messages, verdict, session, state=state, preparation=preparation)
        goal.update(progress_observations=seen, no_progress_rounds=count)
        return count

    def observed(tool, output, ref, args=None, **meta):
        return Message(role='tool', content=output, tool_calls=[ToolCall(tool_name=tool, arguments=args or {},
            call_id=ref, state=ToolState.COMPLETED, metadata=meta)])

    first = observed('read', 'row A', '1')
    assert step([first]) == 0
    assert step([observed('read', 'row A', '2'), observed('goal_checkpoint', '换了一种说法', '3')]) == 1
    assert step([observed('read', 'row B', '4')]) == 0
    assert step([first]) == 1  # alternating two old results does not work
    assert step([observed('export', '', '5', artifacts=[{'path':'review.csv','sha256':'v1'}])]) == 0
    assert step([observed('export', '', '6', artifacts=[{'path':'review.csv','sha256':'v1','observed_at':99}])]) == 1
    assert step([observed('export', '', '7', artifacts=[{'path':'review.csv','sha256':'v2'}])]) == 0
    assert step([observed('write', 'ok', '8', {'filePath':'notes','content':'first'})]) == 0
    assert step([observed('write', 'ok', '9', {'filePath':'notes','content':'second'})]) == 0
    assert step([observed('write', 'ok', '10', {'filePath':'notes','content':'second'})]) == 1
    status = {'verdict':'partial','criterion_results':[{'criterion_id':'A','status':'uncertain'}]}
    assert step(verdict=status) == 0
    assert step(verdict={**status, 'warnings':['措辞变化'], 'confidence':.9}) == 1
    assert step(verdict={**status, 'criterion_results':[{'criterion_id':'A','status':'pass'}]}) == 0
    assert step([observed('exec', 'bad', '11', exitCode=1), observed('read', 'skipped', '12', synthetic=True),
                 observed('question','pending','13'), observed('submit','queued','14',accepted_async=True)]) == 1
    assert step(state='waiting_external') == step(preparation=True) == 1
    assert step([observed('goal_checkpoint','下一轮继续','15')]) == 2
    assert step([observed('inspect','first receipt','16',result_fingerprint='same-view')]) == 0
    assert step([observed('inspect','different receipt','17',result_fingerprint='same-view'),
                 observed('mcp_lookup','remote error','18',raw_mcp_result={'isError':True})]) == 1
    assert 'row A' not in str(goal) and 'review.csv' not in str(goal)


def test_only_new_owned_successful_async_receipt_counts_as_progress():
    from tcode_agent.server.goal_mode import _round_progress
    event = dict(session_id='s', origin_request_id='old', identifier='job', status='completed',
                 result_receipt={'status':'completed','validated':True,'rejected_artifact_count':0})
    session = SimpleNamespace(id='s',metadata={'async_completion_events':[event]})
    goal = {'run_ids':['current'], 'progress_observations':[], 'no_progress_rounds':1}
    def snapshot():
        return _round_progress(goal, [], None, session, state='ready', preparation=False)
    assert snapshot() == ([], 2)
    event['origin_request_id'] = 'current'
    event['result_receipt']['target_is_error'] = True
    assert snapshot() == ([], 2)
    event['result_receipt']['target_is_error'] = False
    seen, count = snapshot()
    assert len(seen) == 1 and count == 0
    goal.update(progress_observations=seen, no_progress_rounds=count)
    assert snapshot() == (seen, 1)


def test_bound_excerpt_is_resolved_from_the_exact_record_and_prioritized():
    claim = 'Release A ran the mixed workload in 12 seconds'
    quote = 'On the mixed workload, Release A took 12 seconds.'
    source = record('original', 'Intro\n\n[Results / Test 2]\n' + quote)
    unrelated = record('other', 'Release B took 5 seconds on the read-only workload.')
    row = {'claim': claim, 'call_id': 'original', 'quote': quote}
    reply, rows, malformed = split_goal_evidence(claim + '\n\n<goal_evidence>\n' + json.dumps([row]) + '\n</goal_evidence>')
    assert reply == claim and not malformed and 'original' not in reply
    resolved, invalid = resolve_goal_evidence(reply, rows, [source, unrelated])
    assert not invalid and resolved == [dict(row, section='Results / Test 2', source_tool='webfetch')]

    ledger = EvidenceLedger.from_messages([source, unrelated], include_web_evidence=True)
    ledger.goal_claim_bindings = resolved
    selected = ledger.slice_for_contract({'goal': 'release benchmark'}, claim, max_entries=1)
    assert [item['call_id'] for item in selected['entries']] == ['original']
    assert selected['goal_claim_bindings'] == resolved

    for bad in [[], [unrelated], [source, source], [record('original', quote, view='sections')],
                [record('original', quote, state=ToolState.ERROR)]]:
        assert resolve_goal_evidence(reply, rows, bad) == ([], True)
    assert resolve_goal_evidence('different answer', rows, [source]) == ([], True)


def test_private_suffix_cannot_leak_when_missing_end_marker_or_malformed():
    assert split_goal_evidence('A general explanation.') == ('A general explanation.', [], False)
    assert split_goal_evidence('Answer\n<goal_evidence>\nprivate') == ('Answer', [], True)
    assert split_goal_evidence('Answer\n<goal_evidence>{}</goal_evidence>') == ('Answer', [], True)


def test_goal_reply_is_clean_before_persistence_or_emission():
    agent = SimpleNamespace(messages=[])
    engine = AgentHarnessEngine(agent)
    engine._persist_turn_state = AsyncMock()
    engine._emit_model_output_event = AsyncMock()
    quote = 'The same version ran the specified test.'
    content = 'Answer\n<goal_evidence>' + json.dumps([{
        'claim': 'Answer', 'call_id': 'private-reference', 'quote': quote,
    }]) + '</goal_evidence>'
    reply = asyncio.run(engine._record_assistant_response(
        response_content=content, tool_calls=[], model_response=None, stream_message_id='reply',
        streamed=True, turn_id='turn', user_message_id='request', iteration=1, goal_chat=True,
    ))
    assert reply.content == agent.messages[0].content == 'Answer'
    assert engine._emit_model_output_event.call_args.kwargs['content'] == 'Answer'
    assert reply.metadata['goal_evidence'][0]['call_id'] == 'private-reference'
    ordinary = asyncio.run(engine._record_assistant_response(
        response_content=content, tool_calls=[], model_response=None, stream_message_id='ordinary',
        streamed=False, turn_id='ordinary', user_message_id='request', iteration=1,
    ))
    assert ordinary.content == content and 'goal_evidence' not in ordinary.metadata


def test_only_successful_complete_checkpoint_can_publish_goal_chat_prose():
    engine = AgentHarnessEngine(SimpleNamespace(_generate_id=lambda: 'final'))
    report = Message(role='assistant', content='A full report with multiple entries', presentation='commentary')
    report._goal_checkpoint_prose = True
    checkpoint = ToolCall(tool_name='goal_checkpoint', arguments={}, call_id='checkpoint')
    for state in ('wait', 'continue', 'blocked'):
        result = ToolResult(title='Progress', output='Summary', metadata={'goal_checkpoint': {'state': state}})
        assert engine._goal_checkpoint_chat_delivery(report, checkpoint, result, user_message_id='user') is None
    complete = ToolResult(title='Progress', output='Summary', metadata={'goal_checkpoint': {'state': 'complete'}})
    report.metadata['output_budget_partial'] = True
    assert engine._goal_checkpoint_chat_delivery(report, checkpoint, complete, user_message_id='user') is None
    report.metadata.pop('output_budget_partial')
    final = engine._goal_checkpoint_chat_delivery(report, checkpoint, complete, user_message_id='user')
    assert final.content == 'A full report with multiple entries'
    assert final.id == 'final' and final.parent_id == 'user' and final._goal_chat_candidate
    assert not final.tool_calls and not report.content
