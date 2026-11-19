import { renderToString } from 'solid-js/web';
import { expect, it } from 'vitest';
import ArtifactDirectoryCard, { directoryFromTool, withoutDirectoryEcho } from './ArtifactDirectoryCard';

it('renders an Artifact directory without exposing its IDs or duplicated raw JSON', () => {
  const output = JSON.stringify({ service_id: 'internal-service', service_name: '实验室文件服务', directory: 'results', entries: [
    { kind: 'directory', path: 'results/samples' },
    { kind: 'file', path: 'results/report.csv', size: 2048, artifact_id: 'internal-artifact' },
    { kind: 'file', path: 'results/old:file', importable: false, artifact_id: 'old-artifact' },
  ], next_offset: 20 });
  const page = directoryFromTool({ tool_name: 'artifact_browse', state: 'completed', arguments: {}, output })!;
  expect(page).toBeDefined();
  expect(directoryFromTool({ tool_name: 'other', arguments: {}, output })).toBeUndefined();
  const html = renderToString(() => <ArtifactDirectoryCard page={page} sessionId="test" />);
  for (const label of ['实验室文件服务', '打开目录 samples', '下载 report.csv 到工作区', '2.0 KB', '加载更多', '旧文件名']) {
    expect(html).toContain(label);
  }
  expect(html).not.toMatch(/internal-service|internal-artifact|下载 old:file/);
  expect(withoutDirectoryEcho(`结果如下：\n\`\`\`json\n${output}\n\`\`\``, [page])).toBe('结果如下：');
  expect(withoutDirectoryEcho('{"message":"unrelated"}', [page])).toBe('{"message":"unrelated"}');
});
