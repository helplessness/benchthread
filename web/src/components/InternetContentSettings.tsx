import { Index, Show, createSignal, onMount } from 'solid-js';
import { desktop as desktopBridge } from '../desktop';
import { confirmAction } from './FeedbackDialog';
import './InternetContentSettings.css';

type Channel = {
  name: string;
  permitted: boolean;
  enabled: boolean;
  public_check: boolean;
  account: boolean;
  actions: string[];
  last_check?: { ok: boolean; error?: string; checked_at: string; action: string };
};
type Settings = { configured: boolean; transcription_configured: boolean; channels: Channel[] };

const labels: Record<string, string> = {
  github: 'GitHub', v2ex: 'V2EX', bilibili: 'B 站', youtube: 'YouTube', xueqiu: '雪球', xiaoyuzhou: '小宇宙',
  linkedin: 'LinkedIn', reddit: 'Reddit', boss: 'BOSS 直聘', xiaohongshu: '小红书',
  x: 'X', facebook: 'Facebook', instagram: 'Instagram',
};
const actions: Record<string, string> = {
  search: '搜索', read: '详情', hot: '热门', node: '节点', user: '用户',
  subtitle: '字幕', quote: '行情', transcribe: '音频转写', hot_posts: '热帖', hot_stocks: '热股',
  comments: '评论', subreddit: '版块', thread: '推文串', profile: '账号资料', posts: '动态',
};
const marks: Record<string, string> = {
  github: 'GH', v2ex: 'V2', bilibili: 'B', youtube: 'YT', xueqiu: '雪', xiaoyuzhou: '宇',
};
const errors: Record<string, string> = {
  NEED_SETUP: '未配置', NEED_LOGIN: '需要登录', TIMEOUT: '连接超时',
  RATE_LIMITED: '站点限流', PLATFORM_BLOCKED: '站点拒绝访问',
  NOT_FOUND: '未找到样例内容', NO_VERIFIABLE_CONTENT: '未取得可验证内容',
};

