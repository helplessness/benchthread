import { expect, it } from 'vitest';
import { wikiHitBlock } from './wikiSearchLocation';

it('locates rendered Markdown without depending on screen position', () => {
  expect(wikiHitBlock('# 方法\n\n比较 **模型 A** 和 [模型 B](ref)。', ['方法', '比较 模型 A 和 模型 B。'])).toBe(1);
});

it('does not jump on duplicate or obsolete passages', () => {
  expect(wikiHitBlock('重复段落', ['重复段落', '重复段落'])).toBe(-1);
  expect(wikiHitBlock('旧段落', ['新段落'])).toBe(-1);
});
