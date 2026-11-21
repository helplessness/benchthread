import { createSignal, onCleanup, onMount, Show } from 'solid-js';
import { dismissDetailsOnOutsideClick } from '../utils/dismissDetails';
import { showFeedback } from './FeedbackDialog';

export default function AccountMenu(props: {
  identity: string;
  managementLabel: string;
  onManage: () => void;
  onSwitch?: () => void;
  onLogout: () => Promise<void>;
}) {
  let menu!: HTMLDetailsElement;
  let trigger!: HTMLElement;
  onMount(() => onCleanup(dismissDetailsOnOutsideClick(() => menu)));
  function closeMenu() {
    menu.open = false;
    trigger.focus();
  }
  const [loggingOut, setLoggingOut] = createSignal(false);
  async function leave() {
    if (loggingOut()) return;
    closeMenu();
    setLoggingOut(true);
    try {
      await props.onLogout();
    } catch {
      await showFeedback('退出失败，请重试');
    } finally {
      setLoggingOut(false);
    }
  }

  return (
    <details ref={menu} class="studio-account-menu"
      onKeyDown={event => {
        if (event.key === 'Escape' && menu.open) { event.stopPropagation(); closeMenu(); }
      }}
      onFocusOut={event => { if (event.relatedTarget && !menu.contains(event.relatedTarget as Node)) menu.open = false; }}>
      <summary ref={trigger} aria-label={`当前账号 · ${props.identity}`} title={props.identity}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
          <circle cx="12" cy="8" r="3.5"/><path d="M5 20v-2a7 7 0 0 1 14 0v2" stroke-linecap="round"/>
        </svg>
        账号
      </summary>
      <div class="studio-account-popup">
        <header><small>当前账号</small><strong>{props.identity}</strong></header>
        <button onClick={() => { closeMenu(); props.onManage(); }}>{props.managementLabel}</button>
        <Show when={props.onSwitch}>
          <button onClick={() => { closeMenu(); props.onSwitch?.(); }}>切换账号</button>
        </Show>
        <button class="studio-account-logout" disabled={loggingOut()} onClick={() => { void leave(); }}>退出登录</button>
      </div>
    </details>
  );
}
