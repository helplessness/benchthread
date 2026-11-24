import { Show, createEffect, createMemo, createSignal, onCleanup, untrack, type Accessor } from 'solid-js';
import { useClientPlatform } from '../clientPlatform';
import { browserLiveStore, type BrowserLiveSession } from '../stores/browserLive';

export type BrowserLivePanelProps = {
  parentSessionId: string;
  workspaceId: string;
  running: boolean;
};

const terminalBrowserStatuses = new Set(['closed', 'idle_timeout', 'stopped']);

const statusLabel = (status: string, connected: boolean) => {
  const normalizedStatus = String(status || '').trim().toLowerCase();
  if (!connected && !terminalBrowserStatuses.has(normalizedStatus)) return '正在连接';
  switch (normalizedStatus) {
    case 'starting': return '正在启动';
    case 'running': return '正在操作';
    case 'paused': return '等待确认';
    case 'ready': return '实时';
    case 'closed': return '已关闭';
    case 'idle_timeout': return '空闲结束';
    case 'stopped': return '已停止';
    case 'error': return '异常';
    default: return status || '等待画面';
  }
};

export function createBrowserTask(props: BrowserLivePanelProps, keepActive?: (item: BrowserLiveSession) => boolean) {
  return createMemo(() => {
    const item = browserLiveStore.activeForParent(props.parentSessionId);
    if (!item || (!props.running && !keepActive?.(item))) return;
    if (!terminalBrowserStatuses.has(item.status.trim().toLowerCase())) return item;
  });
}

export function createBrowserObservation(
  active: Accessor<BrowserLiveSession | undefined>,
  effectiveWorkspaceId: Accessor<string>,
  options?: {
    onConnect?: () => void;
    onState?: (state: { desktop_browser?: boolean }, previousStatus: string, status: string) => boolean;
  },
) {
  const [frameUrl, setFrameUrl] = createSignal('');
  const [connected, setConnected] = createSignal(false);
  const [connectionError, setConnectionError] = createSignal('');
  const [controlMode, setControlMode] = createSignal<'agent' | 'human' | 'paused'>('agent');
  const [pendingConfirmation, setPendingConfirmation] = createSignal<{ action?: string; summary?: string } | null>(null);
  let liveSocket: WebSocket | null = null;
  const connectionKey = createMemo(() => {
    const item = active();
    const workspaceId = effectiveWorkspaceId();
    if (!item || item.key === 'manual' || !workspaceId) return '';
    const status = String(item.status || '').trim().toLowerCase();
    const lifecycle = terminalBrowserStatuses.has(status) ? 'terminal' : 'live';
    return `${item.key}:${workspaceId}:${lifecycle}`;
  });

  const replaceFrame = (blob: Blob) => {
    const next = URL.createObjectURL(blob);
    setFrameUrl((previous) => {
      if (previous) URL.revokeObjectURL(previous);
      return next;
    });
  };

  const clearFrame = () => {
    setFrameUrl((previous) => {
      if (previous) URL.revokeObjectURL(previous);
      return '';
    });
  };

  const sendControl = (payload: Record<string, unknown>) => {
    if (liveSocket?.readyState !== WebSocket.OPEN) return;
    liveSocket.send(JSON.stringify(payload));
  };

  createEffect(() => {
    const key = connectionKey();
    // Only the stable connection key should restart this effect. Browser state
    // updates replace the active session object on every frame; tracking that
    // object here would close the socket after the JSON metadata arrives and
    // before the following binary JPEG frame can be delivered.
    const item = untrack(active);
    const workspaceId = untrack(effectiveWorkspaceId);
    if (!key || !item || !workspaceId || item.key === 'manual') return;

    clearFrame();
    setConnected(false);
    setControlMode('agent');
    untrack(() => options?.onConnect?.());
    setConnectionError('');
    if (terminalBrowserStatuses.has(String(item.status || '').trim().toLowerCase())) {
      return;
    }

    let disposed = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | undefined;
    let reconnectAttempt = 0;

    const connect = () => {
      if (disposed) return;
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const query = new URLSearchParams({
        browser_session_id: item.browserSessionId,
        workspace_id: workspaceId,
      });
      socket = new WebSocket(`${protocol}//${window.location.host}/ws/browser?${query.toString()}`);
      liveSocket = socket;
      socket.binaryType = 'blob';
      socket.onopen = () => {
        if (disposed) return;
        reconnectAttempt = 0;
        setConnected(true);
        setConnectionError('');
      };
      socket.onmessage = (event) => {
        if (disposed) return;
        if (event.data instanceof Blob) {
          replaceFrame(event.data);
          return;
        }
        try {
          const data = JSON.parse(String(event.data || '{}'));
          if (data.type !== 'browser_state') return;
          const nextStatus = String(data.status || '').trim().toLowerCase();
          if (!options?.onState?.(data, item.status, nextStatus)) {
            setControlMode(['agent', 'human', 'paused'].includes(data.control_mode) ? data.control_mode : 'agent');
          }
          setPendingConfirmation(data.pending_confirmation && typeof data.pending_confirmation === 'object'
            ? data.pending_confirmation
            : null);
          if (terminalBrowserStatuses.has(nextStatus)) {
            clearFrame();
          }
          browserLiveStore.upsert({
            parentSessionId: item.parentSessionId,
            agentSessionId: item.agentSessionId,
            browserSessionId: item.browserSessionId,
            workspaceId,
            status: data.status,
            action: data.action,
            url: data.url,
            title: data.title,
            frameSeq: Number(data.frame_seq || 0),
            error: data.error,
          });
        } catch {
          // Heartbeats and malformed metadata must not interrupt the frame stream.
        }
      };
      socket.onerror = () => {
        if (!disposed) setConnectionError('实时画面连接暂时不可用');
      };
      socket.onclose = () => {
        if (disposed) return;
        setConnected(false);
        socket = null;
        liveSocket = null;
        const latest = browserLiveStore.activeForParent(item.parentSessionId);
        if (latest?.browserSessionId !== item.browserSessionId) return;
        if (terminalBrowserStatuses.has(String(latest.status || '').toLowerCase())) return;
        reconnectAttempt += 1;
        const delay = Math.min(5000, 400 * (2 ** Math.min(reconnectAttempt, 4)));
        reconnectTimer = window.setTimeout(connect, delay);
      };
    };

    connect();
    onCleanup(() => {
      disposed = true;
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      socket?.close();
      if (liveSocket === socket) liveSocket = null;
    });
  });

  onCleanup(() => {
    const current = frameUrl();
    if (current) URL.revokeObjectURL(current);
  });

  return { frameUrl, connected, controlMode, setControlMode, connectionError, setConnectionError, pendingConfirmation, replaceFrame, sendControl };
}

