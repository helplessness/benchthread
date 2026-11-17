import { describe, expect, it } from 'vitest';

import { nativeModelTurnBlock, resolveActiveModel, shouldAcceptResponseModel } from './modelSelection';

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
