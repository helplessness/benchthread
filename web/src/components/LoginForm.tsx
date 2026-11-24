import { createSignal, For, Show, type JSX } from 'solid-js';

interface LoginFormProps {
  initialEmail?: string;
  recentEmails?: string[];
  registering?: boolean;
  onSubmit: (email: string, password: string) => Promise<void>;
  footer?: (form: {busy: () => boolean; reset: () => void; setError: (message: string) => void}) => JSX.Element;
}

export default function LoginForm(props: LoginFormProps) {
  const [loginEmail, setLoginEmail] = createSignal(props.initialEmail || '');
  const [loginPassword, setLoginPassword] = createSignal('');
  const [loginPasswordVisible, setLoginPasswordVisible] = createSignal(false);
  const [loginError, setLoginError] = createSignal('');
  const [loginSubmitting, setLoginSubmitting] = createSignal(false);
  function reset() {
    setLoginEmail('');
    setLoginPassword('');
    setLoginPasswordVisible(false);
    setLoginError('');
  }
  async function submit(event: SubmitEvent) {
    event.preventDefault();
    if (loginSubmitting()) return;
    setLoginError('');
    setLoginSubmitting(true);
    try {
      await props.onSubmit(loginEmail().trim(), loginPassword());
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : '请求失败，请重试');
    } finally {
      setLoginSubmitting(false);
    }
  }

  return (
    <div class="desktop-login flex min-h-screen items-center justify-center bg-[#0f1418] px-4 text-[#dee3e8]">
      <div class="desktop-login-intro"><span>Tcode</span><h2>你的研究，<br/>从这里继续。</h2><p>组织文件与知识，连接远程计算，<br/>推进每一个研究任务。</p><div>会话与协作 · 工作流 · 文献与知识 · 插件</div></div>
      <form
        class="w-full max-w-sm rounded border border-[#2a3136] bg-[#171c20] p-6 shadow-[0_24px_80px_rgba(0,0,0,0.42)]"
        onSubmit={submit}
      >
        <div class="mb-5">
          <h1 class="text-xl font-semibold text-[#f7fafc]">{props.registering ? '注册本机账号' : '登录 Tcode'}</h1>
          <p class="mt-1 text-sm text-[#9faeb7]">{props.registering ? '账号保存在这台电脑上，有独立的工作区和会话。' : '使用已配置的账号进入工作区。'}</p>
        </div>
        <Show when={!props.registering && props.recentEmails?.length}>
          <label class="mb-3 block">最近登录的账号
            <select class="block w-full" value={props.recentEmails?.includes(loginEmail()) ? loginEmail() : ''}
              onChange={event => { setLoginEmail(event.currentTarget.value); setLoginPassword(''); setLoginError(''); }}>
              <option value="">使用其他账号</option>
              <For each={props.recentEmails}>{email => <option value={email}>{email}</option>}</For>
            </select>
          </label>
        </Show>
        <label class="mb-3 block">
          <span class="mb-1 block text-xs font-semibold text-[#bdc8d1]">邮箱</span>
          <input
            class="h-10 w-full rounded border border-[#303539] bg-[#0f1418] px-3 text-sm text-[#f7fafc] outline-none transition-colors focus:border-[#7bd0ff]/60"
            type="email"
            value={loginEmail()}
            onInput={(event) => setLoginEmail(event.currentTarget.value)}
            autocomplete="username"
            required
          />
        </label>
        <div class="mb-4">
        <label for="login-password" class="block">
          <span class="mb-1 block text-xs font-semibold text-[#bdc8d1]">密码</span>
        </label>
        <div class="relative">
          <input
            id="login-password"
            class="h-10 w-full rounded border border-[#303539] bg-[#0f1418] pl-3 pr-16 text-sm text-[#f7fafc] outline-none transition-colors focus:border-[#7bd0ff]/60"
            type={loginPasswordVisible() ? 'text' : 'password'}
            value={loginPassword()}
            onInput={(event) => setLoginPassword(event.currentTarget.value)}
            autocomplete={props.registering ? "new-password" : "current-password"}
            minlength={props.registering ? 12 : undefined}
            placeholder={props.registering ? "至少 12 个字符" : undefined}
            required
          />
          <button type="button" class="absolute right-2 top-1/2 -translate-y-1/2 rounded px-2 py-1 text-xs" aria-label={loginPasswordVisible() ? '隐藏密码' : '显示密码'} aria-pressed={loginPasswordVisible()} aria-controls="login-password" onClick={() => setLoginPasswordVisible(value => !value)}>{loginPasswordVisible() ? '隐藏' : '显示'}</button>
        </div>
        </div>
        <Show when={loginError()}>
          <div role="alert" class="mb-4 rounded border border-[#ef4444]/35 bg-[#ef4444]/10 px-3 py-2 text-sm text-[#fca5a5]">{loginError()}</div>
        </Show>
        <button
          class="inline-flex h-10 w-full items-center justify-center rounded border border-[#38bdf8]/35 bg-[#38bdf8]/14 text-sm font-semibold text-[#8ed5ff] transition-colors hover:bg-[#38bdf8]/20 disabled:cursor-not-allowed disabled:opacity-60"
          type="submit"
          disabled={loginSubmitting()}
        >
          {props.registering ? (loginSubmitting() ? '正在创建…' : '创建并登录') : (loginSubmitting() ? '登录中...' : '登录')}
        </button>
        {props.footer?.({busy: loginSubmitting, reset, setError: setLoginError})}
      </form>
    </div>
  );
}
