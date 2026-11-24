import { For, Show } from 'solid-js';
import { ActivityIcon, createStudioControls, type StudioShellProps } from '../components/studioControls';
import { CompanionTabList, CompanionActions } from '../components/CompanionTabs';
import { studioNavigationPages } from '../utils/studioLayout';
import '../studio.css';
import '../studioTheme.css';
import '../interface.css';
import './layout.css';
import DesktopServices from '../components/DesktopServices';

export default function WebStudioShell(props: StudioShellProps) {
  const groups = [...new Set(studioNavigationPages.map(page => page.group))];
  const ui = createStudioControls(props, {
    storagePrefix: 'tcode-studio-layout-v1',
    treeShare: .22,
    railWidth: 52,
    mainMinimum: () => 360,
    treeReserve: width => width,
    shrinkInspectorOnTreeResize: false,
  }, studioNavigationPages);
  return <ui.Frame>
    <header class="studio-topbar" hidden={ui.settingsMode()}>
      <ui.Brand/><ui.Notices/>
      <div class="studio-tools" data-testid="app-topbar-actions">
        <DesktopServices onOpenSettings={props.onOpenServiceSettings}/>
        <ui.FocusButton/><ui.OpenedTabs/><ui.LayoutMenu device="此浏览器"/>
        <button onClick={() => props.onNavigate('assets')}>文件改动</button>
        <button onClick={() => props.onNavigate('subagents')}>协作{props.subagentCount ? ` · ${props.subagentCount}` : ''}</button>
        <button onClick={props.onOpenSkillCenter} title="管理和查看可用 Skills" data-testid="skill-center-trigger">Skill Center · {props.skillCount}</button>
        {props.actions}
      </div>
    </header>
    <nav class="studio-rail" aria-label="活动导航" hidden={ui.hidden('rail')}>
      <button class="studio-rail-new" aria-label="新建会话" onClick={props.onNewSession}><ActivityIcon name="new"/><span class="studio-rail-label" role="tooltip">新建会话</span></button>
      <For each={groups}>{group => <ui.GroupButton group={group}/>}</For>
    </nav>
    <aside id="studio-tree" class="studio-explorer" hidden={ui.hidden('tree')} aria-label="资源与会话">
      <header><b>{ui.activity()==='任务'?'任务与研究空间':ui.activity()}</b><button aria-label="收起资源与会话" onClick={()=>ui.toggle('tree')}>×</button></header>
      <ui.FeatureList/>
      <div class="studio-sessions">{props.sidebar}</div>
    </aside>
    <section class="studio-work">
      <ui.WorkTabs/>
      <main id="studio-main" tabindex="-1" aria-label={ui.activeTitle()}>{props.children}</main>
    </section>
    <aside id="studio-inspector" class="studio-context" data-browser={false} data-collaboration={(props.inspectorTitle === '协作' || props.inspectorTitle === '伴随查看')} hidden={ui.hidden('inspector')} aria-label={props.inspectorTitle || '会话上下文'}>
      <header><b>{props.inspectorTitle || '会话上下文'}</b><button aria-label="收起右侧面板" onClick={()=>ui.toggle('inspector')}>×</button></header>
      <div><Show when={props.inspectorScope} keyed>{_scope => <>
        <div class="companion-tabs"><CompanionTabList {...props.companionBar}/><CompanionActions {...props.companionBar}/></div>
        {props.inspector}
      </>}</Show></div>
    </aside>
    <ui.Dock/>
    {ui.divider('tree')}{ui.divider('inspector')}{ui.divider('dock')}
    <footer class="studio-status" hidden={ui.focused() || ui.settingsMode()}><span>{props.sessionTitle}</span><span>{props.status}</span><span>本地后端 · 当前账号工作区</span></footer>
  </ui.Frame>;
}
