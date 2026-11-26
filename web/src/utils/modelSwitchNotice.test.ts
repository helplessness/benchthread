import { beforeEach, expect, it, vi } from 'vitest';
const { confirm } = vi.hoisted(() => ({ confirm: vi.fn() }));
vi.mock('../components/FeedbackDialog', () => ({ confirmAction: confirm }));
import { confirmModelSwitch, recordModelSwitch } from './modelSwitchNotice';
beforeEach(() => { confirm.mockReset(); confirm.mockResolvedValue(true); });

it('shows readable labels while confirming only the original model reference', async () => {
  recordModelSwitch('labels', 'desktop-first/text', 'desktop-second/text', {
    from: '文本模型 · 主服务', to: '文本模型 · 备用服务',
  });
  await confirmModelSwitch('labels', '文本模型 · 备用服务');
  expect(confirm).not.toHaveBeenCalled();
  expect(await confirmModelSwitch('labels', 'desktop-second/text')).toBe(true);
  expect(confirm.mock.calls[0][0]).toContain('从 文本模型 · 主服务 切换为 文本模型 · 备用服务');
  expect(confirm.mock.calls[0][0]).not.toContain('desktop-');
});

it('warns only on the next request, once for an acknowledged switch', async () => {
  recordModelSwitch('once', 'old', 'new');
  expect(confirm).not.toHaveBeenCalled();
  expect(await confirmModelSwitch('once', 'new')).toBe(true);
  expect(confirm.mock.calls[0][0]).toContain('可能出现效果下降');
  expect(await confirmModelSwitch('once', 'new')).toBe(true);
  expect(confirm).toHaveBeenCalledTimes(1);
});
it('retains the reminder after cancelling and isolates sessions', async () => {
  recordModelSwitch('cancel', 'a', 'b');
  confirm.mockResolvedValueOnce(false);
  expect(await confirmModelSwitch('cancel', 'b')).toBe(false);
  expect(await confirmModelSwitch('other', 'b')).toBe(true);
  expect(await confirmModelSwitch('cancel', 'b')).toBe(true);
  expect(confirm).toHaveBeenCalledTimes(2);
});
it('coalesces multiple changes and clears a switch back to the original model', async () => {
  recordModelSwitch('coalesce', 'a', 'b');
  recordModelSwitch('coalesce', 'b', 'c');
  await confirmModelSwitch('coalesce', 'c');
  expect(confirm.mock.calls[0][0]).toContain('从 a 切换为 c');
  recordModelSwitch('back', 'a', 'b');
  recordModelSwitch('back', 'b', 'a');
  await confirmModelSwitch('back', 'a');
  expect(confirm).toHaveBeenCalledTimes(1);
});
it('rejects duplicate confirmation and does not acknowledge a newer switch', async () => {
  let resolve!: (value: boolean) => void;
  confirm.mockImplementationOnce(() => new Promise<boolean>(done => { resolve = done; }));
  recordModelSwitch('race', 'a', 'b');
  const pending = confirmModelSwitch('race', 'b');
  expect(await confirmModelSwitch('race', 'b')).toBe(false);
  recordModelSwitch('race', 'b', 'c');
  resolve(true);
  expect(await pending).toBe(false);
  expect(await confirmModelSwitch('race', 'c')).toBe(true);
  expect(confirm).toHaveBeenCalledTimes(2);
});
