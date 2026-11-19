import { createMemo, createSignal, For, Show } from 'solid-js';
import { sessionStore } from '../stores/session';
import type { ToolCall } from '../stores/session';

type Entry = { path: string; kind: 'file' | 'directory'; artifact_id?: string; size?: number; importable?: boolean; browsable?: boolean };
export type ArtifactDirectoryPage = { service_id: string; service_name?: string; directory: string; entries: Entry[]; next_offset: number | null };

export function parseDirectoryPage(output: string): ArtifactDirectoryPage | undefined {
  try {
    const data = JSON.parse(output);
    if (!data || typeof data.service_id !== 'string' || !data.service_id || typeof data.directory !== 'string'
      || (data.service_name !== undefined && typeof data.service_name !== 'string')
      || !Array.isArray(data.entries) || data.entries.length > 100
      || (data.next_offset !== null && (!Number.isSafeInteger(data.next_offset) || data.next_offset <= 0))) return;
    if (!data.entries.every((entry: Entry) => entry && typeof entry.path === 'string'
      && (entry.kind === 'file' || entry.kind === 'directory'))) return;
    return data;
  } catch { return; }
}

export function directoryFromTool(tool: ToolCall): ArtifactDirectoryPage | undefined {
  return tool.tool_name === 'artifact_browse' && !tool.error && tool.output ? parseDirectoryPage(tool.output) : undefined;
}

export function withoutDirectoryEcho(text: string, pages: ArtifactDirectoryPage[]) {
  if (!pages.length) return text;
  const start = text.indexOf('{'), end = text.lastIndexOf('}');
  if (start < 0 || end < start) return text;
  const echoed = parseDirectoryPage(text.slice(start, end + 1));
  if (!echoed || !pages.some(page => page.service_id === echoed.service_id && page.directory === echoed.directory
    && page.entries.length === echoed.entries.length
    && page.entries.every((entry, index) => entry.path === echoed.entries[index].path && entry.kind === echoed.entries[index].kind))) return text;
  return [text.slice(0, start).replace(/```(?:json)?\s*$/i, '').trim(), text.slice(end + 1).replace(/^\s*```/, '').trim()]
    .filter(Boolean).join('\n\n');
}

