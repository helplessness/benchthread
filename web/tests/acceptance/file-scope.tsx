// Run in a browser against the Web Vite dev server. Delayed transports intentionally ignore abort.
import { createSignal } from 'solid-js';
import { render } from 'solid-js/web';
import FileBrowser from '../../src/components/FileBrowser';

const [owner, account] = createSignal('alice:t1'), [workspace, move] = createSignal('shared');
const [job, setJob] = createSignal(false);
let revision = 'old', hold = true, delayDetail = false, oldDetailReads = 0, fileReads = 0;
let release!: () => void;
let releaseDetail!: () => void;
window.fetch = async input => {
  const u = new URL(String(input), location.href), id = revision;
  const run = { id:'job:'+id, session:id, started:1 };
  if (u.pathname.endsWith('/history/runs')) {
    if (hold) { hold = false; await new Promise<void>(r => {release=r}); }
    return Response.json({runs:[run],total:1});
  }
  if (u.pathname.endsWith('/history')) {
    if (u.searchParams.get('run_id')==='job:old') oldDetailReads++;
    if(delayDetail){delayDetail=false;await new Promise<void>(r=>{releaseDetail=r});return Response.json({detail:'STALE-HISTORY'},{status:500})}
    return Response.json({selected_run:run,changes:[{path:id+'.txt',status:'added'}]});
  }
  if (u.pathname.endsWith('/content')) { fileReads++; return Response.json({content:id+'正文'}); }
  return Response.json({project_bound:true,items:[{type:'file',name:'same.txt',path:'same.txt'}]});
};
const dispose = render(() => <>
  <FileBrowser mode="changes" identityScope={owner()} workspaceId={workspace()}/>
  <FileBrowser identityScope={owner()} workspaceId={job()?undefined:workspace()} jobId={job()?'example':undefined} artifactRoot={job()?'outputs':undefined}/>
</>, document.querySelector('#root')!);
const tick = () => new Promise(r=>setTimeout(r,0));
function check(ok:unknown, message:string){if(!ok)throw Error(message)}
async function until(f:()=>unknown,label:string){for(let i=0;i<100;i++){if(f())return;await new Promise(r=>setTimeout(r,15))}throw Error(label+': '+document.body.innerText.slice(0,320))}
async function test(){
  await tick();
  revision='bob'; account('bob:t1'); await tick();
  await until(()=>document.querySelector('.asset-history-picker-trigger')?.textContent?.includes('bob'),'same workspace account switch');
  await until(()=>document.querySelector('.asset-change-row')?.textContent?.includes('bob.txt'),'Bob history loaded');
  delayDetail=true;(document.querySelector('.asset-change-row') as HTMLElement).click();
  revision='tenant2'; account('bob:t2'); await tick();
  await until(()=>document.querySelector('.asset-history-picker-trigger')?.textContent?.includes('tenant2'),'same workspace tenant switch');
  revision='again'; account('alice:t1'); await tick(); release(); releaseDetail(); await tick();
  await until(()=>document.querySelector('.asset-history-picker-trigger')?.textContent?.includes('again'),'reload original account');
  check(!oldDetailReads,'late history selected old run');
  check(!document.body.textContent?.includes('STALE-HISTORY'),'old diff error shown');
  const picker=()=>[...document.querySelectorAll('button')].find(b=>b.textContent==='选取项目输入');
  const pick=async()=>{await until(picker,'picker ready');picker()!.click()};
  for(const fail of [false,true]){
    await pick(); move('other'); await tick(); move('shared'); await tick();
    const p=(window as any).finishPicker;
    if(fail)p.fail(Error('STALE-PICKER'));else p.ok('same.txt');
    await tick(); check(!fileReads&&!document.body.textContent?.includes('STALE-PICKER'),'stale picker affected current workspace');
  }
  await pick(); (window as any).finishPicker.ok('same.txt'); await tick();
  await until(()=>fileReads===1&&document.querySelector('pre')?.textContent?.includes('again正文'),'current picker preview');
  const bridge=(window as any).tcodeDesktop;
  const operations=['Finder','终端','在文件夹中显示'];
  const pending=new Map<string,(reason:Error)=>void>();
  const defer=(name:string)=>new Promise((_,reject)=>pending.set(name,reject));
  bridge.workspace=(_:string,action:string)=>defer(action==='finder'?'Finder':'终端');
  bridge.revealFile=()=>defer('在文件夹中显示');
  const press=(name:string)=>{
    const button=[...document.querySelectorAll('button')].find(b=>b.textContent===name);
    check(button,name+' missing');button!.click();check(pending.has(name),name+' not invoked');
  };
  for(const roundTrip of [
    (away:boolean)=>account(away?'bob:t1':'alice:t1'),
    (away:boolean)=>account(away?'alice:t2':'alice:t1'),
    (away:boolean)=>move(away?'other':'shared'),
  ]){
    for(const name of operations)press(name);
    roundTrip(true);await tick();roundTrip(false);await tick();
    for(const [name,reject] of pending){
      reject(Error('OLD-'+name));await tick();
      check(!document.body.innerText.includes('OLD-'+name),'late '+name);
    }
    pending.clear();
    const file=document.querySelector<HTMLElement>('[aria-label="预览文件 same.txt"]');
    check(file,'file missing after switch');file!.click();await tick();
  }
  for(const name of operations){
    press(name);pending.get(name)!(Error('CURRENT-'+name));await tick();
    check(document.querySelector('[role="alert"]')?.textContent?.includes('CURRENT-'+name),'missing current '+name);pending.delete(name);
  }
  const beforeJob=fileReads;
  setJob(true); revision='artifact'; await tick();
  const artefact=()=>document.querySelector<HTMLElement>('[aria-label="预览文件 same.txt"]');
  await until(artefact,'job directory'); artefact()!.click(); await tick();
  await until(()=>fileReads===beforeJob+1&&document.querySelector('pre')?.textContent?.includes('artifact正文'),'job-only preview');
}
test().then(()=>{document.querySelector('#result')!.textContent='PASS'}).catch(e=>{document.querySelector('#result')!.textContent='FAIL '+e.message}).finally(dispose);
