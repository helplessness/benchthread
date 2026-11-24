import { renderToString } from 'solid-js/web';
import { describe, expect, it } from 'vitest';
import DesktopStudioShell from '../desktop-app/DesktopStudioShell';
import WebStudioShell from '../web/WebStudioShell';
import type { StudioShellProps } from './studioControls';

const noop = () => {};
const props: StudioShellProps = {
  identity: 'user:tenant', page: 'console', workspace: 'project', sessionTitle: '会话', status: '',
  onNavigate: noop, onNewSession: noop, onCommandPalette: noop, onOpenSkillCenter: noop,
  onOpenServiceSettings: noop, skillCount: 0, subagentCount: 0, sidebarRequest: 0,
  notices: [], noticeCounts: {jobs: 0, review: 0, workflow: 0}, onNoticeSelect: noop,
  sidebar: '会话列表', dock: '运行记录', inspector: '共享预览内容', children: '共享会话内容', actions: '账号',
  inspectorScope: 'session', companionBar: {
    tabs: ['changes'], active: 'changes', label: () => '任务改动', title: () => '任务改动',
    merged: false, mainActive: false, plugins: [], canCreateChat: false, creatingChat: false,
    closePending: () => false, onMain: noop, onSelect: noop, onClose: noop,
    onCreateChat: noop, onReopen: noop, onSplit: noop,
  },
};

describe('工作区布局', () => {
  it.each([['desktop', DesktopStudioShell], ['web', WebStudioShell]] as const)('%s 组装自己的导航和分栏，使用同一份业务内容', (surface, Shell) => {
    const html = renderToString(() => <Shell {...props}/>);
    for (const text of ['共享会话内容', '共享预览内容', '侧边标签', '关闭任务改动标签', '布局与面板']) expect(html).toContain(text);
    for (const text of ['studio-sidebar-split', 'studio-browser-host', 'studio-command-trigger']) {
      expect(html.includes(text)).toBe(surface === 'desktop');
    }
    expect(html.includes('管理和查看可用 Skills')).toBe(surface === 'web');
  });
});
