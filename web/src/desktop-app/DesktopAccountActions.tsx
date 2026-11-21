import { createSignal, For, Show } from 'solid-js';
import FeedbackDialog from '../components/FeedbackDialog';
import { login, logout, type AuthUser } from '../api/auth';
import AccountMenu from '../components/AccountMenu';

export default function DesktopAccountActions(props: { user: AuthUser; accounts: string[]; onOpenSettings: () => void }) {
  const [open, setOpen] = createSignal(false);
  const [email, setEmail] = createSignal('');
  const [password, setPassword] = createSignal('');
  const [error, setError] = createSignal('');
  const [busy, setBusy] = createSignal(false);
  const choices = () => props.accounts.filter(address => address !== props.user.email);

  function select(address: string) {
    setEmail(address);
    setPassword('');
    setError('');
  }

  async function switchAccount(event: SubmitEvent) {
    event.preventDefault();
    if (busy() || !email()) return;
    setBusy(true);
    try {
      await login(email(), password());
      window.location.reload();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : '切换账号失败';
      setError(message === 'Invalid email or password' ? '密码不正确，请重试' : message);
    } finally {
      setBusy(false);
    }
  }

  return <>
    <AccountMenu identity={props.user.email || '本机工作区'} managementLabel="账号设置"
      onManage={props.onOpenSettings}
      onSwitch={choices().length ? () => { select(choices()[0]); setOpen(true); } : undefined}
      onLogout={async () => { await logout(); window.location.reload(); }} />
    <Show when={open()}>
      <FeedbackDialog title="切换账号" eyebrow="本机账号" onClose={() => { if (!busy()) setOpen(false); }}>
        <p>当前账号 · {props.user.email || '本机工作区'}</p>
        <form class="settings-form" onSubmit={switchAccount}>
          <label>在这台电脑上登录过的账号
            <select value={email()} disabled={busy()} onChange={event => select(event.currentTarget.value)}>
              <For each={choices()}>{address => <option value={address}>{address}</option>}</For>
            </select>
          </label>
          <label>密码
            <input type="password" required autofocus autocomplete="current-password" value={password()}
              onInput={event => setPassword(event.currentTarget.value)} />
          </label>
          <Show when={error()}><p role="alert">{error()}</p></Show>
          <footer class="feedback-dialog-actions">
            <button type="button" disabled={busy()} onClick={() => setOpen(false)}>取消</button>
            <button class="feedback-primary" type="submit" disabled={busy()}>确认切换</button>
          </footer>
        </form>
      </FeedbackDialog>
    </Show>
  </>;
}
