import { expect, it } from 'vitest';
import { latestScopedRead } from './latestScopedRead';

it('only the current request may publish results, errors or loading state', async () => {
  let owner = 'me', finish!: () => void;
  const gate = latestScopedRead(() => owner), a = gate.begin();
  const events: string[] = [];
  const delayed = new Promise<void>(resolve => finish = resolve).then(() => {
    if (a()) events.push('old result');
  }).finally(() => { if (a()) events.push('old loading'); });
  const b = gate.begin();
  if (b()) events.push('new result');
  finish(); await delayed;
  expect(events).toEqual(['new result']);
  owner = 'shared'; expect(b()).toBe(false);
  const c = gate.begin(); gate.invalidate(); expect(c()).toBe(false);
});
