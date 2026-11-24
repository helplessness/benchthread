import { For, Show } from 'solid-js';
import { ActivityIcon, createStudioControls, type StudioShellProps } from '../components/studioControls';
import { CompanionTabList, CompanionActions } from '../components/CompanionTabs';
import { studioNavigationPages } from '../utils/studioLayout';
import '../studio.css';
import '../studioTheme.css';
import '../interface.css';
import './layout.css';
import DesktopServices from '../components/DesktopServices';
import { openDesktopBrowser } from './browserTabs';
import ComputerUsePanel from './ComputerUsePanel';

function NavigationDivider(props: {value: number; onChange: (value: number) => void; onSave: () => void}) {
  let drag: {start: number; share: number; height: number; pointerId: number} | undefined;
  const adjust = (share: number) => props.onChange(Math.max(.10, Math.min(.55, share)));
  const reset = () => { adjust(.22); props.onSave(); };

  return <div class="studio-sidebar-split" role="separator" tabindex="0" aria-label="调整功能导航与会话列表高度"
    aria-orientation="horizontal" aria-valuemin={10} aria-valuemax={55} aria-valuenow={Math.round(props.value * 100)}
    title="上下拖动；方向键调整；双击恢复"
    onPointerDown={event => {
      if (event.button !== 0) return;
      event.preventDefault();
      const handle = event.currentTarget;
      handle.focus();
      drag = {start: event.clientY, share: props.value, height: handle.parentElement!.clientHeight, pointerId: event.pointerId};
      handle.setPointerCapture(event.pointerId);
    }}
    onPointerMove={event => {
      if (drag?.pointerId === event.pointerId) adjust(drag.share + (event.clientY - drag.start) / drag.height);
    }}
    onPointerUp={event => {
      if (drag?.pointerId !== event.pointerId) return;
      drag = undefined;
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
      props.onSave();
    }}
    onPointerCancel={() => { if (drag) adjust(drag.share); drag = undefined; }}
    onLostPointerCapture={() => { drag = undefined; }}
    onDblClick={reset}
    onKeyDown={event => {
      const steps: Record<string, number> = {ArrowUp: props.value - .02, ArrowDown: props.value + .02, Home: .10, End: .55};
      if (!(event.key in steps)) return;
      event.preventDefault();
      adjust(steps[event.key]);
      props.onSave();
    }}/>
}

export default function DesktopStudioShell(props: StudioShellProps) {
  const groups = [...new Set(studioNavigationPages.map(page => page.group))];
  const ui = createStudioControls(props, {
    storagePrefix: 'tcode-desktop-layout-v1',
    treeShare: .20,
    railWidth: 64,
    mainMinimum: width => width >= 1100 ? 560 : 360,
    treeReserve: (width, visible) => visible ? 280 : width,
    shrinkInspectorOnTreeResize: true,
  }, studioNavigationPages);
  return <ui.Frame>
    <header class="studio-topbar" hidden={ui.settingsMode()}>
      <ui.Brand/><ui.Notices/>
      <div class="studio-tools" data-testid="app-topbar-actions">
        <button class="studio-command-trigger" onClick={props.onCommandPalette} title="打开全局命令面板">搜索 <kbd>⌘K</kbd></button>
        <ComputerUsePanel/>
        <DesktopServices onOpenSettings={props.onOpenServiceSettings}/>
        <ui.FocusButton/><ui.OpenedTabs/><ui.LayoutMenu device="此电脑"/>
        {props.actions}
      </div>
    </header>
    <nav class="studio-rail" aria-label="活动导航" hidden={ui.hidden('rail')}>
      <button class="studio-rail-new" aria-label="新建会话" onClick={props.onNewSession}><ActivityIcon name="new"/><span class="studio-rail-label" role="tooltip">新建会话</span></button>
      <For each={groups}>{group => <>
        <ui.GroupButton group={group}/>
        <Show when={group === '扩展'}><button aria-label="浏览器" onClick={openDesktopBrowser}><ActivityIcon name="浏览器"/><span class="studio-rail-label" role="tooltip">浏览器</span></button></Show>
      </>}</For>
    </nav>
    <aside id="studio-tree" class="studio-explorer" hidden={ui.hidden('tree')} aria-label="资源与会话" style={{'--studio-navigation-share': `${ui.layout().navigationShare * 100}%`}}>
      <header><b>{ui.activity()==='任务'?'任务与研究空间':ui.activity()}</b><button aria-label="收起资源与会话" onClick={()=>ui.toggle('tree')}>×</button></header>
      <ui.FeatureList/>
      <NavigationDivider value={ui.layout().navigationShare}
        onChange={navigationShare => ui.setLayout(previous => ({...previous, navigationShare}))} onSave={ui.save}/>
      <div class="studio-sessions">{props.sidebar}</div>
    </aside>
    <section class="studio-work">
      <ui.WorkTabs/>
      <main id="studio-main" tabindex="-1" aria-label={ui.activeTitle()}>{props.children}</main>
    </section>
    <aside id="studio-inspector" class="studio-context" data-browser={!!props.browserActive} data-collaboration={!props.browserActive && (props.inspectorTitle === '协作' || props.inspectorTitle === '伴随查看')} hidden={ui.hidden('inspector')} aria-label={props.inspectorTitle || '会话上下文'}>
      <header><b>{props.inspectorTitle || '会话上下文'}</b><button aria-label="收起右侧面板" onClick={()=>ui.toggle('inspector')}>×</button></header>
      <div><Show when={props.inspectorScope} keyed>{_scope => <>
        <div class="companion-tabs"><CompanionTabList {...props.companionBar}/><CompanionActions {...props.companionBar} extraAction={{label: '打开浏览器', run: openDesktopBrowser}}/></div>
        <div id="studio-browser-host" hidden={!props.companionBar.active.startsWith('browser:')}/>
        {props.inspector}
      </>}</Show></div>
    </aside>
    <ui.Dock/>
    {ui.divider('tree')}{ui.divider('inspector')}{ui.divider('dock')}
    <footer class="studio-status" hidden={ui.focused() || ui.settingsMode()}><span>{props.sessionTitle}</span><span>{props.status}</span><span>本机工作区</span></footer>
  </ui.Frame>;
}
