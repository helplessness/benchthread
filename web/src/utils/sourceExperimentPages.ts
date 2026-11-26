import { createSignal } from 'solid-js';
import type { ResearchPackReadObjectPage } from '../api/research';

export function sourceExperimentPages(read: (cursor:string) => Promise<Pick<ResearchPackReadObjectPage,'items'|'page_complete'|'next_cursor'>>) {
  const [items,setItems] = createSignal<Array<Record<string, any>>>([]);
  const [state,setState] = createSignal<'idle'|'loading'|'ready'|'error'>('idle');
  const [cursor,setCursor] = createSignal('');
  let version = 0;
  const reset = () => { ++version; setItems([]); setCursor(''); setState('idle'); };
  const load = async () => {
    if (state() === 'loading' || (state() === 'ready' && !cursor())) return;
    const current = ++version;
    setState('loading');
    try {
      const page = await read(cursor());
      if (current !== version) return;
      if (!page.page_complete && (!page.next_cursor || page.next_cursor === cursor())) throw Error('invalid cursor');
      setItems(prev => [...prev, ...page.items.map(item => item.payload || item)]);
      setCursor(page.page_complete ? '' : page.next_cursor);
      setState('ready');
    } catch { if (current === version) setState('error'); }
  };
  return {items,state,cursor,load,reset};
}