export default function InternetContentSettings(props: { workspaceId?: string }) {
  const desktop = Boolean(desktopBridge);
  const [settings, setSettings] = createSignal<Settings>();
  const [busy, setBusy] = createSignal('');
  const [status, setStatus] = createSignal('');
  const [error, setError] = createSignal('');
  const [failedPath, setFailedPath] = createSignal('');
  const enabledCount = () => settings()?.channels.filter(row => row.enabled).length ?? 0;

  async function request(path = '', method = 'GET', body?: object) {
    setBusy(path || 'load');
    setStatus('');
    setError('');
    setFailedPath('');
    try {
      const response = await fetch(`/api/config/internet-content${path}`, {
        method, cache: 'no-store',
        ...(method !== 'GET' ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) } : {}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '渠道操作失败');
      if (data.state === 'login_window_opened') setStatus('已打开隔离浏览器，请本人登录；关闭窗口后可在当前工作区读取。');
      else if (data.state === 'local_session_cleared') setStatus('已退出此工作区的浏览器登录。');
      else {
        setSettings(data);
        if (method === 'PUT') setStatus('已保存，对后续读取生效。');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '无法连接 TCode 后端。');
      setFailedPath(path);
    } finally {
      setBusy('');
    }
  }

  onMount(() => { if (desktop) void request(); });

  return <div class="settings-page settings-page-wide internet-settings">
    <header class="internet-settings-heading">
      <div>
        <h2>互联网渠道</h2>
        <p>选择 Agent 可以从哪些站点读取内容。</p>
      </div>
      <Show when={settings()}>
        <span class="internet-count"><strong>{enabledCount()}</strong> / {settings()?.channels.length} 个渠道已启用</span>
      </Show>
    </header>
    <Show when={desktop} fallback={<p>请在 TCode 桌面端管理渠道。</p>}>
      <Show when={!settings() && busy()}><p role="status">正在读取渠道配置…</p></Show>
      <Show when={settings()}>
        <Show when={!settings()?.configured}><p role="status">尚未配置公开渠道读取，连接检查暂不可用。</p></Show>
        <div class="internet-directory">
          <div class="internet-directory-head" aria-hidden="true">
            <span>渠道与能力</span><span>最近检查</span><span>操作</span>
          </div>
          <ul aria-label="内容渠道">
            <Index each={settings()?.channels}>{row => {
              const name = () => labels[row().name] ?? row().name;
              const path = () => `/${row().name}`;
              const checking = () => busy() === `${path()}/check`;
              const check = () => row().last_check;
              const accountPath = () => `${path()}/account/`;
              async function account(operation: 'login' | 'logout') {
                const workspace = props.workspaceId;
                if (!workspace || busy()) return;
                if (operation === 'logout' && !(await confirmAction(
                  '退出将关闭该渠道正在使用的浏览器页并清除这个工作区的本地登录状态。不会退出系统浏览器。',
                  { title: `退出 ${name()}`, confirmLabel: '退出本地登录' },
                ))) return;
                if (props.workspaceId !== workspace) return;
                await request(`${accountPath()}${operation}?workspace_id=${encodeURIComponent(workspace)}`, 'POST');
              }
              return <li class="internet-channel">
                <div class="internet-channel-identity">
                  <span class="internet-channel-mark" aria-hidden="true">{marks[row().name] ?? name().slice(0, 2)}</span>
                  <div>
                    <div class="internet-channel-title">
                      <h3>{name()}</h3>
                      <span class="internet-badge" data-enabled={row().enabled}>
                        {!row().permitted ? '管理员未开放' : row().enabled ? '已启用' : '已停用'}
                      </span>
                    </div>
                    <p>{row().actions.map(action => actions[action] ?? action).join('、')}</p>
                  </div>
                </div>
                <div class="internet-check" role="status" aria-label={`${name()} 检查状态`}>
                  <span class="internet-check-label" data-result={checking() ? 'pending' : !check() ? 'none' : check()?.ok ? 'ok' : 'error'}>
                    <i aria-hidden="true" />
                    {checking() ? '正在检查' : !row().public_check ? row().name === 'xiaoyuzhou' ? '转写未验证' : '需在聊天中验证' : !check() ? '未检查' :
                      check()?.ok ? '公开样例可读' : errors[check()?.error ?? ''] ?? '未读到有效内容'}
                  </span>
                  <Show when={!checking() && check()?.checked_at}>{at =>
                    <time dateTime={at()}>{new Date(at()).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })}</time>
                  }</Show>
                </div>
                <div class="internet-channel-controls">
                  <Show when={row().public_check}>
                    <button type="button" class="internet-button internet-button-check"
                      disabled={Boolean(busy()) || !row().enabled || !settings()?.configured}
                      aria-label={`检查 ${name()}`} onClick={() => void request(`${path()}/check`, 'POST')}>
                      {checking() ? '检查中…' : '检查'}
                    </button>
                  </Show>
                  <button type="button" class="internet-button"
                    disabled={Boolean(busy()) || !row().permitted}
                    aria-label={`${row().enabled ? '停用' : '启用'} ${name()}`}
                    onClick={() => void request(path(), 'PUT', { enabled: !row().enabled })}>
                    {busy() === path() ? '保存中…' : row().enabled ? '停用' : '启用'}
                  </button>
                  <Show when={row().account && props.workspaceId}>
                    <button type="button" class="internet-button" disabled={Boolean(busy()) || !row().enabled}
                      aria-label={`登录 ${name()}`} onClick={() => void account('login')}>登录</button>
                    <button type="button" class="internet-button" disabled={Boolean(busy())}
                      aria-label={`退出 ${name()}`} onClick={() => void account('logout')}>退出</button>
                  </Show>
                </div>
                <Show when={error() && (failedPath() === path() || failedPath().startsWith(path() + '/'))}>
                  <p class="internet-channel-error" role="alert">{error()}</p>
                </Show>
              </li>;
            }}</Index>
          </ul>
        </div>
        <p class="internet-directory-note">检查只读取一个公开样例，不验证登录或其他动作。停用仅影响新的读取，不退出网站账号。</p>
        <div class="internet-transcription">
          <span class="internet-channel-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="21" height="21" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
              <path d="M3 10v4m4-7v10m5-13v16m5-13v10m4-7v4" />
            </svg>
          </span>
          <div>
            <h3>音频转写</h3>
            <p>仅在明确请求转写时向 Groq 发送公开音频。</p>
          </div>
          <span class="internet-badge">{settings()?.transcription_configured ? '已配置，待验证' : '未配置 Groq'}</span>
        </div>
      </Show>
      <div class="internet-save-status" role="status">{status()}</div>
      <Show when={error() && !failedPath()}><p role="alert">{error()}</p><button type="button" class="internet-button" onClick={() => void request()}>重新读取配置</button></Show>
    </Show>
  </div>;
}
