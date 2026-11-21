import { Show, createSignal } from 'solid-js';

export default function PersonalArtifactBinding(props: { id: string; url?: string; bound: boolean; saved: boolean; changed: (value: boolean) => void }) {
  const [busy, setBusy] = createSignal(false);
  const [message, setMessage] = createSignal('');
  let input!: HTMLInputElement;
  async function submit(remove: boolean) {
    if (busy()) return;
    setBusy(true); setMessage('');
    try {
      const response = await fetch('/api/artifact-services/personal/' + encodeURIComponent(props.id), {
        method: remove ? 'DELETE' : 'PUT', headers: { 'Content-Type': 'application/json' },
        body: remove ? undefined : JSON.stringify({token: input.value.trim(), expected_url: props.url}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败');
      props.changed(data.bound);
      setMessage(remove ? '已解除本账号绑定，远端文件保留。' : '服务已验证，本账号的绑定已生效。');
    } catch (e) { setMessage(e instanceof Error ? e.message : '请求失败，请重试。'); }
    finally { if (input) input.value = ''; setBusy(false); }
  }
  return <form class="settings-form" onSubmit={e => { e.preventDefault(); void submit(false); }}>
    <fieldset disabled={busy()}><legend>{props.url ? '我的文件服务' : props.id} · {props.bound ? '已绑定' : props.saved ? (props.url ? '地址已变更' : '配置已移除') : '未绑定'}</legend>
      <Show when={props.url}>
      <label>个人令牌<input type="password" required maxLength={512} autocomplete="off" ref={input} /></label>
      <p class="service-hint">填写文件服务管理员发给你的令牌。仅当前登录账号使用，不用于后台计算。</p>
      <button type="submit">{busy() ? '处理中…' : props.bound ? '验证并更新令牌' : '验证并绑定'}</button>
      </Show>
      <Show when={props.saved}><button type="button" onClick={() => void submit(true)}>解除我的绑定</button></Show>
    </fieldset><p role="status">{message()}</p>
  </form>;
}
