import { For, Show, createMemo, createSignal } from 'solid-js';
import DOMPurify from 'dompurify';
import { marked } from 'marked';
import { csvPreview } from '../utils/csvPreview';

export default function TextFilePreview(props: { path: string; content: string }) {
  const [sourceFor, setSourceFor] = createSignal('');
  const markdown = () => /\.md(?:own)?$|\.markdown$/i.test(props.path);
  const csv = () => /\.csv$/i.test(props.path);
  const source = () => sourceFor() === props.path;
  const html = createMemo(() => markdown() && !source() ? DOMPurify.sanitize(
    marked.parse(props.content, { async: false }),
    { ALLOWED_TAGS: ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'br', 'hr', 'blockquote', 'pre', 'code', 'strong', 'em', 'del', 'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'th', 'td'], ALLOWED_ATTR: ['start'] },
  ) : '');
  const table = createMemo(() => csv() ? csvPreview(props.content) : undefined);

  return <>
    <Show when={markdown()}>
      <button type="button" class="mb-2 min-h-11 rounded border border-[#2a3136] px-3 text-sm text-[#dee3e8]"
        onClick={() => setSourceFor(source() ? '' : props.path)}>
        {source() ? '查看排版' : '查看源码'}
      </button>
    </Show>
    <Show when={markdown() && !source()}>
      <article class="markdown-content overflow-auto rounded border border-[#2a3136] p-4 text-sm text-[#dee3e8]" innerHTML={html()} />
    </Show>
    <Show when={table()}>{data => <>
      <Show when={data().limited}><p class="mb-2 text-sm text-[#b6c2c9]">仅预览前 200 行、100 列；请下载原文件查看完整内容。</p></Show>
      <Show when={data().rows.length} fallback={<p class="text-sm text-[#b6c2c9]">没有可预览的 CSV 数据。</p>}>
        <div class="max-h-[65vh] overflow-auto rounded border border-[#2a3136]" tabIndex={0} role="region" aria-label="CSV 表格预览">
          <table class="w-full border-collapse text-left text-sm text-[#dee3e8]">
            <thead class="sticky top-0 bg-[#22292e]"><tr><For each={data().rows[0]}>
              {cell => <th scope="col" class="border border-[#2a3136] px-3 py-2 whitespace-pre-wrap">{cell}</th>}
            </For></tr></thead>
            <tbody><For each={data().rows.slice(1)}>{row => <tr><For each={row}>
              {cell => <td class="border border-[#2a3136] px-3 py-2 whitespace-pre-wrap">{cell}</td>}
            </For></tr>}</For></tbody>
          </table>
        </div>
      </Show>
    </>}</Show>
    <Show when={!csv() && (!markdown() || source())}>
      <pre class="overflow-x-auto rounded border border-[#2a3136] bg-[#0d1216] p-4 font-mono text-xs text-[#e6edf3] whitespace-pre-wrap">{props.content}</pre>
    </Show>
  </>;
}
