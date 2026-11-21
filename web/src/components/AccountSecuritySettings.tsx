import { createSignal, Show } from 'solid-js';
import FeedbackDialog, { showFeedback } from './FeedbackDialog';
import { changeAccountPassword, type AuthUser } from '../api/auth';

function PasswordDialog(props: { email: string; setup: boolean; onClose: () => void; onSaved: () => void }) {
  const [current, setCurrent] = createSignal('');
  const [next, setNext] = createSignal('');
  const [confirm, setConfirm] = createSignal('');
  const [error, setError] = createSignal('');
  const [saving, setSaving] = createSignal(false);

  async function save(event: SubmitEvent) {
    event.preventDefault();
    if (saving()) return;
    if (next() !== confirm()) {
      setError('两次输入的新密码不一致');
      return;
    }
    setSaving(true);
    setError('');
    try {
      await changeAccountPassword(current(), next());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '保存失败，请重试');
      return;
    } finally {
      setSaving(false);
    }
    props.onSaved();
    await showFeedback('下次登录这个账号时，请使用新密码。', { title: '密码已保存', tone: 'success' });
  }

  return <FeedbackDialog title={props.setup ? '设置登录密码' : '修改密码'} eyebrow={props.email}
    onClose={() => { if (!saving()) props.onClose(); }}>
    <form class="settings-form" onSubmit={save}>
      <Show when={!props.setup}>
        <label>当前密码<input type="password" autocomplete="current-password" required autofocus
          value={current()} onInput={event => setCurrent(event.currentTarget.value)} /></label>
      </Show>
      <label>新密码<input type="password" autocomplete="new-password" required minlength={12}
        autofocus={props.setup} placeholder="至少 12 个字符"
        value={next()} onInput={event => setNext(event.currentTarget.value)} /></label>
      <label>确认新密码<input type="password" autocomplete="new-password" required
        value={confirm()} onInput={event => setConfirm(event.currentTarget.value)} /></label>
      <Show when={error()}><p role="alert">{error()}</p></Show>
      <footer class="feedback-dialog-actions">
        <button type="button" disabled={saving()} onClick={props.onClose}>取消</button>
        <button class="feedback-primary" type="submit" disabled={saving()}>保存密码</button>
      </footer>
    </form>
  </FeedbackDialog>;
}

export default function AccountSecuritySettings(props: { user: AuthUser; onPasswordSaved: () => void }) {
  const [editing, setEditing] = createSignal(false);
  const action = () => props.user.password_mode === 'setup' ? '设置登录密码' : '修改密码';
  return <section class="settings-page">
    <h2>账号与安全</h2>
    <div class="settings-group">
      <div class="settings-select-row"><span><b>当前账号</b><small>{props.user.email || '本机工作区'}</small></span></div>
      <Show when={props.user.password_mode}>
        <div class="settings-select-row">
          <span><b>登录密码</b><small>用于登录当前账号</small></span>
          <button class="settings-save" onClick={() => setEditing(true)}>{action()}</button>
        </div>
      </Show>
    </div>
    <Show when={editing()}>
      <PasswordDialog email={props.user.email} setup={props.user.password_mode === 'setup'}
        onClose={() => setEditing(false)} onSaved={() => { setEditing(false); props.onPasswordSaved(); }} />
    </Show>
  </section>;
}
