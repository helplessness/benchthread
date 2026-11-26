import { describe, expect, it } from 'vitest';

import { modelDisplayLabel, nativeModelTurnBlock, resolveActiveModel, shouldAcceptResponseModel } from './modelSelection';

describe('modelDisplayLabel', () => {
  it('uses readable service names to distinguish identical model IDs', () => {
    const model = { id: 'desktop-ab1234/deepseek-v4-flash', provider_id: 'desktop-ab1234', name: 'DeepSeek V4 Flash' };
    expect(modelDisplayLabel({ ...model, provider_name: 'OpenCode Go' })).toBe('DeepSeek V4 Flash · OpenCode Go');
    expect(modelDisplayLabel({ ...model, id: 'desktop-cd4567/deepseek-v4-flash', provider_id: 'desktop-cd4567', provider_name: '实验室网关' }))
      .toBe('DeepSeek V4 Flash · 实验室网关');
  });

  it('hides managed IDs for legacy options and preserves unmanaged runtime labels', () => {
    const id = 'desktop-ab1234/vendor/text';
    expect(modelDisplayLabel({ id, name: id })).toBe('vendor/text');
    expect(modelDisplayLabel({ id, name: '文本模型', provider_name: 'desktop-ab1234' })).toBe('文本模型');
    expect(modelDisplayLabel({ id: 'web-model-cd4567/text', name: 'text' })).toBe('text');
    expect(modelDisplayLabel({ id: 'opencode-go/kimi', name: 'opencode-go/kimi' })).toBe('opencode-go/kimi');
    expect(modelDisplayLabel({ id: '', name: '无可用模型' })).toBe('无可用模型');
  });
});

describe('resolveActiveModel', () => {
  it('keeps a server-confirmed selection when a background projection is stale', () => {
    expect(resolveActiveModel(
      'kimi/kimi-k2.6',
      'kimi/kimi-k2.5',
      'kimi/kimi-k2.5',
    )).toBe('kimi/kimi-k2.6');
  });

  it('falls back to server projections before a selection is loaded', () => {
    expect(resolveActiveModel('', 'kimi/kimi-k2.6', 'kimi/kimi-k2.5'))
      .toBe('kimi/kimi-k2.6');
    expect(resolveActiveModel('', '', 'kimi/kimi-k2.5'))
      .toBe('kimi/kimi-k2.5');
  });

  it('rejects a late response from the model used before a rapid switch', () => {
    expect(shouldAcceptResponseModel('kimi/kimi-k2.6', 'kimi/kimi-k2.5')).toBe(false);
    expect(shouldAcceptResponseModel('kimi/kimi-k2.6', 'kimi/kimi-k2.6')).toBe(true);
  });
});

describe('nativeModelTurnBlock', () => {
  it('blocks new requests before verification (including a failed fetch), and unlocks after retry', () => {
    expect(nativeModelTurnBlock(false, undefined, '')).toBe('unverified');
    expect(nativeModelTurnBlock(false, undefined, 'demo/stale')).toBe('unverified');
    expect(nativeModelTurnBlock(false, [], '')).toBe('empty');
    expect(nativeModelTurnBlock(false, [{ id: 'demo/valid' }], 'demo/stale')).toBe('unverified');
    expect(nativeModelTurnBlock(false, [{ id: 'demo/valid' }], 'demo/valid')).toBe('');
  });

  it('lets guidance for an existing goal through without opening a new chat turn', () => {
    expect(nativeModelTurnBlock(true, undefined, '')).toBe('');
    expect(nativeModelTurnBlock(true, [], '')).toBe('');
  });
});
