import { For } from 'solid-js';

export const LazyPanelFallback = (props: { label?: string }) => (
  <div
    class="tcode-panel-skeleton"
    role="status"
  >
    <div class="tcode-skeleton-layout" aria-hidden="true"><span/><span/><span/><span/></div>
    <p>{props.label || '正在加载工作区...'}</p>
  </div>
);

export const AppLoading = (props: { label: string }) => (
  <div class="workspace-skeleton" role="status" aria-live="polite" aria-busy="true">
    <p class="workspace-skeleton-status">{props.label}</p>
    <div class="workspace-skeleton-top" aria-hidden="true"><i/><i/></div>
    <div class="workspace-skeleton-layout" aria-hidden="true">
      <div class="workspace-skeleton-rail"><For each={[1,2,3,4,5]}>{()=><i/>}</For></div>
      <div class="workspace-skeleton-sidebar"><i/><i/><For each={[1,2,3,4]}>{()=><div><i/><i/></div>}</For></div>
      <div class="workspace-skeleton-main"><i/><div class="workspace-skeleton-messages"><i/><div><i/><i/><i/><i/></div><i/><div><i/><i/><i/></div></div><div class="workspace-skeleton-composer"><i/><i/></div></div>
    </div>
  </div>
);

export const WorkspaceInitError = (props: { onRetry: () => void }) => (
  <main class="grid min-h-screen place-items-center bg-[var(--tcode-bg)] px-4 text-[var(--tcode-text)]">
    <section class="w-full max-w-sm rounded-xl border border-[var(--tcode-border)] bg-[var(--tcode-surface)] p-6">
      <div role="alert">
        <h1 class="text-lg font-semibold">工作区加载失败</h1>
        <p class="mb-4 mt-2 text-sm text-[var(--tcode-text-muted)]">已保留登录状态，可以重新加载工作区。</p>
      </div>
      <button class="settings-save" type="button" onClick={props.onRetry}>重试</button>
    </section>
  </main>
);
