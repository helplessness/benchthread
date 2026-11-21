import assert from 'node:assert/strict';
import {test} from 'node:test';
import {setTimeout as sleep} from 'node:timers/promises';
import {getPanel} from '../client.js';
import {createMockPanel} from '../mock.js';

const turn = {request_id: '17adb089-343f-4b6c-bad0-c21c346afd5d', question: '测试'};
test('本地预览在显式选择时启用', () => {
  assert.throws(getPanel, /TCode/);
});
test('提交、重试、读取历史并明确分享', async () => {
  const shared = [];
  const panel = createMockPanel({delayMs: 0, onShare: value => shared.push(value)});
  const run = await panel.submit(turn);
  assert.deepEqual(await panel.submit(turn), run);
  await assert.rejects(panel.submit({...turn, question: '不同输入'}));
  const item = {run: run.id, item_id: 'answer', text: '选中内容'};
  await assert.rejects(panel.share(item));
  await sleep(10);
  assert.equal((await panel.history()).runs[0].status, 'completed');
  assert.deepEqual(shared, []);
  await panel.share(item);
  assert.deepEqual(shared, [item]);
});
test('取消中的异步回答不会覆盖取消状态，权限和实例独立', async () => {
  let finish;
  const panel = createMockPanel({delayMs: 0, respond: () => new Promise(resolve => { finish = resolve; })});
  const run = await panel.submit(turn);
  await sleep(10);
  await panel.cancel(run.id);
  finish('已过期');
  await sleep(0);
  assert.equal((await panel.history()).runs[0].status, 'cancelled');
  const denied = createMockPanel({grants: []});
  assert.deepEqual((await denied.history()).runs, []);
  await assert.rejects(denied.submit(turn), /team.run/);
  await assert.rejects(denied.share({}), /selection.share/);
});
