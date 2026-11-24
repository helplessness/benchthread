import { ClientPlatformContext, webPlatform } from '../clientPlatform';
import { createSignal, lazy, Show, Suspense } from 'solid-js';
import App from '../App';
import { login, logout } from '../api/auth';
import { createAccountSession } from '../stores/accountSession';
import AccountMenu from '../components/AccountMenu';
import LoginForm from '../components/LoginForm';
import { AppLoading, LazyPanelFallback } from '../components/WorkspaceLoading';
import type { SettingsSection } from '../utils/settingsSections';
import WebStudioShell from './WebStudioShell';

const SettingsWorkspace = lazy(() => import('../components/SettingsWorkspace'));
const TenantManagementPanel = lazy(() => import('../components/TenantManagementPanel'));

export default function WebApp() {
  const session = createAccountSession();
  const [email, setEmail] = createSignal('admin@example.com');
  const [manageTenants, setManageTenants] = createSignal(false);
  const inactive = () => session.user()?.tenant_status === 'inactive';

  async function signOut() {
    await logout();
    setEmail(session.user()?.email || email());
    setManageTenants(false);
    session.leave();
  }
  function SettingsTab(props: {workspaceId?: string; section: SettingsSection; onSectionChange: (section: SettingsSection) => void}) {
    return <SettingsWorkspace section={props.section} onSectionChange={props.onSectionChange} workspaceId={props.workspaceId}
      account={session.user()?.password_mode ? session.user()! : undefined} onPasswordSaved={session.passwordSaved}/>;
  }
  return <ClientPlatformContext.Provider value={webPlatform}><Show when={!session.checking()} fallback={<AppLoading label="正在确认登录状态…"/>}>
    <Show when={session.scope()} keyed fallback={<LoginForm initialEmail={email()} onSubmit={async (address, password) => session.enter(await login(address, password))}/>}>
      {_scope => <>
        <Show when={!inactive()}>
          <App Shell={WebStudioShell} user={session.user()!}
            accountMenu={<AccountMenu identity={session.user()!.email || session.user()!.user_id} managementLabel="账号与租户"
              onManage={() => setManageTenants(true)} onLogout={signOut}/>}
            SettingsTab={SettingsTab}/>
        </Show>
        <Show when={inactive() || manageTenants()}>
          <Suspense fallback={<LazyPanelFallback/>}><TenantManagementPanel currentUser={session.user()!}
            onClose={() => setManageTenants(false)} onTenantSwitched={user => {
              setManageTenants(false);
              session.enter(user);
            }}/></Suspense>
        </Show>
      </>}
    </Show>
  </Show></ClientPlatformContext.Provider>;
}
