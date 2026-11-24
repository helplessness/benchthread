import { Show, batch, createEffect, createMemo, createSignal, onCleanup, untrack } from 'solid-js';
import { Portal } from 'solid-js/web';
import { BrowserObservationView, createBrowserObservation, createBrowserTask, type BrowserLivePanelProps } from '../components/BrowserLivePanel';
import { browserLiveStore } from '../stores/browserLive';
import { sendMessage } from '../api/chat';
import { sessionStore } from '../stores/session';
import { desktop as browser, type BrowserCommand, type BrowserState } from './bridge';
import BrowserToolbar from './BrowserToolbar';

export default function DesktopBrowserPanel(props: BrowserLivePanelProps) {
  const [desktopBrowser, setDesktopBrowser] = createSignal(false);
  const [sidePanelOpen, setSidePanelOpen] = createSignal(false);
  const [question, setQuestion] = createSignal('');
  const [manualSessions, setManualSessions] = createSignal<string[]>([]);
  const manualOpen = () => manualSessions().length > 0;
  const [selectedBrowser, setSelectedBrowser] = createSignal('');
  const [takenOverSession, setTakenOverSession] = createSignal('');
  const manualSession = () => manualSessions().includes(selectedBrowser()) ? selectedBrowser() : manualSessions()[manualSessions().length - 1] || '';
  const notifyPanel = (open: boolean, session: string) => window.dispatchEvent(new CustomEvent('tcode:browser-panel', { detail: {open, session} }));
  const manualItem = () => ({ key: 'manual', parentSessionId: props.parentSessionId, browserSessionId: manualSession(), workspaceId: props.workspaceId, agentSessionId: '', status: 'ready', action: '', title: '', url: '', frameSeq: 0, hidden: true, error: '', updatedAt: 0 });
  const [browserState, setBrowserState] = createSignal<BrowserState>({ url: '', title: '', loading: false, canGoBack: false, canGoForward: false, zoom: 1 });
  const [nativeHost, setNativeHost] = createSignal<HTMLDivElement>();
  const task = createBrowserTask(props, item => takenOverSession() === item.browserSessionId
    || ['waiting_login', 'login_interrupted'].includes(item.status));
  const active = createMemo(() => task() || (manualOpen() ? manualItem() : undefined));
  const displayingManual = createMemo(() => manualOpen() && (manualSessions().includes(selectedBrowser()) || !takenOverSession()));
  const panelItem = createMemo(() => displayingManual() ? manualItem() : active());
  const panelHuman = createMemo(() => displayingManual() || (!!takenOverSession() && controlMode() === 'human'));
  const nativePanel = createMemo(() => !!browser?.browserPanel && (displayingManual() || desktopBrowser()));
  const effectiveWorkspaceId = createMemo(() => active()?.workspaceId || props.workspaceId);
  const nativeSession = createMemo(() => panelItem()?.browserSessionId || '');
  const waitingLogin = () => active()?.status === 'waiting_login';
  const observation = createBrowserObservation(active, effectiveWorkspaceId, {
    onConnect: () => setSidePanelOpen(manualOpen()),
    onState: (state, previousStatus, status) => {
      const native = state.desktop_browser === true;
      setDesktopBrowser(native);
      if (previousStatus === 'waiting_login' && status !== 'waiting_login') {
        window.dispatchEvent(new Event('tcode:restore-browser-view'));
      }
      // Python's native frame metadata cannot observe Electron's control mode.
      return native;
    },
  });
  const { controlMode, setControlMode, connectionError, setConnectionError, frameUrl, replaceFrame, sendControl } = observation;
  const [loginBusy, setLoginBusy] = createSignal(false);
  const [loginDeferred, setLoginDeferred] = createSignal(false);
  const [interrupted, setInterrupted] = createSignal<{site: string}>();
  const [restartingRead, setRestartingRead] = createSignal(false);
  const [recoveryNotice, setRecoveryNotice] = createSignal('');
  createEffect(() => {
    const parent = props.parentSessionId;
    const workspace = props.workspaceId;
    setInterrupted(undefined);
    setRecoveryNotice('');
    if (!parent || !workspace) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const restore = async () => {
      if (timer !== undefined) window.clearTimeout(timer);
      try {
        const query = new URLSearchParams({session_id: parent, workspace_id: workspace});
        const response = await fetch(`/api/browser/pending-login?${query}`, { signal: controller.signal, cache: 'no-store' });
        if (!response.ok) return;
        const data = await response.json();
        if (controller.signal.aborted) return;
        if (data.pending) browserLiveStore.upsertFromAgentEvent(data.pending, parent);
        setInterrupted(data.interrupted || undefined);
        if ((data.resuming || restartingRead()) && !data.pending) timer = window.setTimeout(restore, 800);
      } catch {
        // The live socket and the next online event can restore this prompt.
      }
    };
    void restore();
    window.addEventListener('online', restore);
    window.addEventListener('tcode:restore-browser-view', restore);
    onCleanup(() => {
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
      window.removeEventListener('online', restore);
      window.removeEventListener('tcode:restore-browser-view', restore);
    });
  });
  async function resumeRead() {
    if (!interrupted() || props.running || restartingRead()) return;
    const parent = props.parentSessionId;
    const query = new URLSearchParams({session_id: parent, workspace_id: props.workspaceId});
    setRestartingRead(true);
    setRecoveryNotice('正在重新打开原读取；需要时会请你登录。');
    window.dispatchEvent(new Event('tcode:restore-browser-view'));
    try {
      const response = await fetch(`/api/browser/resume-read?${query}`, {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}',
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '未能恢复读取。');
      if (parent !== props.parentSessionId || sessionStore.currentSessionId() !== parent) return;
      setInterrupted(undefined);
      setRecoveryNotice('原读取已验证，正在使用该结果继续任务。');
      await sendMessage(`继续异步任务 ${data.identifier}。`, {async_continuation_id:data.identifier});
      setRecoveryNotice('');
    } catch (error) {
      if (parent === props.parentSessionId) setRecoveryNotice(error instanceof Error ? error.message : '未能恢复读取。');
    } finally {
      setRestartingRead(false);
      window.dispatchEvent(new Event('tcode:restore-browser-view'));
    }
  }
  const loginDecision = async (decision: 'takeover' | 'cancel_login') => {
    const item = active();
    if (!item || !waitingLogin() || !browser?.browserControl || loginBusy()) return;
    setLoginBusy(true);
    try {
      if (decision === 'takeover') await takeControl();
      else await browser.browserControl(item.browserSessionId, decision);
    } catch {
      setConnectionError('登录请求已失效或操作未送达');
    } finally { setLoginBusy(false); }
  };
  createEffect(() => {
    nativeSession();
    setBrowserState({ url: '', title: '', loading: false, canGoBack: false, canGoForward: false, zoom: 1 });
  });
  let previousParent = props.parentSessionId;
  createEffect(() => {
    const parent = props.parentSessionId;
    if (parent === previousParent) return;
    previousParent = parent;
    const task = untrack(takenOverSession);
    for (const session of untrack(manualSessions)) {
      void browser?.browserPanel?.(session, {action:'close'}).catch(() => {});
      notifyPanel(false, session);
    }
    const previous = untrack(() => browserLiveStore.sessions().find(item => item.browserSessionId === task));
    if (task) {
      if (previous?.status === 'waiting_login') void browser?.browserPanel?.(task, {action: 'detach'}).catch(() => {});
      else void browser?.browserControl?.(task, 'release').catch(() => {});
    }
    batch(() => { setManualSessions([]); setSelectedBrowser(''); setTakenOverSession(''); setControlMode('agent'); setSidePanelOpen(false); });
  });
  onCleanup(() => {
    for (const session of manualSessions()) void browser?.browserPanel?.(session, {action:'close'}).catch(() => {});
    if (takenOverSession()) {
      if (waitingLogin()) void browser?.browserPanel?.(takenOverSession(), {action: 'detach'}).catch(() => {});
      else void browser?.browserControl?.(takenOverSession(), 'release').catch(() => {});
    }
  });
  const takeControl = async () => {
    const item = active();
    if (item && takenOverSession() === item.browserSessionId && controlMode() === 'human') {
      setSidePanelOpen(true);
      setSelectedBrowser(item.browserSessionId); notifyPanel(true, item.browserSessionId);
      return;
    }
    try {
      setConnectionError('');
      if (item && browser?.browserControl) {
        const result = await browser.browserControl(item.browserSessionId, 'takeover');
        batch(() => { setDesktopBrowser(true); setTakenOverSession(item.browserSessionId); setControlMode(result.control_mode); setSidePanelOpen(true); });
        browserLiveStore.setHidden(item.key, true);
        setSelectedBrowser(item.browserSessionId); notifyPanel(true, item.browserSessionId);
        return;
      }
      sendControl({ action: 'set_control', mode: 'human' });
      setSidePanelOpen(true);
    } catch {
      setConnectionError('浏览器正在完成当前操作，请稍后重试接管');
    }
  };

  const releaseControl = async (session?: string) => {
    const item = session ? active() : panelItem();
    if (!session && displayingManual()) { await closeManual(manualSession()); return; }
    try {
      if (nativePanel() && item && browser?.browserControl) {
        const result = await browser.browserControl(item.browserSessionId, !session && waitingLogin() ? 'return_login' : 'release');
        setControlMode(result.control_mode);
      } else {
        sendControl({ action: 'set_control', mode: 'agent' });
        setControlMode('agent');
      }
      batch(() => {
        setTakenOverSession(''); setSelectedBrowser(manualSession()); setControlMode('agent'); setSidePanelOpen(manualOpen());
      });
      if (item) notifyPanel(false, item.browserSessionId);
    } catch {
      setConnectionError('暂时无法交还控制，请稍后重试');
    }
  };
  const closeManual = async (session: string) => {
    if (!manualSessions().includes(session)) return;
    setManualSessions(previous => previous.filter(item => item !== session));
    if (selectedBrowser() === session) setSelectedBrowser(manualSession() || takenOverSession());
    setSidePanelOpen(manualOpen() || !!takenOverSession());
    await browser?.browserPanel?.(session, {action:'close'}).catch(() => {});
    notifyPanel(false, session);
  };

  const panelCommand = async (command: BrowserCommand) => {
    const item = panelItem();
    if (!item || !browser?.browserPanel) return;
    if (['navigate', 'back', 'forward', 'reload', 'clear-data'].includes(command.action)) {
      setBrowserState(previous => ({ ...previous, loading: true, error: '' }));
    }
    try {
      const state = await browser.browserPanel(item.browserSessionId, command);
      if (nativeSession() !== item.browserSessionId) return;
      const menuAction = (state as BrowserState & { menuAction?: string }).menuAction;
      if (menuAction) window.dispatchEvent(new CustomEvent('tcode:browser-menu-action', { detail: menuAction }));
      setBrowserState(previous => JSON.stringify(previous) === JSON.stringify(state) ? previous : state);
      window.dispatchEvent(new CustomEvent('tcode:browser-title', { detail: {session:item.browserSessionId, title:state.title || state.url || '浏览器'} }));
      setConnectionError(state.error ? '页面没有打开，请检查链接或网络后重试' : '');
    } catch { if (nativeSession() === item.browserSessionId) setConnectionError('无法打开该页面，请检查网址或连接后重试'); }
  };

  createEffect(() => {
    const openBrowser = async (url = '') => {
      const browserPanel = browser?.browserPanel;
      if (!browserPanel) return;
      try {
        const session = `manual-browser-${crypto.randomUUID()}`;
        await browser?.browserControl?.(session, 'takeover');
        batch(() => { setManualSessions(previous => [...previous, session]); setSelectedBrowser(session); setDesktopBrowser(true); setSidePanelOpen(true); });
        notifyPanel(true, session);
        if (url) {
          setBrowserState(previous => ({ ...previous, loading: true, error: '' }));
          void browserPanel(session, { action: 'navigate', url }).catch(() => setConnectionError('无法打开新页面，请检查链接或连接后重试'));
        }
      } catch { setConnectionError('暂时无法打开手动浏览器，请重试'); }
    };
    const openBrowserEvent = () => { void openBrowser(); };
    window.addEventListener('tcode:open-browser', openBrowserEvent);
    const stopNewTabListener = browser?.onBrowserNewTab?.(url => { void openBrowser(url); });
    const closeBrowser = (event: Event) => {
      const session = (event as CustomEvent<string>).detail;
      if (session && manualSessions().includes(session)) void closeManual(session);
      else if (!session || session === takenOverSession()) void releaseControl(session || takenOverSession() || undefined);
    };
    const selectBrowser = (event: Event) => {
      const session = (event as CustomEvent<string>).detail;
      if (manualSessions().includes(session) || session === takenOverSession()) setSelectedBrowser(session);
    };
    window.addEventListener('tcode:select-browser', selectBrowser);
    onCleanup(() => window.removeEventListener('tcode:select-browser', selectBrowser));
    window.addEventListener('tcode:close-browser', closeBrowser);
    onCleanup(() => window.removeEventListener('tcode:close-browser', closeBrowser));
    onCleanup(() => { window.removeEventListener('tcode:open-browser', openBrowserEvent); stopNewTabListener?.(); });
  });

  createEffect(() => {
    const session = takenOverSession();
    const item = active();
    if (!session || (item?.key !== 'manual' && item?.browserSessionId === session)) return;
    void browser?.browserControl?.(session, 'release').catch(() => {});
    batch(() => { setTakenOverSession(''); setSelectedBrowser(manualSession()); setControlMode('agent'); setSidePanelOpen(manualOpen()); });
    notifyPanel(false, session);
  });

  const sendHumanInput = (input: { action: 'click' | 'scroll' | 'key' | 'text'; x?: number; y?: number; deltaX?: number; deltaY?: number; key?: string; text?: string }) => {
    const item = active();
    if (!item || controlMode() !== 'human') return;
    if (desktopBrowser() && browser?.browserInput) {
      void browser.browserInput(item.browserSessionId, input).then(async result => {
        if (result.screenshot) replaceFrame(await (await fetch(`data:image/jpeg;base64,${result.screenshot}`)).blob());
      }).catch(() => setConnectionError('浏览器操作未送达，请重试'));
      return;
    }
    const action = input.action === 'click' ? 'click_at' : input.action === 'key' ? 'key_down' : input.action;
    const { action: _inputAction, ...details } = input;
    sendControl({ ...details, action, delta_x: input.deltaX, delta_y: input.deltaY });
  };

  createEffect(() => {
    const session = nativeSession();
    const host = nativeHost();
    if (!session || !host || !sidePanelOpen() || !panelHuman() || !nativePanel() || !browser?.browserPanel) return;
    const resize = () => {
      const rect = host.getBoundingClientRect();
      if (!host.isConnected || !host.getClientRects().length || rect.width < 1 || rect.height < 1) {
        void browser?.browserPanel?.(session, { action: 'detach' }).catch(() => {});
        return;
      }
      void panelCommand({ action: 'attach', bounds: { x: rect.x, y: rect.y, width: rect.width, height: rect.height } });
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    const visibilityObserver = new MutationObserver(resize);
    for (let ancestor: HTMLElement | null = host; ancestor; ancestor = ancestor.parentElement) {
      visibilityObserver.observe(ancestor, { attributes: true, attributeFilter: ['hidden', 'style', 'class', 'data-focus'] });
    }
    untrack(resize);
    window.addEventListener('resize', resize);
    let polling = false;
    const stateTimer = window.setInterval(() => {
      if (polling || document.hidden) return;
      polling = true;
      void panelCommand({ action: 'state' }).finally(() => { polling = false; });
    }, 250);
    const boundsTimer = window.setInterval(resize, 1000);
    onCleanup(() => {
      observer.disconnect(); visibilityObserver.disconnect(); window.clearInterval(stateTimer); window.clearInterval(boundsTimer);
      window.removeEventListener('resize', resize);
      void browser?.browserPanel?.(session, { action: 'detach' }).catch(() => {});
    });
  });

  const framePoint = (event: MouseEvent | PointerEvent) => {
    const image = event.currentTarget as HTMLImageElement;
    const rect = image.getBoundingClientRect();
    const scale = Math.min(rect.width / image.naturalWidth, rect.height / image.naturalHeight);
    const width = image.naturalWidth * scale;
    const height = image.naturalHeight * scale;
    return {
      x: Math.max(0, Math.min(image.naturalWidth, (event.clientX - rect.left - (rect.width - width) / 2) / scale)),
      y: Math.max(0, Math.min(image.naturalHeight, (event.clientY - rect.top - (rect.height - height) / 2) / scale)),
    };
  };

  return (
    <>
    <Show when={interrupted() && !waitingLogin()}>
      <section aria-label="恢复浏览器读取" class="rounded-lg border border-current/20 p-3">
        <p>服务重启前暂停了 {interrupted()?.site || '网站'} 的读取。可以从该读取步骤恢复，不会重放点击或输入。</p>
        <button type="button" disabled={props.running || restartingRead()} onClick={() => void resumeRead()}>
          {restartingRead() ? '正在恢复…' : '恢复原读取'}
        </button>
      </section>
    </Show>
    <Show when={recoveryNotice()}><p role="status">{recoveryNotice()}</p></Show>
    <Show when={active()}>
      {(itemAccessor) => {
        const item = () => itemAccessor();
        return (
          <>
          <Show when={waitingLogin() && !takenOverSession()}>
            <section aria-label="浏览器登录确认" class="rounded-lg border border-current/20 p-3">
              <p role="status">需要登录 {item().title || '目标网站'}，当前读取已暂停。</p>
              <Show when={!loginDeferred()} fallback={<button type="button" onClick={() => setLoginDeferred(false)}>处理登录请求</button>}>
                <p>请在浏览器内本人登录，交还后会验证原页面能否访问。请勿在聊天中发送凭据。</p>
                <Show when={browser?.browserControl} fallback={<p>请前往 TCode 桌面端处理登录请求。</p>}>
                  <button type="button" disabled={loginBusy()} onClick={() => void loginDecision('takeover')}>接管或继续登录</button>{' · '}
                  <button type="button" disabled={loginBusy()} onClick={() => setLoginDeferred(true)}>稍后处理</button>{' · '}
                  <button type="button" disabled={loginBusy()} onClick={() => void loginDecision('cancel_login')}>取消本次读取</button>
                </Show>
              </Show>
              <Show when={connectionError()}><p role="alert">{connectionError()}</p></Show>
            </section>
          </Show>
          <Show when={item().status === 'login_interrupted' && !interrupted()}>
            <p role="status">原浏览器登录请求已结束，未继续读取。请重新发起读取，需要时会重新请你登录。</p>
          </Show>
          <Show when={item().key !== 'manual'}>
            <BrowserObservationView item={item()} observation={observation} nativeFrame={desktopBrowser()}
              onTakeControl={() => void takeControl()}/>
          </Show>
          <Show when={browser?.browserPanel && sidePanelOpen() && panelHuman()}>
            <Portal mount={document.getElementById('studio-browser-host')!}>
            <section class="desktop-browser-panel" aria-label="浏览器接管侧边栏">
              <header class="flex items-center justify-between gap-3 border-b border-[#26313a] px-4 py-3">
                <div class="min-w-0">
                  <div class="text-xs font-semibold">{displayingManual() ? '手动浏览' : '你正在接管，Agent 等待交还控制'}</div>
                  <div class="mt-0.5 truncate text-[11px]" title={browserState().url || item().url}>{browserState().title || item().title || item().url}</div>
                </div>
                <button class="shrink-0 rounded-lg border border-[#f1a02b]/60 bg-[#3a2910] px-3 py-1.5 text-xs font-semibold text-[#ffd17a]" onClick={() => void releaseControl()}>
                  {displayingManual() ? '关闭浏览器' : '交还给 Agent'}
                </button>
              </header>
              <BrowserToolbar state={browserState()} command={panelCommand} />
              <Show when={connectionError()}><div class="desktop-browser-error" role="alert">{connectionError()}</div></Show>
              <div class="relative min-h-0 flex-1 bg-white">
                <Show when={nativePanel()} fallback={
                <Show when={frameUrl()} fallback={<div class="flex h-full items-center justify-center text-sm text-[#7f8b93]">正在等待浏览器画面…</div>}>
                  <img
                    src={frameUrl()}
                    alt="可操作的浏览器页面"
                    class="h-full w-full cursor-pointer object-contain outline-none"
                    tabindex="0"
                    onClick={(event) => sendHumanInput({ action: 'click', ...framePoint(event) })}
                    onWheel={(event) => { event.preventDefault(); sendHumanInput({ action: 'scroll', deltaX: event.deltaX, deltaY: event.deltaY }); }}
                    onKeyDown={(event) => sendHumanInput({ action: 'key', key: event.key })}
                  />
                </Show>
                }><div ref={setNativeHost} class="desktop-browser-native-host h-full w-full" aria-label="内置浏览器页面" /></Show>
              </div>
              <Show when={!nativePanel()}>
              <form class="border-t border-[#26313a] bg-[#10171c] p-3" onSubmit={(event) => {
                event.preventDefault();
                const value = question().trim();
                if (!value) return;
                sendHumanInput({ action: 'text', text: value });
                sendHumanInput({ action: 'key', key: 'Enter' });
                setQuestion('');
              }}>
                <div class="flex items-center gap-2 rounded-xl border border-[#35434c] bg-[#0b1115] p-2 focus-within:border-[#5ba8d8]">
                  <input class="min-w-0 flex-1 bg-transparent px-2 text-sm text-[#e6edf3] outline-none" value={question()} onInput={(event) => setQuestion(event.currentTarget.value)} placeholder="向当前网页输入内容…" />
                  <button class="rounded-lg bg-[#d9edf8] px-3 py-1.5 text-xs font-semibold text-[#10202a]" type="submit">发送</button>
                </div>
                <div class="mt-2 text-[10px] text-[#71808a]">可直接点击、滚动页面；完成后请交还给 Agent 继续执行。</div>
              </form>
              </Show>
            </section>
            </Portal>
          </Show>
          </>
        );
      }}
    </Show>
    </>
  );
}
