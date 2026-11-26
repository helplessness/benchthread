import {expect, it, vi} from 'vitest';
import {confirmResearchReproduction, readResearchSections} from './researchReads';
import {latestScopedRead} from './latestScopedRead';

it('keeps a successful experiment list when hypotheses fail, including a genuinely empty list', async () => {
  for (const experiments of [[], ['experiment B']]) {
    const {results, unavailable} = await readResearchSections(
      [Promise.reject(Error('offline')), Promise.resolve(experiments)], ['假说','实验']);
    expect(unavailable).toEqual(['假说']);
    expect(results[1]).toEqual({status:'fulfilled',value:experiments});
  }
});

it('reports only failed homepage sections, not the current project', async () => {
  const {results, unavailable} = await readResearchSections(
    [Promise.resolve('当前课题'), Promise.reject(Error()), Promise.reject(Error())], ['课题','导入记录','研究资料']);
  expect(unavailable).toEqual(['导入记录','研究资料']);
  expect(results[0]).toEqual({status:'fulfilled',value:'当前课题'});
});

it('does not replace B with a late failure from A even if the owner key is reused', async () => {
  const gate = latestScopedRead(() => '同一工作区');
  const old = gate.begin(); let reject!: () => void; let detail = 'A';
  const pending = new Promise<void>((_, fail) => {reject = () => fail(Error());}).catch(() => {if(old()) detail = '读取失败';});
  if (gate.begin()()) detail = 'B';
  reject(); await pending; expect(detail).toBe('B');
});

it.each([[false,true,0],[true,false,0],[true,true,1]] as const)(
  'reproduction confirmed=%s same project=%s submits %s times', async (confirmed,current,count) => {
    const submit = vi.fn(async () => {});
    await confirmResearchReproduction(async () => confirmed, () => current, submit);
    expect(submit).toHaveBeenCalledTimes(count);
});
