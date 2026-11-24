import { For, Show, createSignal, onMount, onCleanup } from 'solid-js';
import { isSideChatView, type CompanionView } from '../utils/companionState';
import TabIndicator from './TabIndicator';

export interface CompanionBarProps {
  tabs: CompanionView[];
  active: CompanionView;
  label: (view: CompanionView) => string;
  title: (view: CompanionView) => string;
  merged: boolean;
  mainActive: boolean;
  lastClosed?: CompanionView;
  plugins: {title: string; open: () => void}[];
  canCreateChat: boolean;
  creatingChat: boolean;
  closePending: (view: CompanionView) => boolean;
  onMain: () => void;
  onSelect: (view: CompanionView) => void;
  onClose: (view: CompanionView) => void;
  onCreateChat: () => void;
  onReopen: () => void;
  onSplit: () => void;
}

export function CompanionTabList(props: CompanionBarProps) {
  return (
    <nav class="companion-tab-list elastic-tabs" aria-label="侧边标签">
      <TabIndicator active={props.merged&&props.mainActive?'main':props.active} selector=".companion-tab[data-active=true]" top />
      <Show when={props.merged}><div class="companion-tab" data-active={props.mainActive}><button aria-pressed={props.mainActive} onClick={props.onMain}>主界面</button></div></Show>
      <For each={props.tabs}>{tab => <div class="companion-tab" data-active={props.active===tab&&!(props.merged&&props.mainActive)}>
        <button title={props.title(tab)} aria-pressed={props.active===tab&&!(props.merged&&props.mainActive)} onClick={()=>props.onSelect(tab)}>{props.label(tab)}</button>
        <button class="companion-tab-close" aria-label={`关闭${props.label(tab)}标签`} title={isSideChatView(tab) ? '关闭并删除这段侧聊' : '关闭标签'} disabled={isSideChatView(tab) && props.closePending(tab)} onClick={()=>props.onClose(tab)}>×</button>
      </div>}</For>
    </nav>
  );
}

export function CompanionActions(props: CompanionBarProps & {extraAction?: {label: string; run: () => void}}) {
  const [showCompanionMenu, setShowCompanionMenu] = createSignal(false);
  let companionMenuElement: HTMLDivElement | undefined;
  onMount(() => {
    const dismiss = (event: PointerEvent) => {
      if (!companionMenuElement?.contains(event.target as Node)) setShowCompanionMenu(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || !showCompanionMenu()) return;
      if (companionMenuElement?.contains(document.activeElement)) {
        companionMenuElement.querySelector('button')?.focus();
      }
      setShowCompanionMenu(false);
    };
    document.addEventListener('pointerdown',dismiss); document.addEventListener('keydown',escape);
    onCleanup(()=>{document.removeEventListener('pointerdown',dismiss);document.removeEventListener('keydown',escape);});
  });
  return (
    <div class="companion-actions" role="group" aria-label="侧边面板操作">
      <Show when={props.lastClosed}>{tab => (
        <button type="button" class="companion-action" aria-label={`重开标签：${props.label(tab())}`} title={`重开标签：${props.label(tab())}`} onClick={props.onReopen}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5v5h5M4 10a8 8 0 1 1 1.2 7.6"/></svg>
          <span>重开标签</span>
        </button>
      )}</Show>
      <div ref={companionMenuElement} class="companion-new">
        <button type="button" class="companion-action companion-action--add" aria-label="打开新标签" title="打开新标签" aria-expanded={showCompanionMenu()} aria-controls={showCompanionMenu() ? "companion-new-menu" : undefined} onClick={()=>setShowCompanionMenu(value=>!value)}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
          <span>打开标签</span>
        </button>
        <Show when={showCompanionMenu()}><div class="companion-new-menu" id="companion-new-menu" role="group" aria-label="选择标签类型">
          <For each={props.plugins}>{panel => <button type="button" onClick={() => {setShowCompanionMenu(false);panel.open();}}>{panel.title}</button>}</For>
          <Show when={props.extraAction}>{action => <button type="button" onClick={() => {setShowCompanionMenu(false); action().run();}}>{action().label}</button>}</Show>
          <button type="button" disabled={!props.canCreateChat || props.creatingChat} onClick={()=>{setShowCompanionMenu(false);props.onCreateChat();}}>{props.creatingChat ? '创建中…' : '新建侧边聊天'}</button>
        </div></Show>
      </div>
      <Show when={props.merged}><button type="button" class="companion-action" aria-label="恢复独立分栏" title="恢复独立分栏" onClick={props.onSplit}><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M14 4v16"/></svg></button></Show>
    </div>
  );
}
