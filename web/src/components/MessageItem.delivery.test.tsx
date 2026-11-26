import { renderToString } from 'solid-js/web';
import { beforeEach, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({messages: [] as any[], counts: {} as Record<string, number>, running: false, turnRunning: false}));
beforeEach(() => {state.messages = []; state.counts = {}; state.running = false; state.turnRunning = false;});

vi.mock('../stores/session', () => ({ sessionStore: {
  currentSessionId: () => '', messages: () => state.messages, isRunning: () => state.running,
  currentSession: () => ({task_runtime:{active_counts:state.counts}}),
  getSessionRuntimeState: () => ({deliveryTurnRunning:state.turnRunning}),
  messageToolsCollapsePreference: () => undefined,
} }));
vi.mock('../api/client', () => ({hasActiveChatSocket: () => false}));
vi.mock('./MarkdownContent', () => ({default: (props: any) => <p>{props.content}</p>}));
import MessageItem from './MessageItem';

it('keeps the completion summary, shows short tool text literally and folds longer returns', () => {
  const card = (text?: string, summary = '处理成功') => renderToString(() => <MessageItem message={{
    id:'msg-async-continuation-test', role:'assistant', presentation:'async_completion',
    content:'[Symphony:test]\ntranslation 异步任务已完成。\n任务编号: test\n任务状态: Done\n结果摘要: ' + summary,
    metadata:{tool_result_text:text},
  }}/>);
  expect(card()).not.toContain('工具返回文本');
  const short = card('<b>译文</b>');
  expect(short).toContain('处理成功');
  expect(short).toContain('&lt;b>译文&lt;/b>');
  expect(short).not.toContain('展开文本');
  expect(card('译文', '{"content":[')).not.toContain('请打开原会话');
  for (const text of ['长'.repeat(300)+'末尾', '一\n二\n三\n四\n末尾']) {
    const html = card(text);
    expect(html).toMatch(/<details[^>]*><summary[^>]*>[\s\S]*?展开文本/);
    expect(html).toContain(text);
  }
});

const verification = {advisory:true, verification_status:'PARTIAL' as const, confidence:.23,
  criteria_total:2, criteria_passed:0, criteria_failed:1, criteria_unverified:1,
  findings:[{status:'failed' as const,title:'报告完整性',reason:'报告缺少计算依据'}]};

it('opens a final result body for an interactive result even without assistant text', () => {
  const html = renderToString(() => <MessageItem message={{id:'directory',role:'assistant',content:''}}
    renderResultContent={() => <button type="button">下载到工作区</button>}/>);
  expect(html).toMatch(/data-message-result-body[\s\S]*?最终结果[\s\S]*?下载到工作区/);
});

it('defaults to folded explanations, with optional acceptance and modification controls', () => {
  const html = renderToString(() => <MessageItem latestAssistantMessageId="result"
    message={{id:'result',role:'assistant',content:'已有结果',verification}}/>);
  expect(html).toContain('aria-expanded="false"');
  expect(html).toContain('接受当前结果并完成');
  expect(html).toContain('要求修改');
  expect(html).toContain('23%');
  expect(html).not.toContain('报告缺少计算依据');
  expect(html).not.toContain('重试上限');
  expect(html).not.toContain('证明等级');
});

it('keeps the delivered answer actionable after progress notices and during background calculation', () => {
  const message = {id:'result', role:'assistant' as const,content:'已交付',verification};
  state.messages = [message, {id:'notice',role:'assistant',presentation:'async_completion',content:'任务进度'}];
  state.counts = {waiting_external:1, running:1};
  state.running = true;
  const html = renderToString(() => <MessageItem latestAssistantMessageId="notice" message={message}/>);
  expect(html).toContain('接受当前结果并完成');
  expect(html.match(/<button[^>]*>接受当前结果并完成/)?.[0]).not.toMatch(/\sdisabled(?:[=\s>])/);
  expect(html).not.toContain('还有执行中的任务');
});

it('still disables acceptance during explicit human review or a generating turn', () => {
  for (const gate of ['review','turn']) {
    state.counts = gate === 'review' ? {waiting_review:1} : {};
    state.turnRunning = gate === 'turn';
    const html = renderToString(() => <MessageItem latestAssistantMessageId="result"
      message={{id:'result',role:'assistant',content:'已有结果',verification}}/>);
    expect(html.match(/<button[^>]*>接受当前结果并完成/)?.[0]).toMatch(/\sdisabled(?:[=\s>])/);
  }
});

it('does not offer an old answer after a new user request', () => {
  const message = {id:'result',role:'assistant' as const,content:'已有结果',verification};
  state.messages = [message,{id:'next',role:'user',content:'新的要求'}];
  const html = renderToString(() => <MessageItem latestAssistantMessageId="result" message={message}/>);
  expect(html).not.toContain('接受当前结果并完成');
});

it('does not describe nonexistent remaining checks when all seven named checks passed', () => {
  const html = renderToString(() => <MessageItem latestAssistantMessageId="result"
    message={{id:'result',role:'assistant',content:'已有结果',verification:{...verification,confidence:.86,
      criteria_total:7,criteria_passed:7,criteria_failed:0,criteria_unverified:0}}}/>);
  expect(html).toContain('86%');
  expect(html).toContain('7项检查通过，仍有内容需要核对');
  expect(html).toContain('接受当前结果并完成');
  expect(html).not.toContain('其余内容尚未核实');
});
it('removes both controls after acceptance without turning the result into verified', () => {
  const html = renderToString(() => <MessageItem latestAssistantMessageId="result"
    message={{id:'result',role:'assistant',content:'已有结果',verification:{...verification,delivery_state:'accepted'}}}/>);
  expect(html).toContain('已接受当前结果并完成');
  expect(html).toContain('部分验证');
  expect(html).toContain('23%');
  expect(html).not.toContain('要求修改');
  expect(html).not.toMatch(/<button[^>]*>接受当前结果并完成/);
});
