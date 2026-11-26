import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRoot, createSignal } from 'solid-js';
import type { AttachedFile } from '../../stores/composer';
import { createFileMentions } from './createFileMentions';
import { createGuidanceQueue } from './createGuidanceQueue';

// Effects and onMount must run as in the browser, even under the node test runner.
vi.mock('solid-js', () => vi.importActual('solid-js/dist/solid.js'));
const api = vi.hoisted(() => ({
  searchWorkspaceFiles: vi.fn(), getChatGuidanceQueue: vi.fn(), deleteChatGuidanceItem: vi.fn(),
}));
const store = vi.hoisted(() => ({
  currentSessionId: () => 'session-1' as string | null,
  isRunning: (): boolean => false,
  setIsRunning: vi.fn(), setStatus: vi.fn(),
}));
vi.mock('../../api/client', () => api);
vi.mock('../../stores/session', () => ({ sessionStore: store }));

const file = (path: string) => ({
  name: path, path, type: 'file' as const, size: 10, modified_at: 0, origin: 'uploaded' as const,
});
const disposers: Array<() => void> = [];

function mentions() {
  return createRoot(dispose => {
    disposers.push(dispose);
    const [input, setInput] = createSignal('@report');
    const [attachedFiles, setAttachedFiles] = createSignal<AttachedFile[]>([]);
    const controls = createFileMentions({
      input, setInput, attachedFiles, setAttachedFiles,
      workspaceId: () => 'workspace-1', textarea: () => undefined,
      autoSizeTextarea: vi.fn(), closeCommandMenu: vi.fn(),
    });
    return { ...controls, input, attachedFiles, dispose };
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  store.currentSessionId = () => 'session-1';
  store.isRunning = () => false;
  vi.stubGlobal('window', { setInterval, clearInterval });
});
afterEach(() => {
  disposers.splice(0).forEach(dispose => dispose());
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('composer file mentions', () => {
  it('debounces search and ignores a response after the menu is closed', async () => {
    let finish!: (value: ReturnType<typeof file>[]) => void;
    api.searchWorkspaceFiles.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const menu = mentions();
    menu.refreshFileMentionMenu('@r');
    menu.refreshFileMentionMenu('@report');
    await vi.advanceTimersByTimeAsync(140);
    expect(api.searchWorkspaceFiles).toHaveBeenCalledExactlyOnceWith('report', 'workspace-1', 40, expect.any(Function));
    menu.closeFileMentionMenu();
    finish([file('report.txt')]);
    await Promise.resolve();
    expect(menu.fileMentionItems()).toEqual([]);
    expect(menu.isFileMentionMenuOpen()).toBe(false);
    expect(menu.fileMentionLoading()).toBe(false);
  });

  it('attaches a selected file once and removes the active token', () => {
    const menu = mentions();
    menu.applyFileMention(file('report.txt'));
    expect(menu.input()).toBe('');
    expect(menu.attachedFiles()).toEqual([
      expect.objectContaining({ name: 'report.txt', path: 'report.txt', source: 'mention' }),
    ]);
    menu.applyFileMention(file('report.txt'));
    expect(menu.attachedFiles()).toHaveLength(1);
  });

  it('cancels a pending search when the composer is disposed', async () => {
    const menu = mentions();
    menu.refreshFileMentionMenu('@report');
    menu.dispose();
    await vi.advanceTimersByTimeAsync(200);
    expect(api.searchWorkspaceFiles).not.toHaveBeenCalled();
  });
});

describe('composer guidance queue', () => {
  it('counts every pending item without hiding the latest unapplied notice', async () => {
    const items = Array.from({ length: 501 }, (_, i) => ({
      id: `pending-${i + 1}`, message: 'queued fixture', mode: 'queue', status: 'pending',
    }));
    api.getChatGuidanceQueue.mockResolvedValue({
      session_id: 'session-1',
      items: [...items, { id: 'latest-notice', message: 'fixture', status: 'failed', failure_code: 'expired_turn_unapplied' }],
    });
    const queue = createRoot(dispose => {
      disposers.push(dispose);
      return createGuidanceQueue();
    });
    await Promise.resolve();
    expect(queue.activeGuidanceItems()).toHaveLength(501);
    expect(queue.activeGuidanceItems()[0]?.id).toBe('pending-1');
    expect(queue.latestActiveGuidance()?.id).toBe('pending-501');
    expect(queue.latestUnappliedGuidance()?.id).toBe('latest-notice');
  });

  it('remembers exactly the dismissed guidance after the page is recreated', async () => {
    const saved = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => saved.get(key) ?? null,
      setItem: (key: string, value: string) => saved.set(key, value),
    });
    const unapplied = (id: string) => ({
      id, message: 'synthetic guidance text', status: 'failed', failure_code: 'expired_turn_unapplied',
    });
    const response = { session_id: 'session-1', items: [unapplied('guidance-a')] };
    api.getChatGuidanceQueue.mockResolvedValue(response);
    const first = createRoot(dispose => ({ ...createGuidanceQueue(), dispose }));
    disposers.push(first.dispose);
    await Promise.resolve();
    expect(first.latestUnappliedGuidance()?.id).toBe('guidance-a');
    first.dismissUnappliedGuidance('guidance-a');
    expect(first.latestUnappliedGuidance()).toBeUndefined();
    first.dispose();
    expect([...saved.values()]).toEqual(['["guidance-a"]']);
    const afterReload = createRoot(dispose => {
      disposers.push(dispose);
      return createGuidanceQueue();
    });
    await Promise.resolve();
    expect(afterReload.latestUnappliedGuidance()).toBeUndefined();
    response.items = [unapplied('guidance-a'), unapplied('guidance-b')];
    await afterReload.refreshGuidanceQueue();
    expect(afterReload.latestUnappliedGuidance()?.id).toBe('guidance-b');
  });

  it('refreshes once per session change and discards the previous session response', async () => {
    let finishFirst!: (value: unknown) => void;
    api.getChatGuidanceQueue.mockImplementation((id: string) => id === 'session-1'
      ? new Promise(resolve => { finishFirst = resolve; })
      : Promise.resolve({ session_id: id, items: [{ id: 'new', message: '新任务', status: 'pending' }] }));
    const [sessionId, setSessionId] = createSignal<string | null>('session-1');
    store.currentSessionId = sessionId;
    const queue = createRoot(dispose => {
      disposers.push(dispose);
      return createGuidanceQueue();
    });
    expect(api.getChatGuidanceQueue).toHaveBeenCalledTimes(1);
    setSessionId('session-2');
    await Promise.resolve();
    expect(api.getChatGuidanceQueue).toHaveBeenCalledTimes(2);
    finishFirst({ session_id: 'session-1', items: [{ id: 'old', message: '旧任务', status: 'pending' }] });
    await Promise.resolve();
    expect(queue.latestActiveGuidance()?.id).toBe('new');
    setSessionId(null);
    expect(queue.activeGuidanceItems()).toEqual([]);
  });

  it('polls only active queues and stops on disposal', async () => {
    store.isRunning = () => true;
    api.getChatGuidanceQueue.mockResolvedValue({ session_id: 'session-1', items: [] });
    const dispose = createRoot(dispose => {
      disposers.push(dispose);
      createGuidanceQueue();
      return dispose;
    });
    await vi.advanceTimersByTimeAsync(2500);
    expect(api.getChatGuidanceQueue).toHaveBeenCalledTimes(2);
    dispose();
    await vi.advanceTimersByTimeAsync(5000);
    expect(api.getChatGuidanceQueue).toHaveBeenCalledTimes(2);
  });
});
