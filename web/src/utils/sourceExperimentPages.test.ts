import {expect,it,vi} from 'vitest';
import {sourceExperimentPages} from './sourceExperimentPages';


const page = (start:number,n:number,next='') => ({items:Array.from({length:n},(_,i)=>({payload:{experiment_id:start+i}})),next_cursor:next,page_complete:!next});
it('retains the first 100 when loading more fails and retries the same cursor',async()=>{
  const read=vi.fn().mockResolvedValueOnce(page(1,100,'next')).mockRejectedValueOnce(Error()).mockResolvedValueOnce(page(101,1));
  const pages=sourceExperimentPages(read);
  await pages.load(); await pages.load();
  expect(pages.state()).toBe('error'); expect(pages.items()).toHaveLength(100);
  await pages.load(); await pages.load();
  expect(read.mock.calls.flat()).toEqual(['','next','next']);
  expect(pages.items()[100]?.experiment_id).toBe(101);
});
it('ignores late pages after changing packs and distinguishes an empty successful result',async()=>{
  let finish!:(p:ReturnType<typeof page>)=>void;
  const pages=sourceExperimentPages(vi.fn().mockImplementationOnce(()=>new Promise(r=>finish=r)).mockResolvedValue(page(1,0)));
  const pending=pages.load(); expect(pages.state()).toBe('loading'); pages.reset();
  await pages.load(); finish(page(1,4)); await pending;
  expect(pages.state()).toBe('ready'); expect(pages.items()).toEqual([]);
});