export function BrowserObservationView(props: {
  item: BrowserLiveSession;
  observation: ReturnType<typeof createBrowserObservation>;
  nativeFrame?: boolean;
  onTakeControl: () => void;
}) {
  const item = () => props.item;
  const terminal = () => terminalBrowserStatuses.has(item().status.trim().toLowerCase());
  const [expanded, setExpanded] = createSignal(false);
  return (
    <Show when={!['waiting_login', 'login_interrupted'].includes(item().status)}>
      <Show
        when={!item().hidden}
        fallback={
          <button
            type="button"
            class="fixed bottom-24 right-5 z-[110] inline-flex items-center gap-2 rounded-full border border-[#38bdf8]/45 bg-[#101a20]/95 px-4 py-2 text-xs font-semibold text-[#bdeaff] shadow-[0_16px_40px_rgba(0,0,0,0.4)] backdrop-blur"
            onClick={() => browserLiveStore.setHidden(item().key, false)}
          >
            <span class="h-2 w-2 rounded-full bg-[#38bdf8]" />
            显示浏览器画面
          </button>
        }
      >
        <section
          class={expanded()
            ? 'fixed inset-4 z-[140] flex flex-col overflow-hidden rounded-2xl border border-[#31536a] bg-[#090d10] shadow-[0_28px_100px_rgba(0,0,0,0.68)]'
            : 'fixed bottom-24 right-5 z-[110] flex w-[min(560px,calc(100vw-2.5rem))] flex-col overflow-hidden rounded-2xl border border-[#31536a] bg-[#0b1115]/98 shadow-[0_24px_70px_rgba(0,0,0,0.58)] backdrop-blur'}
          aria-label="浏览器实时观察面板"
        >
          <header class="flex items-start justify-between gap-3 border-b border-[#26313a] px-3 py-2.5">
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2">
                <span class={`h-2 w-2 rounded-full ${props.observation.connected() ? 'bg-[#7ee787]' : 'bg-[#facc15]'}`} />
                <span class="text-xs font-semibold text-[#e6edf3]">浏览器观察</span>
                <span class="rounded-full border border-[#31536a] bg-[#10202a] px-2 py-0.5 text-[10px] text-[#8ed5ff]">
                  {props.nativeFrame && props.observation.connected() && item().status === 'ready' ? '已更新' : statusLabel(item().status, props.observation.connected())}
                </span>
                <Show when={item().action}>
                  <span class="font-mono text-[10px] text-[#7f8b93]">{item().action}</span>
                </Show>
              </div>
              <div class="mt-1 truncate text-[11px] text-[#9faeb7]" title={item().url || item().title}>
                {terminal() ? '浏览器会话已结束' : (item().title || item().url || '等待浏览器打开页面')}
              </div>
            </div>
            <div class="flex shrink-0 items-center gap-1">
              <button
                type="button"
                class={`rounded-lg border px-2 py-1 text-[11px] ${props.observation.controlMode() === 'human' ? 'border-[#f1a02b] bg-[#3a2910] text-[#ffd17a]' : 'border-[#3e484f] bg-[#171c20] text-[#b7c7d0]'}`}
                disabled={!props.observation.connected() || terminal()}
                onClick={() => props.onTakeControl()}
              >
                {props.observation.controlMode() === 'human' ? '正在接管控制中' : '接管控制'}
              </button>
              <button
                type="button"
                class="rounded-lg border border-[#3e484f] bg-[#171c20] px-2 py-1 text-[11px] text-[#b7c7d0] hover:bg-[#1d272d]"
                onClick={() => setExpanded((value) => !value)}
              >
                {expanded() ? '还原' : '放大'}
              </button>
              <button
                type="button"
                class="rounded-lg border border-[#3e484f] bg-[#171c20] px-2 py-1 text-[11px] text-[#b7c7d0] hover:bg-[#1d272d]"
                onClick={() => browserLiveStore.setHidden(item().key, true)}
              >
                隐藏
              </button>
            </div>
          </header>

          <div class={`relative flex items-center justify-center bg-black ${expanded() ? 'min-h-0 flex-1' : 'aspect-[16/10]'}`}>
            <Show when={props.observation.pendingConfirmation()}>
              {(confirmation) => (
                <div class="absolute left-3 right-3 top-3 z-10 rounded-xl border border-[#f1a02b]/60 bg-[#241b0d]/95 p-3 text-xs text-[#ffe0a3] shadow-xl">
                  <div class="font-semibold">Agent 请求执行敏感操作</div>
                  <div class="mt-1 break-words text-[#d9c39a]">{confirmation().summary || confirmation().action || '请确认是否继续'}</div>
                  <div class="mt-2 flex gap-2">
                    <button class="rounded-lg bg-[#f1a02b] px-3 py-1 font-semibold text-black" onClick={() => props.observation.sendControl({ action: 'confirm_pending' })}>确认执行</button>
                    <button class="rounded-lg border border-[#6b5a3b] px-3 py-1" onClick={() => props.observation.sendControl({ action: 'reject_pending' })}>拒绝</button>
                  </div>
                </div>
              )}
            </Show>
            <Show
              when={props.observation.frameUrl()}
              fallback={
                <div class="flex flex-col items-center gap-3 px-6 text-center text-sm text-[#7f8b93]">
                  <Show when={!terminal()}>
                    <div class="h-7 w-7 animate-spin rounded-full border-2 border-[#31536a] border-t-[#8ed5ff]" />
                  </Show>
                  <span>{terminal() ? '实时画面已清理' : (props.observation.connectionError() || '正在等待第一帧画面…')}</span>
                </div>
              }
            >
              <img
                src={props.observation.frameUrl()}
                alt="Browser Agent 实时页面"
                class="h-full w-full object-contain outline-none"
              />
            </Show>
          </div>

          <footer class="flex items-center justify-between gap-3 border-t border-[#26313a] px-3 py-2 text-[10px] text-[#6f7d87]">
            <span class="min-w-0 flex-1 truncate" title={terminal() ? '' : item().url}>
              {terminal() ? '会话数据已清理' : (item().url || 'about:blank')}
            </span>
            <span class="shrink-0 font-mono">frame {terminal() ? 0 : item().frameSeq}</span>
          </footer>
        </section>
      </Show>
    </Show>
  );
}

export default function BrowserLivePanel(props: BrowserLivePanelProps) {
  const NativeBrowser = useClientPlatform().BrowserPanel;
  if (NativeBrowser) return <NativeBrowser {...props}/>;

  const active = createBrowserTask(props);
  const observation = createBrowserObservation(active, () => active()?.workspaceId || props.workspaceId);
  return <Show when={active()}>{item => <>
    <Show when={item().status === 'waiting_login'}>
      <section aria-label="浏览器登录确认" class="rounded-lg border border-current/20 p-3">
        <p role="status">需要登录 {item().title || '目标网站'}，当前读取已暂停。</p>
        <p>请前往 TCode 桌面端处理登录请求。</p>
      </section>
    </Show>
    <Show when={item().status === 'login_interrupted'}>
      <p role="status">原浏览器登录请求已结束，未继续读取。请重新发起读取，需要时会重新请你登录。</p>
    </Show>
    <BrowserObservationView item={item()} observation={observation}
      onTakeControl={() => observation.sendControl({ action: 'set_control', mode: 'human' })}/>
  </>}</Show>;
}
