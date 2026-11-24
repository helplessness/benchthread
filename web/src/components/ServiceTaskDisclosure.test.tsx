import { renderToString } from 'solid-js/web';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ScheduledTasksWorkspace from './ScheduledTasksWorkspace';
import SettingsWorkspace from './SettingsWorkspace';
import PluginAsyncTargetsSettings from './PluginAsyncTargetsSettings';
import JobWorkspacePanel, { selectJobTimeline } from './JobWorkspacePanel';
import { issueStateLabel } from './SymphonyWorkspace';

afterEach(() => vi.unstubAllGlobals());

describe('list-first services and tasks', () => {
  it('keeps the scheduled task list visible without the creation form', () => {
    const html = renderToString(() => <ScheduledTasksWorkspace currentUser={{ role: 'admin' } as any} />);
    expect(html).toContain('新建任务');
    expect(html).toContain('aria-label="定时任务列表"');
    expect(html).not.toContain('<form');
    expect(html).not.toContain('生成确认预览');
    expect(html).not.toContain('390px_minmax');
  });
  it('opens regular MCP configuration on demand and keeps diagnostics collapsed', () => {
    vi.stubGlobal('localStorage', { getItem: () => null });
    const html = renderToString(() => <SettingsWorkspace initialSection="mcp" />);
    expect(html).toContain('MCP 服务');
    expect(html).not.toContain('placeholder="例如 literature-search"');
    expect(html).toContain('<summary>高级：连接诊断与代理路由</summary>');
    expect(html).toContain('<summary>高级：配置文件与重新加载</summary>');
  });
  it('keeps asynchronous deployment instructions in a closed disclosure', () => {
    const html = renderToString(() => <PluginAsyncTargetsSettings workspaceId="test" />);
    expect(html).toContain('<details><summary>高级：接入协议与部署说明</summary>');
    expect(html).not.toContain('type="url"');
  });
  it('uses the Web workspace section instead of the local initial section', () => {
    vi.stubGlobal('localStorage', { getItem: () => null });
    const html = renderToString(() => <SettingsWorkspace initialSection="mcp" section="models" />);
    expect(html).toContain('<h2>模型与执行</h2>');
    expect(html).not.toContain('添加常规 MCP');
  });
  it('places task result controls ahead of timestamps and technical policies', () => {
    const html = renderToString(() => <JobWorkspacePanel workspace={{ workdir: '/workspace/test', tool: 'test', status: 'completed' } as any} />);
    expect(html).toContain('查看结果文件');
    expect(html.indexOf('查看结果文件')).toBeLessThan(html.indexOf('提交时间'));
  });
  it('shows the recovery hint only while awaiting retry', () => {
    for (const workspace of [{ recovery: { state: 'retry_wait', error: '访问被拒绝' } }, { transfer_state: 'retry_wait' }]) {
      expect(issueStateLabel('running', { workspace } as any)).toBe('状态待核对');
    }
    for (const state of ['retry_wait', 'observed']) {
      const html = renderToString(() => <JobWorkspacePanel workspace={{
        workdir: '/workspace/test', recovery: { state, error: '请勿重新提交' },
      }} />);
      expect(html.includes('请勿重新提交')).toBe(state === 'retry_wait');
    }
  });
  it('renders built-in task timeline from the tracker before runtime events exist', () => {
    const html = renderToString(() => <JobWorkspacePanel workspace={{
      workdir: '/workspace/test', job_ref: { job_id: 'tcode-job-fixture' }, lifecycle_status: 'running',
      timeline: [{ title: '任务已提交' }],
    }} />);
    expect(html).toContain('运行中');
    expect(html).toContain('任务时间线');
    expect(html).toContain('任务已提交');
  });
  it('adds tracker completion without dropping built-in runtime events or changing legacy priority', () => {
    const started = { kind: 'started', at: '2026-01-01T00:00:00Z' };
    const terminal = { kind: 'completed', at: '2026-01-01T00:00:01Z' };
    const collecting = { kind: 'collecting', at: '2026-01-01T00:00:02Z' };
    const workspace = { timeline: [{ kind: 'submitted' }, terminal] };
    const runtime = [started, collecting];
    expect(selectJobTimeline(workspace, 'tcode-job-fixture', runtime, [])).toEqual([started, terminal, collecting]);
    expect(selectJobTimeline(workspace, 'external', runtime, [])).toBe(runtime);
    expect(selectJobTimeline({ timeline: [{ kind: 'failed' }] }, 'tcode-job-fixture', runtime, [])).toEqual([...runtime, { kind: 'failed' }]);
    const completed = [{ kind: 'completed' }];
    expect(selectJobTimeline(workspace, 'tcode-job-fixture', completed, [])).toBe(completed);
  });
});
