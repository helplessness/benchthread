// Run using desktop/node_modules/.bin/electron. Actual renderer, auth cookies, IPC and backend;
// only the macOS file selector and confirmation answers are automated.
const {app, dialog} = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {randomUUID} = require('node:crypto');
const instances = require('../test-instance.cjs');
const a = {email:'a@example.invalid',password:randomUUID(),userId:'account-a'};
const b = {email:'b@example.invalid',password:randomUUID(),user_id:'account-b'};
const instance = instances.createTestInstance(app.getPath('temp'),process.env,a);
instances.createTestInstance = () => instance;
instances.readTestLogin = () => a;
const dirs = ['main','reports'].map(name => {
  const dir=path.join(fs.realpathSync(instance.root),name); fs.mkdirSync(dir);
  fs.writeFileSync(path.join(dir,'same.txt'),name); return dir;
});
let picker, duringConfirm, started=false;
dialog.showOpenDialog = async () => ({canceled:false,filePaths:[picker]});
dialog.showMessageBox = async (_,opts) => {
  if (duringConfirm) { const change = duringConfirm; duringConfirm = null; await change(); }
  assert.ok(['允许这个项目访问所选文件夹？','将此文件夹设为只读','设为默认工作目录','解除此文件夹的关联'].includes(opts.message));
  return {response:1};
};
const timeout=setTimeout(()=>{console.error('FAIL: account fixture timed out');app.exit(1);},90000);
app.on('browser-window-created',(_,win)=>win.webContents.on('did-finish-load',async()=>{
  if(started || !win.webContents.getURL().endsWith('/desktop.html')) return;
  started=true;
  const run = js => win.webContents.executeJavaScript(js);
  const pause = ms=>new Promise(r=>setTimeout(r,ms));
  const wait = async js=>{for(let i=0;i<150;i++){if(await run(js))return;await pause(100);}throw Error('UI not ready: '+js);};
  const json = JSON.stringify;
  const api = (url,body)=>run(`(async()=>{const r=await fetch(${json(url)},${body===undefined?'{}':json({method:'POST',headers:{'Content-Type':'application/json'},body:json(body)})});if(!r.ok)throw Error('HTTP '+r.status+': '+await r.text());return r.json()})()`);
  const invoke=(method,...args)=>run(`window.tcodeDesktop[${json(method)}](...${json(args)})`);
  const loginUI=async who=>{
    await wait("!!document.querySelector('input[type=email]')");
    await run(`(()=>{const email=document.querySelector('input[type=email]'), pw=document.querySelector('#login-password');
      for(const [input,value] of [[email,${json(who.email)}],[pw,${json(who.password)}]]){input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));}
      email.form.requestSubmit();})()`);
    await wait("document.body.innerText.includes('新建任务')");
    assert.equal((await api('/api/auth/me')).user.email,who.email);
  };
  try {
    await loginUI(a);
    const tenant=(await api('/api/auth/me')).user.tenant_id;
    await api('/api/admin/users',b); await api('/api/tenants',{tenant_id:'fixture-other',name:'Fixture Other'});
    picker=dirs[0]; const {name:id}=await invoke('openProject');
    picker=dirs[1]; await invoke('addProjectFolder',id);
    const folders=()=>invoke('projectFolders',id);
    const change=async (root,action)=>invoke('projectFolders',id,{id:root,action,revision:(await folders()).revision});
    const report=(await folders()).roots.find(r=>r.name==='reports').id;
    await change(report,'default');
    for(const [index,root] of [[0,'primary'],[1,report]]){
      picker=path.join(dirs[index],'same.txt');const ref=await invoke('projectInput',id);
      assert.equal(ref,'@roots/'+root+'/same.txt');
      assert.equal((await api('/api/files/content?'+new URLSearchParams({workspace_id:id,path:ref}))).content,path.basename(dirs[index]));
    }
    await change('primary','ro');
    const receipt=await run(`(async()=>{const f=new FormData();f.append('file',new Blob(['A private attachment']),'a.txt');
      const r=await fetch('/api/files/upload?workspace_id='+${json(id)},{method:'POST',body:f});if(!r.ok)throw Error(await r.text());return r.json()})()`);
    assert.equal((await api('/api/files/content?'+new URLSearchParams({workspace_id:id,path:receipt.relative_path}))).content,'A private attachment');
    assert.ok(!fs.existsSync(path.join(dirs[0],'upload','a.txt')));
    duringConfirm=()=>api('/api/tenants/fixture-other/switch',{});
    await assert.rejects(change(report,'ro'),/账号或租户已切换/);
    await assert.rejects(folders(),/请先打开一个项目/);
    await api('/api/tenants/'+encodeURIComponent(tenant)+'/switch',{});
    assert.equal((await folders()).roots.find(r=>r.id===report).access,'rw');
    duringConfirm=async()=>{await api('/api/auth/logout',{});await api('/api/auth/login',b);};
    await assert.rejects(change('primary','remove'),/账号或租户已切换/);
    assert.equal((await api('/api/auth/me')).user.email,b.email);
    await assert.rejects(folders(),/请先打开一个项目/);
    await api('/api/auth/logout',{}); await win.webContents.loadURL(win.webContents.getURL()); await loginUI(b);
    picker=dirs[0]; assert.equal((await invoke('openProject')).name,id);
    assert.deepEqual((await folders()).roots.map(r=>[r.id,r.access]),[['primary','rw']]);
    await assert.rejects(api('/api/files/content?'+new URLSearchParams({workspace_id:id,path:'@roots/'+report+'/same.txt'})),/HTTP 404/);
    await assert.rejects(api('/api/files/content?'+new URLSearchParams({workspace_id:id,path:receipt.relative_path})),/HTTP 404/);
    await change('primary','remove'); assert.equal((await folders()).roots.length,0);
    await api('/api/auth/logout',{}); await win.webContents.loadURL(win.webContents.getURL()); await loginUI(a);
    const p=await folders(); assert.equal(p.default_id,report);assert.equal(p.roots.length,2);
    assert.equal(p.roots.find(r=>r.id==='primary').access,'ro');
    console.log('PASS: two desktop logins; same-name picker; read-only attachment; pending account/tenant changes cancelled; B cannot read A grants or attachments; per-folder unlink leaves A intact.');
  } catch(e){console.error(e.message);process.exitCode=1;}
  finally{clearTimeout(timeout);app.quit();}
}));
process.argv.push('--test-instance');
require('../main.cjs');