const button = 'cursor-pointer rounded-md px-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--tcode-primary)] enabled:hover:bg-[var(--tcode-surface-hover,var(--tcode-surface-muted))] disabled:cursor-default disabled:opacity-50';
const fileName = (path: string) => path.slice(path.lastIndexOf('/') + 1) || path;
const formatSize = (size: number) => size < 1024 ? `${size} B` : size < 1024 ** 2 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1024 ** 2).toFixed(1)} MB`;

const iconPaths = {
  server: 'M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01',
  directory: 'M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
  file: 'M6 3h8l4 4v14H6zM14 3v5h4M9 13h6M9 17h4',
  download: 'M12 3v12m-4-4 4 4 4-4M5 16v4h14v-4',
  chevron: 'm9 6 6 6-6 6',
};

function FileIcon(props: { kind: keyof typeof iconPaths }) {
  return <svg aria-hidden="true" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" class="shrink-0"><path d={iconPaths[props.kind]} /></svg>;
}

export default function ArtifactDirectoryCard(props: { page: ArtifactDirectoryPage; sessionId: string }) {
  const [current, setCurrent] = createSignal<ArtifactDirectoryPage>();
  const page = () => current() || props.page;
  const [busy, setBusy] = createSignal('');
  const [error, setError] = createSignal('');
  const [downloaded, setDownloaded] = createSignal('');
  let breadcrumb: HTMLElement | undefined;
  const crumbs = createMemo(() => page().directory ? page().directory.split('/') : []);
  const enabled = () => Boolean(props.sessionId) && props.sessionId === sessionStore.currentSessionId() && !busy();

  async function action(kind: 'browse' | 'import', args: Record<string, string | number>, append = false) {
    if (!enabled()) return;
    const before = page(), sessionId = props.sessionId;
    setBusy(kind); setError(''); setDownloaded('');
    try {
      const response = await fetch(`/api/artifact-services/chat/${kind}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...args, service_id: before.service_id, session_id: sessionId }),
      });
      const text = await response.text();
      if (!response.ok) {
        let detail: unknown;
        try { detail = JSON.parse(text).detail; } catch { /* no structured error */ }
        throw new Error(typeof detail === 'string' ? detail : '文件服务操作失败，请重试');
      }
      if (kind === 'import') {
        const path = JSON.parse(text).path;
        if (typeof path !== 'string' || !path) throw new Error('服务器未返回下载路径');
        setDownloaded(path);
      } else {
        const next = parseDirectoryPage(text);
        if (!next || next.service_id !== before.service_id || next.directory !== args.directory) throw new Error('文件服务未返回有效目录');
        setCurrent(append ? { ...next, entries: [...before.entries, ...next.entries] } : next);
        if (!append || next.next_offset === null) breadcrumb?.focus();
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '文件服务操作失败，请重试');
    } finally { setBusy(''); }
  }

  const browse = (directory: string, offset = 0) => action('browse', { directory, offset, limit: 20 }, offset > 0);
  return <section aria-label="远端文件目录" aria-busy={Boolean(busy())} class="mt-2 min-w-0 rounded-xl border border-[var(--tcode-border)] bg-[var(--tcode-surface)] text-sm text-[var(--tcode-text)]">
    <header class="rounded-t-xl border-b border-[var(--tcode-border)] bg-[var(--tcode-surface-soft)] px-3 pb-1 pt-3">
      <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div class="flex min-w-0 items-center gap-2.5 font-semibold">
          <span class="grid size-8 shrink-0 place-items-center rounded-lg border border-[var(--tcode-border)] bg-[var(--tcode-surface)] text-[var(--tcode-primary)]"><FileIcon kind="server" /></span>
          <span class="[overflow-wrap:anywhere]">{page().service_name || '远端文件服务'}</span>
        </div>
        <span class="shrink-0 text-xs tabular-nums text-[var(--tcode-text-muted)]">已显示 {page().entries.length} 项</span>
      </div>
      <nav ref={breadcrumb} tabindex={-1} aria-label="远端文件路径" class="mt-1 flex flex-wrap items-center gap-x-1 rounded-md text-xs text-[var(--tcode-text-muted)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--tcode-primary)] [overflow-wrap:anywhere]">
        <Show when={crumbs().length} fallback={<span class="px-2 py-2 font-medium text-[var(--tcode-text)]" aria-current="location">根目录</span>}>
          <button type="button" class={`${button} min-h-8`} disabled={!enabled()} onClick={() => void browse('')}>根目录</button>
          <For each={crumbs()}>{(crumb, i) => <><span aria-hidden="true">/</span><Show when={i() < crumbs().length - 1} fallback={<span class="px-2 py-1 font-medium text-[var(--tcode-text)]" aria-current="location">{crumb}</span>}>
            <button type="button" class={`${button} min-h-8 max-w-full text-left`} disabled={!enabled()} onClick={() => void browse(crumbs().slice(0, i() + 1).join('/'))}>{crumb}</button>
          </Show></>}</For>
        </Show>
      </nav>
    </header>
    <Show when={page().entries.length} fallback={<p class="px-4 py-5 text-[var(--tcode-text-muted)]">该目录没有文件或子目录。</p>}>
      <ul class="divide-y divide-[var(--tcode-border)] px-2">
        <For each={page().entries}>{entry => <li class="min-w-0 py-0.5">
          <Show when={entry.kind === 'directory' && entry.browsable !== false} fallback={
            <div class="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 px-2">
              <div class="flex min-w-0 flex-[1_1_10rem] items-center gap-2.5 font-medium [overflow-wrap:anywhere]">
                <span class="shrink-0 text-[var(--tcode-text-muted)]"><FileIcon kind={entry.kind} /></span>
                <span class="sr-only">{entry.kind === 'directory' ? '目录：' : '文件：'}</span>{fileName(entry.path)}
              </div>
              <Show when={entry.kind === 'file' && typeof entry.size === 'number'}><span class="text-xs tabular-nums text-[var(--tcode-text-muted)]">{formatSize(entry.size!)}</span></Show>
              <Show when={entry.kind === 'file' && entry.importable !== false && entry.artifact_id}>
                <button type="button" class={`${button} flex min-h-9 items-center gap-1.5 whitespace-nowrap text-xs font-medium text-[var(--tcode-primary)]`} disabled={!enabled()} aria-label={`下载 ${fileName(entry.path)} 到工作区`} onClick={() => void action('import', { path: entry.path, artifact_id: entry.artifact_id! })}><FileIcon kind="download" />下载到工作区</button>
              </Show>
              <Show when={entry.importable === false || entry.browsable === false}><small class="text-xs text-[var(--tcode-text-muted)]">旧文件名，不支持按路径操作</small></Show>
            </div>
          }>
            <button type="button" class={`${button} flex min-h-11 w-full min-w-0 items-center gap-2.5 py-1 text-left font-medium`} disabled={!enabled()} onClick={() => void browse(entry.path)} aria-label={`打开目录 ${fileName(entry.path)}`}>
              <span class="shrink-0 text-[var(--tcode-accent)]"><FileIcon kind="directory" /></span>
              <span class="min-w-0 flex-1 [overflow-wrap:anywhere]">{fileName(entry.path)}</span>
              <span class="shrink-0 text-[var(--tcode-text-muted)]"><FileIcon kind="chevron" /></span>
            </button>
          </Show>
        </li>}</For>
      </ul>
    </Show>
    <Show when={page().next_offset !== null || busy() || error() || downloaded()}>
      <footer class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-t border-[var(--tcode-border)] px-3 py-2 text-xs">
        <Show when={busy()}><div role="status" class="text-[var(--tcode-text-muted)]">{busy() === 'browse' ? '正在读取目录…' : '正在下载到工作区…'}</div></Show>
        <Show when={error()}><div role="alert" class="text-[var(--tcode-danger)]">{error()}</div></Show>
        <Show when={downloaded()}><div role="status" class="min-w-0 text-[var(--tcode-success)] [overflow-wrap:anywhere]">已下载到 {downloaded()}</div></Show>
        <Show when={page().next_offset}>{offset => <button type="button" class={`${button} ml-auto min-h-8 font-medium text-[var(--tcode-primary)]`} disabled={!enabled()} onClick={() => void browse(page().directory, offset())}>加载更多</button>}</Show>
      </footer>
    </Show>
  </section>;
}
