import { describe, expect, it, vi } from 'vitest';
import {
  autoEmbedStandaloneImagePaths,
  getInlineFileUrl,
  isImageFileRef,
  normalizeInlineFilePath,
  toInlineImageMarkdown,
} from './inlineFiles';

describe('inline workspace images', () => {
  it('keeps the managed source in desktop link targets and inline URLs only', () => {
    try {
      for (const desktop of [false, true]) {
        vi.stubGlobal('window', desktop ? {tcodeDesktop: {}} : {});
        for (const rel of ['project-inputs/hash/report.txt', 'artifacts/jobs/job/report.txt', 'artifacts/local/job/report.txt']) {
          const path = '/workspace/' + rel;
          expect(normalizeInlineFilePath(path)).toBe(desktop ? path : rel);
          expect(new URL(getInlineFileUrl(path), 'http://test').searchParams.get('path')).toBe(desktop ? path : rel);
          expect(normalizeInlineFilePath(rel)).toBe(rel);
        }
        expect(normalizeInlineFilePath('/workspace/docs/report.txt')).toBe('docs/report.txt');
      }
    } finally { vi.unstubAllGlobals(); }
  });
  it('maps the sandbox workspace root to a browser-visible workspace path', () => {
    expect(normalizeInlineFilePath('/workspace/docs/figure.png')).toBe('docs/figure.png');
    expect(getInlineFileUrl('/workspace/docs/figure.png', 'research')).toBe(
      '/api/files/inline?path=docs%2Ffigure.png&workspace_id=research',
    );
  });

  it('recognizes local images but not remote images', () => {
    expect(isImageFileRef('artifacts/plot.webp')).toBe(true);
    expect(isImageFileRef('https://example.com/plot.webp')).toBe(false);
  });

  it('embeds a standalone image path without changing code fences', () => {
    const content = [
      '结果如下：',
      '/workspace/artifacts/plot.png',
      '```text',
      '/workspace/artifacts/debug.png',
      '```',
    ].join('\n');

    const rendered = autoEmbedStandaloneImagePaths(content);

    expect(rendered).toContain('![plot.png](/workspace/artifacts/plot.png)');
    expect(rendered).toContain('```text\n/workspace/artifacts/debug.png\n```');
  });

  it('does not mistake a prose sentence ending in an image name for a complete path', () => {
    const content = '文件已验证：page_2_figure_1.png';
    expect(autoEmbedStandaloneImagePaths(content)).toBe(content);
  });

  it('builds safe Markdown for an uploaded image path', () => {
    expect(toInlineImageMarkdown('/workspace/upload/my plot.png', 'my [plot].png')).toBe(
      '![my \\[plot\\].png](</workspace/upload/my plot.png>)',
    );
    expect(toInlineImageMarkdown('/workspace/upload/report.pdf', 'report')).toBe('');
  });
});
