// Real Electron + local backend. Only provider and native dialog answers are fixtures.
const {app,dialog}=require('electron');
const fs=require('node:fs'), path=require('node:path'), http=require('node:http'), assert=require('node:assert/strict');
const instances=require('../test-instance.cjs');
const identity={email:'isolation@example.invalid',password:require('node:crypto').randomUUID(),userId:'fixture'};
const fixture=instances.createTestInstance(app.getPath('temp'),process.env,identity);
fixture.environment.TCODE_AUTH_REQUIRED='0';
instances.createTestInstance=()=>fixture; instances.readTestLogin=()=>identity;
const roots=['workspace-A','workspace-B'].map(name=>{const dir=path.join(fs.realpathSync(fixture.root),name);fs.mkdirSync(dir);fs.writeFileSync(path.join(dir,'project-only.txt'),name);return dir});
const evidence=path.join(fixture.root,'evidence');fs.mkdirSync(evidence);
const checks=[];const pass=x=>{checks.push(x);console.log('PASS: '+x)};
let picker;
dialog.showOpenDialog=async()=>({canceled:false,filePaths:[picker]});
dialog.showMessageBox=async()=>({response:1});
const provider=http.createServer(async(req,res)=>{
 let body='';for await(const chunk of req)body+=chunk;
 const data=JSON.parse(body);const user=[...(data.messages||[])].reverse().find(m=>m.role==='user');
 const marker=JSON.stringify(user?.content).includes('CHAT_B_PRIVATE')?'CHAT_B_PRIVATE':'CHAT_A_PRIVATE';
 assert(!JSON.stringify(data.messages).includes(marker==='CHAT_B_PRIVATE'?'CHAT_A_PRIVATE':'CHAT_B_PRIVATE'),'foreign chat marker reached provider');
 const message={role:'assistant',content:'已记录 '+marker+'。\n\n[预览工作区文件](/workspace/upload/same.txt)'};
 const common={id:'fixture-chat',model:'fixture',created:1};
 if(data.stream){res.writeHead(200,{'Content-Type':'text/event-stream'});res.end('data: '+JSON.stringify({...common,object:'chat.completion.chunk',choices:[{index:0,delta:message,finish_reason:null}]})+'\n\ndata: '+JSON.stringify({...common,object:'chat.completion.chunk',choices:[{index:0,delta:{},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n')}
 else{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({...common,choices:[{index:0,message,finish_reason:'stop'}]}))}
});
let started=false;
const timeout=setTimeout(()=>{console.error('FAIL: desktop isolation timeout');app.exit(1)},120000);
app.on('browser-window-created',(_,win)=>win.webContents.on('did-finish-load',async()=>{
 if(started||!win.webContents.getURL().endsWith('/desktop.html'))return;started=true;
 const run=js=>win.webContents.executeJavaScript(js),j=JSON.stringify;
 const api=(url,body,method='POST')=>run(`(async()=>{const r=await fetch(${j(url)},${body===undefined?'{}':j({method,headers:{'Content-Type':'application/json'},body:j(body)})});let value=await r.text();try{value=JSON.parse(value)}catch{}return {status:r.status,value}})()`);
 const ok=async(url,body,method)=>{const r=await api(url,body,method);assert.equal(r.status,200,JSON.stringify(r));return r.value};
 const invoke=(method,...args)=>run(`window.tcodeDesktop[${j(method)}](...${j(args)})`);
 const shot=async name=>fs.writeFileSync(path.join(evidence,name+'.png'),(await win.webContents.capturePage()).toPNG());
 const ui=async code=>run(`(async()=>{const wait=async fn=>{for(let i=0;i<250;i++){const v=fn();if(v)return v;await new Promise(r=>setTimeout(r,100))}throw Error('UI timeout '+fn)};const button=text=>[...document.querySelectorAll('button')].find(b=>b.offsetWidth&&!b.disabled&&b.textContent.trim()===text);${code}})()`);
 const upload=async(id,text,name='same.txt')=>run(`(async()=>{const form=new FormData();form.append('file',new Blob([${j(text)}]),${j(name)});const r=await fetch('/api/files/upload?workspace_id='+${j(id)},{method:'POST',body:form});if(!r.ok)throw Error(await r.text());return r.json()})()`);
 const file=(id,p)=>'/api/files/content?'+new URLSearchParams({workspace_id:id,path:p});
 try{
  await ui("await wait(()=>button('新建任务'))");
  const spaces=[];
  for(let i=0;i<2;i++){
   picker=roots[i];const opened=await invoke('openProject');const id=opened.name;spaces.push(id);
   await upload(id,i?'FILE_B_PRIVATE':'FILE_A_PRIVATE');
   await upload(id,i?'ONLY_B':'ONLY_A',i?'only-b.txt':'only-a.txt');
   await ui(`button('新建任务').click();(await wait(()=>[...document.querySelectorAll('label')].find(l=>l.textContent.trim()===${j(path.basename(roots[i]))}))).click();(await wait(()=>button('创建任务'))).click();await wait(()=>!document.querySelector('#new-task-heading'));const input=await wait(()=>[...document.querySelectorAll('textarea[placeholder*="下达新指令"]')].find(e=>e.offsetWidth));input.value=${j(i?'CHAT_B_PRIVATE':'CHAT_A_PRIVATE')};input.dispatchEvent(new Event('input',{bubbles:true}));(await wait(()=>[...input.closest('form').querySelectorAll('button[type=submit]')].find(b=>!b.disabled&&b.textContent.includes('发送')))).click();await wait(()=>[...document.querySelectorAll('[data-message-result-body]')].some(e=>e.textContent.includes(${j(i?'CHAT_B_PRIVATE':'CHAT_A_PRIVATE')})));`);
   await ui(`(await wait(()=>[...document.querySelectorAll('a[data-local-path]')].find(a=>a.textContent==='预览工作区文件'))).click();await wait(()=>[...document.querySelectorAll('.file-preview-panel')].some(e=>e.textContent.includes(${j(i?'FILE_B_PRIVATE':'FILE_A_PRIVATE')})));`);
   const messages=await run("[...document.querySelectorAll('[data-message-id]')].map(e=>e.innerText).join(' ')");
   assert(messages.includes(i?'CHAT_B_PRIVATE':'CHAT_A_PRIVATE'));
   assert(!messages.includes(i?'CHAT_A_PRIVATE':'CHAT_B_PRIVATE'));
   await shot(i?'workspace-B':'workspace-A');
   pass(`workspace ${i?'B':'A'}: real chat renderer and linked file preview show only own markers`);
  }
  const [a,b]=spaces;
  assert.notEqual(a,b);
  assert.equal((await ok(file(a,'upload/same.txt'))).content,'FILE_A_PRIVATE');
  assert.equal((await ok(file(b,'upload/same.txt'))).content,'FILE_B_PRIVATE');
  assert.equal((await api(file(b,'upload/only-a.txt'))).status,404);
  assert.equal((await api(file(a,'upload/only-b.txt'))).status,404);
  const grantA=await invoke('projectFolders',a),grantB=await invoke('projectFolders',b);
  assert.equal(grantA.roots[0].path,roots[0]);assert.equal(grantB.roots[0].path,roots[1]);
  const cross=await api(file(b,path.join(roots[0],'project-only.txt')));assert([403,404].includes(cross.status));
  pass('two workspaces: same-name files separate; other workspace attachment and project path denied');
  const sessions=[];
  for(const id of spaces){const listing=await ok('/api/sessions?workspace_id='+id);const row=listing.sessions.find(s=>s.workspace_id===id);assert(row);sessions.push(row.session_id);const detail=await ok('/api/sessions/'+row.session_id);const text=JSON.stringify(detail.messages);assert(text.includes(id===a?'CHAT_A_PRIVATE':'CHAT_B_PRIVATE'));assert(!text.includes(id===a?'CHAT_B_PRIVATE':'CHAT_A_PRIVATE'))}
  assert.notEqual(...sessions);pass('workspace-filtered session lists and persisted transcripts remain separate');
  for(const i of [0,1,0]){
   await ui(`(await wait(()=>document.querySelector('[data-session-id="'+${j(sessions[i])}+'"]'))).click();await wait(()=>[...document.querySelectorAll('[data-message-result-body]')].some(e=>e.textContent.includes(${j(i?'CHAT_B_PRIVATE':'CHAT_A_PRIVATE')})));(await wait(()=>[...document.querySelectorAll('a[data-local-path]')].find(a=>a.textContent==='预览工作区文件'))).click();await wait(()=>[...document.querySelectorAll('.file-preview-panel')].some(e=>e.textContent.includes(${j(i?'FILE_B_PRIVATE':'FILE_A_PRIVATE')})));`);
   const shown=await run("[...document.querySelectorAll('[data-message-id]')].map(e=>e.innerText).join(' ')");
   assert(!shown.includes(i?'CHAT_A_PRIVATE':'CHAT_B_PRIVATE'));
   await shot(i?'workspace-B-switched':'workspace-A-switched');
  }
  pass('actual sidebar switching A to B and back restores the correct chat and file preview');
  const download=path.join(evidence,'downloaded-A.txt');
  const completed=new Promise((resolve,reject)=>win.webContents.session.once('will-download',(_e,item)=>{item.setSavePath(download);item.once('done',(_e,state)=>state==='completed'?resolve():reject(Error(state)))}));
  await invoke('saveFile',{workspaceId:a,path:'upload/same.txt'});await completed;assert.equal(fs.readFileSync(download,'utf8'),'FILE_A_PRIVATE');pass('native Electron download saved exact A bytes');
  assert.equal((await api('/api/files/entry',{workspace_id:a,path:'upload/same.txt',name:'renamed-A.txt'},'PATCH')).status,409);
  await ok('/api/files/entry',{workspace_id:a,path:'@roots/primary/project-only.txt',name:'renamed-A.txt'},'PATCH');
  assert.equal((await ok(file(a,'@roots/primary/renamed-A.txt'))).content,'workspace-A');
  const removed=await run(`fetch('/api/files/entry?'+new URLSearchParams({workspace_id:${j(a)},path:'@roots/primary/renamed-A.txt'}),{method:'DELETE'}).then(r=>r.status)`);assert.equal(removed,200);
  assert.equal((await api(file(a,'@roots/primary/renamed-A.txt'))).status,404);assert.equal((await ok(file(b,'upload/same.txt'))).content,'FILE_B_PRIVATE');
  assert.equal((await ok(file(b,'@roots/primary/project-only.txt'))).content,'workspace-B');
  assert.equal((await ok(file(a,'upload/same.txt'))).content,'FILE_A_PRIVATE');
  pass('attachment mutation denied; original project rename/delete in A leave B same-name file unchanged');
  await win.webContents.loadURL(win.webContents.getURL());await ui("await wait(()=>button('新建任务'))");
  assert.equal((await ok(file(b,'upload/same.txt'))).content,'FILE_B_PRIVATE');
  assert.deepEqual((await invoke('projectFolders',a)).roots.map(r=>r.path),[roots[0]]);
  assert.deepEqual((await invoke('projectFolders',b)).roots.map(r=>r.path),[roots[1]]);
  pass('renderer reload preserves separate files and project grants');
  await ui(`(await wait(()=>document.querySelector('[data-session-id="'+${j(sessions[0])}+'"] .session-row-actions'))).click();(await wait(()=>[...document.querySelectorAll('[role=menuitem]')].find(e=>e.textContent.trim()==='删除'))).click();(await wait(()=>button('确认删除'))).click();await wait(()=>!document.querySelector('[data-session-id="'+${j(sessions[0])}+'"]'));`);
  const deletion=await ok('/api/sessions/'+sessions[0]+'/delete-status');assert.equal(deletion.error,'');
  const remaining=await ok('/api/sessions/'+sessions[1]);assert(JSON.stringify(remaining.messages).includes('CHAT_B_PRIVATE'));
  assert.equal((await ok(file(b,'upload/same.txt'))).content,'FILE_B_PRIVATE');
  pass('real UI session deletion succeeds for A and preserves B chat and file');
  fs.writeFileSync(path.join(evidence,'report.json'),JSON.stringify({passed:true,checks,spaces,sessions,fixture:fixture.root,provider:'local fixture',dialogs:'automated'},null,2));
  console.log('EVIDENCE: '+evidence);
 }catch(error){console.error(error);await shot('failure');console.error(await run('document.body.innerText.slice(-1800)'));process.exitCode=1}
 finally{clearTimeout(timeout);provider.close();app.quit()}
}));
process.argv.push('--test-instance');
provider.listen(0,'127.0.0.1',()=>{const config=fixture.environment.TCODE_CONFIG_ROOT;fs.mkdirSync(config,{recursive:true});fs.writeFileSync(path.join(config,'tcode.json'),JSON.stringify({model:'fixture/offline',provider:{fixture:{npm:'@ai-sdk/openai-compatible',name:'本地测试模型',options:{baseURL:'http://127.0.0.1:'+provider.address().port+'/v1',apiKey:'fixture'},models:{offline:{name:'本地测试模型'}}}}}));require('../main.cjs')});
