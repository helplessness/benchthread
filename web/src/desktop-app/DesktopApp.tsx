import { createEffect, createSignal, lazy, onCleanup, onMount, Show, Suspense } from 'solid-js';
import App from '../App';
import { ClientPlatformContext } from '../clientPlatform';
import { desktopPlatform } from './desktopPlatform';
import { enterLocalWorkspace, getDesktopAccounts, login, registerDesktopAccount, type DesktopAccounts } from '../api/auth';
import { createAccountSession } from '../stores/accountSession';
import type { SettingsSection } from '../utils/settingsSections';
import LoginForm from '../components/LoginForm';
import { AppLoading, LazyPanelFallback } from '../components/WorkspaceLoading';
import DesktopStudioShell from './DesktopStudioShell';
import DesktopAccountActions from './DesktopAccountActions';

const SettingsWorkspace = lazy(() => import('../components/SettingsWorkspace'));

function DesktopLogin(props: {accounts: DesktopAccounts}) {
  const [registering, setRegistering] = createSignal(false);
  async function signIn(email: string, password: string) {
    const creating = registering();
    if (creating) {
      await registerDesktopAccount(email, password);
      setRegistering(false);
    }
    try {
      await login(email, password);
    } catch (error) {
      if (creating) throw new Error('账号已创建，请点击登录重试');
      throw error;
    }
    window.location.reload();
  }
  return <LoginForm recentEmails={props.accounts.accounts} registering={registering()} onSubmit={signIn}
    footer={form => <>
      <Show when={props.accounts.can_register}>
        <button class="desktop-login-form-link" type="button" disabled={form.busy()}
          onClick={() => {form.reset(); setRegistering(!registering());}}>{registering() ? '返回登录' : '注册本机账号'}</button>
      </Show>
      <Show when={!registering() && props.accounts.local_workspace}>
        <button type="button" disabled={form.busy()} onClick={() => void enterLocalWorkspace().then(() => window.location.reload())
          .catch(() => form.setError('无法进入本机工作区，请重试'))}>使用本机工作区</button>
      </Show>
    </>}/>;
}

export default function DesktopApp() {
  const session = createAccountSession();
  const [accounts, setAccounts] = createSignal<DesktopAccounts>({accounts: [], local_workspace: false, can_register: false});
  onMount(() => { void getDesktopAccounts().then(setAccounts).catch(error => console.warn('无法读取最近账号列表：', error)); });

  const [section, setSection] = createSignal<SettingsSection>('general');
  const [settingsOpen, setSettingsOpen] = createSignal(false);
  function openSettings(next?: SettingsSection) {
    if (next) setSection(next);
    setSettingsOpen(true);
  }
  function SettingsWindow(props: {workspaceId?: string}) {
    createEffect(() => document.body.classList.toggle('settings-dialog-open', settingsOpen()));
    onCleanup(() => document.body.classList.remove('settings-dialog-open'));
    return <Show when={settingsOpen()}>
      <dialog class="tcode-settings-window" aria-label="设置" ref={element => queueMicrotask(() => element.isConnected && element.showModal())}
        onCancel={event => {event.preventDefault(); setSettingsOpen(false);}}>
        <header><b>设置</b><button aria-label="关闭设置窗口" onClick={() => setSettingsOpen(false)}>×</button></header>
        <div><Suspense fallback={<LazyPanelFallback/>}><SettingsWorkspace initialSection={section()} workspaceId={props.workspaceId}
          account={session.user() || undefined} onPasswordSaved={session.passwordSaved}/></Suspense></div>
      </dialog>
    </Show>;
  }

  return <ClientPlatformContext.Provider value={desktopPlatform}><Show when={!session.checking()} fallback={<AppLoading label="正在打开本机工作区…"/>}>
    <Show when={session.scope()} keyed fallback={session.failed()
      ? <div role="alert" class="settings-page"><h2>暂时无法打开本机工作区</h2><button class="settings-save" onClick={() => window.location.reload()}>重新连接</button></div>
      : <DesktopLogin accounts={accounts()}/>}>
      {_scope => <App Shell={DesktopStudioShell} user={session.user()!}
        accountMenu={<DesktopAccountActions user={session.user()!} accounts={accounts().accounts} onOpenSettings={() => openSettings('account')}/>}
        openSettings={openSettings} SettingsWindow={SettingsWindow}/>}
    </Show>
  </Show></ClientPlatformContext.Provider>;
}
