import { renderToString } from 'solid-js/web';
import { expect, it } from 'vitest';
import ExecutionSettings from './ExecutionSettings';

it('shows service names without putting the stored model reference in visible text or the tooltip', () => {
  const selected = 'desktop-ab1234/text';
  const models = [
    { id: 'desktop-cd4567/text', provider_id: 'desktop-cd4567', provider_name: '备用服务', model_id: 'text', name: '文本模型' },
    { id: selected, provider_id: 'desktop-ab1234', provider_name: '主服务', model_id: 'text', name: '文本模型' },
  ];
  const html = renderToString(() => <ExecutionSettings
    agents={() => []}
    handleAgentChange={async () => {}}
    handleReasoningEffortChange={() => {}}
    runtime={{ selected: () => 'tcode-native', available: () => [], error: () => '', pending: () => false, onChange: async () => {} }}
    model={{ selected: () => selected, visible: () => models, available: () => models, error: () => '', pending: () => false, onChange: async () => {} }}
  />);
  expect(html).toContain('title="文本模型 · 主服务"');
  expect(html).toMatch(/<option\b[^>]* value="desktop-ab1234\/text"[^>]*>文本模型 · 主服务<\/option>/);
  expect(html).toMatch(/<option\b[^>]* value="desktop-cd4567\/text"[^>]*>文本模型 · 备用服务<\/option>/);
  expect(html.replace(/<[^>]+>/g, '')).not.toContain('desktop-');
});
