import { createEffect, createSignal, onCleanup, onMount } from 'solid-js';
import type { ConnectBrowserTabs } from '../clientPlatform';

export function openDesktopBrowser() {
  window.dispatchEvent(new CustomEvent('tcode:open-browser'));
}

export const connectDesktopBrowserTabs: ConnectBrowserTabs = (active, toggleTab) => {
  const [titles, setTitles] = createSignal<Record<string, string>>({});
  onMount(() => {
    const panel = (event: Event) => {
      const { open, session } = (event as CustomEvent<{ open: boolean; session: string }>).detail;
      if (session) toggleTab(session, open);
    };
    const titled = (event: Event) => {
      const { session, title } = (event as CustomEvent<{ session: string; title: string }>).detail;
      setTitles(previous => previous[session] === title ? previous : { ...previous, [session]: title });
    };
    window.addEventListener('tcode:browser-panel', panel);
    window.addEventListener('tcode:browser-title', titled);
    onCleanup(() => {
      window.removeEventListener('tcode:browser-panel', panel);
      window.removeEventListener('tcode:browser-title', titled);
    });
  });
  createEffect(() => {
    const session = active();
    if (session) window.dispatchEvent(new CustomEvent('tcode:select-browser', { detail: session }));
  });
  return {
    label: session => titles()[session] || '浏览器',
    close: session => window.dispatchEvent(new CustomEvent('tcode:close-browser', { detail: session })),
    reopen: openDesktopBrowser,
  };
};
