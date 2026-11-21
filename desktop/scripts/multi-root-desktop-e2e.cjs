// Exercise the actual Electron renderer/preload, native handlers and desktop backend.
// OS picker/permission answers and the loopback chat provider are fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const instances = require('../test-instance.cjs');
const create = instances.createTestInstance;
const identity = {email:'fixture@example.invalid', password:'desktop-fixture-only', userId:'fixture'};
if (!process.versions.electron) {
  const {spawnSync} = require('node:child_process');
  const f = create(require('node:os').tmpdir(),process.env,identity);
  const electron = path.resolve(__dirname,'../node_modules/electron/dist/Electron.app/Contents/MacOS/Electron');
  for (const phase of ['create','reopen']) {
    const child = spawnSync(electron,[__filename],{stdio:'inherit',timeout:90000,
      env:{...process.env,BENCHTHREAD_FIXTURE_PHASE:phase,BENCHTHREAD_FIXTURE:JSON.stringify(f)}});
    if(child.status !== 0) process.exit(child.status || 1);
  }
  console.log('PASS: isolated Electron relaunched with the same project state'); return;
}
const { app, dialog } = require('electron');
const resume = process.env.BENCHTHREAD_FIXTURE_PHASE === 'reopen';
const http = require('node:http');
let marker = '验收样本' + require('node:crypto').randomBytes(6).toString('hex');
let fixture, folders;
const digest = bytes => require('node:crypto').createHash('sha256').update(bytes).digest('hex');
const snapshotRel = 'project-inputs/'+digest('scripts')+'/same.txt';
const jobRel = 'artifacts/jobs/0123456789abcdefabcd/same.txt';
const received = new Map();
const calls = [];
let sawOriginalTrashPath = false;
let shellReceipt;
const provider = http.createServer(async (req,res) => {
  try {
    if (req.method === 'PUT' && req.url.startsWith('/api/v1/files/by-path?')) {
      const parts=[]; for await(const part of req) parts.push(part);
      const form=await new Response(Buffer.concat(parts),{headers:{'content-type':req.headers['content-type']}}).formData();
      const bytes=Buffer.from(await form.get('file').arrayBuffer());
      const dest=new URL(req.url,'http://fixture').searchParams.get('path');
      assert(!received.has(dest)); received.set(dest,bytes.toString());
      res.writeHead(201,{'Content-Type':'application/json'});
      res.end(JSON.stringify({artifact_id:'fixture-content',size:bytes.length,sha256:digest(bytes)}));return;
    }
    if (!req.url.endsWith('/chat/completions')) { res.writeHead(404);res.end();return; }
    let body=''; for await(const chunk of req) body+=chunk;
    const request=JSON.parse(body);
    const tools=(request.tools||[]).map(t=>t.function?.name);
    const contents=JSON.stringify(request.messages);
    if (!resume && request.messages.some(m=>m.role==='tool' && JSON.stringify(m.content)
        .includes('原位置：'+path.join(folders[2],'shortcut-check.txt')))) sawOriginalTrashPath=true;
    let call, text='已完成本地文件验证。';
    if (!resume && contents.includes('普通 Bash 托管验收')) {
      const outputs=request.messages.filter(m=>m.role==='tool').map(m=>JSON.stringify(m.content));
      shellReceipt=outputs.join('\n').match(/\/workspace\/artifacts\/local\/native-[a-f0-9]+\/same\.txt/)?.[0];
      if (outputs.some(s=>s.includes('1: managed-shell-'+marker))) text='已从托管回执读取：managed-shell-'+marker;
      else if (shellReceipt) call=['read',{filePath:shellReceipt}];
      else call=['bash',{command:'printf %s managed-shell-'+marker+' > same.txt'}];
    } else if (resume && contents.includes('重启回读验收') && calls.includes('bash') && contents.includes('项目目录授权已变更')) {
      text='重启回读通过：'+marker+'\n\n[查看快照](/workspace/'+snapshotRel+') · [查看异步产物](/workspace/'+jobRel+')';
    } else if (resume && contents.includes('重启回读验收') && tools.length) {
      const seen=value=>request.messages.some(m=>m.role==='tool' && JSON.stringify(m.content).includes(value));
      if (!seen('attachment:'+marker)) call=['read',{filePath:'/workspace/upload/evidence.txt'}];
      else if (!seen('1: '+marker)) call=['read',{filePath:path.join(folders[2],'verified.txt')}];
      else if (!seen('1: scripts')) call=['read',{filePath:'/workspace/'+snapshotRel}];
      else if (!seen('1: recovered:'+marker)) call=['read',{filePath:'/workspace/'+jobRel}];
      else if (!received.has('selected.txt')) call=['artifact_upload',{local_path:'/workspace/'+snapshotRel,remote_path:'selected.txt'}];
      else if (!received.has('result.txt')) call=['artifact_upload',{local_path:'/workspace/'+jobRel,remote_path:'result.txt'}];
      else if (!calls.includes('bash')) call=['bash',{command:'curl -o readonly-check.txt https://example.invalid/file'}];
    } else if (contents.includes('跨目录桌面验收') && tools.length) {
      const hasRead=request.messages.some(m=>m.role==='tool' && JSON.stringify(m.content).includes(marker));
      const found=request.messages.filter(m=>m.role==='tool').map(m=>JSON.stringify(m.content).match(/@roots\/[a-f0-9]+\/verification\.txt/)?.[0]).find(Boolean);
      if (!found && tools.includes('glob')) call=['glob',{path:folders[1],pattern:'verification.txt'}];
      else if (found && !hasRead && tools.includes('read')) call=['read',{filePath:found}];
      else if (hasRead && !calls.includes('write') && tools.includes('write')) call=['write',{filePath:path.join(folders[2],'verified.txt'),content:marker}];
      else if (calls.includes('write') && !calls.includes('bash')) call=['bash',{command:'trash shortcut-check.txt'}];
    }
    if(call) calls.push(call[0]);
    const invocation=call && {id:'call_'+calls.length,type:'function',function:{name:call[0],arguments:JSON.stringify(call[1])}};
    const message=call?{role:'assistant',content:null,tool_calls:[invocation]}:{role:'assistant',content:text};
    const common={id:'chatcmpl-benchthread-test',created:1,model:'desktop-fixture'};
    const finish=call?'tool_calls':'stop';
    if (request.stream) {
      res.writeHead(200,{'Content-Type':'text/event-stream'});
      for (const [delta,reason] of [[call?{role:'assistant',tool_calls:[{...invocation,index:0}]}:message,null],[{},finish]]) {
        res.write('data: '+JSON.stringify({...common,object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason:reason}]})+'\n\n');
      }
      res.end('data: [DONE]\n\n');
    } else {
      res.writeHead(200,{'Content-Type':'application/json'});
      res.end(JSON.stringify({...common,object:'chat.completion',choices:[{index:0,message,finish_reason:finish}]}));
    }
  } catch(error) {console.error('Fixture provider:',error.message);res.end();}
});
instances.readTestLogin = () => identity;
instances.createTestInstance = (...args) => {
  fixture = process.env.BENCHTHREAD_FIXTURE ? JSON.parse(process.env.BENCHTHREAD_FIXTURE) : create(...args);
  assert.equal(JSON.parse(fs.readFileSync(path.join(fixture.root,'test-instance.json'),'utf8')).isolated,true);
  fixture.environment.TCODE_AUTH_REQUIRED = '0';
  Object.assign(fixture.environment,{
    TMCP_ARTIFACT_SERVICE_ID:'fixture',TMCP_ARTIFACT_SERVICES:JSON.stringify({fixture:{name:'fixture',url:'http://127.0.0.1:'+provider.address().port,token_env:'FIXTURE_ARTIFACT_TOKEN'}}),
    FIXTURE_ARTIFACT_TOKEN:require('node:crypto').randomUUID(),TCODE_ARTIFACT_UPLOAD_ACCESS:JSON.stringify({fixture:{admin_tenant:['local-user']}}),
  });
  const config = fixture.environment.TCODE_CONFIG_ROOT;
  fs.mkdirSync(config,{recursive:true});
  fs.writeFileSync(path.join(config,'tcode.json'),JSON.stringify({
    model:'fixture/desktop-fixture', provider:{fixture:{npm:'@ai-sdk/openai-compatible',name:'离线验收模型',
      options:{baseURL:'http://127.0.0.1:'+provider.address().port+'/v1',apiKey:'local-fixture'},
      models:{'desktop-fixture':{name:'离线验收模型'}}}},
    agent:{build:{permission:{'*':'deny',glob:'allow',read:'allow',write:'ask',network:'allow',artifact_upload:'allow',bash:'allow'}}}
  }),{mode:0o600});
  folders = ['scripts', 'data', 'reports'].map(name => {
    const dir = path.join(fixture.root, name);
    if (!resume) { fs.mkdirSync(dir); fs.writeFileSync(path.join(dir,'same.txt'),name); }
    return fs.realpathSync(dir);
  });
  if (!resume) {
    for (const dir of [folders[0],folders[2]]) for (const name of ['shortcut-check.txt','readonly-check.txt'])
      fs.writeFileSync(path.join(dir,name),path.basename(dir));
    for(const folder of folders) fs.writeFileSync(path.join(folder,'verification.txt'),folder===folders[1]?marker:'wrong folder');
    fs.mkdirSync(path.join(folders[2],'upload'));
    fs.writeFileSync(path.join(folders[2],'upload','evidence.txt'),'wrong attachment');
  }
  else marker = fs.readFileSync(path.join(folders[1],'verification.txt'),'utf8');
  return fixture;
};
const picks = resume ? [] : [0,1,2], permissions = resume ? [] : [0,1];
dialog.showOpenDialog = async (_window, options) => {
  assert(['打开项目文件夹','添加文件夹到当前项目'].includes(options.title));
  assert(picks.length); return {canceled:false, filePaths:[folders[picks.shift()]]};
};
dialog.showMessageBox = async (_window, options) => {
  if (['允许项目读写此文件夹','将此文件夹设为只读','解除此文件夹的关联','设为默认工作目录'].includes(options.message)) {
    assert(options.detail.includes(fixture.root)); return {response:1};
  }
  assert.equal(options.message,'允许这个项目访问所选文件夹？');
  assert(permissions.length); return {response: permissions.shift()};
};
let started = false;
const timeout = setTimeout(() => { console.error('FAIL: desktop did not complete the fixture'); app.exit(1); },60000);
app.on('browser-window-created', (_event, win) => {
  win.webContents.on('did-finish-load', async () => {
    if (started || !win.webContents.getURL().endsWith('/desktop.html')) return;
    started = true;
    try {
      const result = await win.webContents.executeJavaScript(`(async()=>{
        const desktop=window.tcodeDesktop;
        const wait=async fn=>{for(let i=0;i<120;i++){const value=fn();if(value)return value;await new Promise(r=>setTimeout(r,100));}throw Error('项目管理控件未就绪：'+fn)};
        const button=(name,container=document)=>[...container.querySelectorAll('button')].find(b=>b.offsetWidth && !b.disabled && b.textContent.trim()===name);
        (await wait(()=>button('新建任务'))).click();
        const modal=await wait(()=>document.getElementById('new-task-heading')?.closest('[role=dialog]') || document.getElementById('new-task-heading')?.closest('.fixed'));
        if (!${resume}) (await wait(()=>button('打开项目文件夹',modal))).click();
        (await wait(()=>[...modal.querySelectorAll('label')].find(l=>l.textContent.trim()==='scripts'))).click();
        (await wait(()=>button('管理',modal))).click();
        const panel=await wait(()=>modal.querySelector('section[aria-label="项目文件夹"]'));
        if (!${resume}) for(const name of ['data','reports']) {
          (await wait(()=>button('添加文件夹到项目',panel))).click();
          await wait(()=>[...panel.querySelectorAll('strong')].some(s=>s.textContent===name));
        }
        const wid=(await (await fetch('/api/workdirs')).json()).workdirs.find(d=>d.project_name==='scripts').name;
        (await wait(()=>button('关闭',modal))).click();
        const get=async(route, args={})=>{
          const res=await fetch(route+'?'+new URLSearchParams({workspace_id:wid,...args}));
          if(!res.ok)throw Error(await res.text()); return res.json();
        };
        const listing=await get('/api/files');
        const files=[];
        for(const root of listing.items){
          const children=await get('/api/files',{path:root.path});
          const file=children.items.find(i=>i.name==='same.txt');
          const value=await get('/api/files/content',{path:file.path});
          files.push([root.name,root.access,value.content]);
        }
        const search=await get('/api/files/search',{q:'same.txt'});
        let project = await desktop.projectFolders(wid);
        const report=project.roots.find(r=>r.name==='reports');
        if (${resume}) {
          if(project.default_id!==report.id || report.access!=='ro') throw Error('重启后授权或默认目录丢失');
          const denied=await fetch('/api/files/entry',{method:'PATCH',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({workspace_id:wid,path:'same.txt',name:'forbidden.txt'})});
          if(denied.status!==403) throw Error('只读目录应阻止重命名: '+denied.status);
          await desktop.projectFolders(wid,{action:'rw',id:report.id,revision:project.revision});
        } else {
          project=await desktop.projectFolders(wid,{action:'default',id:report.id,revision:project.revision});
          project=await desktop.projectFolders(wid,{action:'ro',id:'primary',revision:project.revision});
          const form=new FormData();
          form.append('file',new Blob([${JSON.stringify('attachment:'+marker)}]),'evidence.txt');
          const upload=await fetch('/api/files/upload?'+new URLSearchParams({workspace_id:wid}),{method:'POST',body:form});
          if(!upload.ok) throw Error(await upload.text());
          const stored=await upload.json();
          if(stored.display_path!=='/workspace/upload/evidence.txt' || (await get('/api/files/content',{path:stored.relative_path})).content!==${JSON.stringify('attachment:'+marker)}) throw Error('附件引用未指向原始内容');
        }
        return {files,search,wid};
      })()`);
      assert.deepEqual(result.files.sort(),[['data','ro','data'],['reports',resume?'ro':'rw','reports'],['scripts',resume?'ro':'rw','scripts']]);
      assert.equal(fs.readFileSync(path.join(folders[2],'same.txt'),'utf8'),'reports');
      assert.equal(new Set(result.search.items.map(i=>i.path)).size,resume?5:3);
      assert.deepEqual(picks,[]); assert.deepEqual(permissions,[]);
      if (!resume) {
        // Opening the primary same.txt above issued a real content-addressed snapshot.
        const managed=path.join(fixture.root,'runtime','workdirs','admin_tenant','local-user',result.wid);
        assert.equal(fs.readFileSync(path.join(managed,snapshotRel),'utf8'),'scripts');
        for(const [dir,rel,bytes] of [[managed,jobRel,'recovered:'+marker],[folders[2],jobRel,'wrong job'],[folders[2],snapshotRel,'wrong snapshot']]){
          const dest=path.join(dir,rel);fs.mkdirSync(path.dirname(dest),{recursive:true});fs.writeFileSync(dest,bytes);
        }
      }
      console.log('PASS: real Electron project open, two folder grants, same-name reads and cross-root search');
      {
        const run = js => win.webContents.executeJavaScript(js);
        await run(`localStorage.setItem('tcode.desktop.permission-mode','ask')`);
        await win.webContents.loadURL(win.webContents.getURL());
        await run(`(async()=>{
          async function wait(fn) {for(let i=0;i<150;i++){const v=fn();if(v)return v;await new Promise(r=>setTimeout(r,100))}throw Error('等待控件超时: '+fn)}
          const button=text=>[...document.querySelectorAll('button')].find(b=>b.offsetWidth && b.textContent.trim()===text);
          (await wait(()=>button('新建任务'))).click();
          (await wait(()=>[...document.querySelectorAll('label')].find(l=>l.textContent.trim()==='scripts'))).click();
          (await wait(()=>button('创建任务'))).click();
          await wait(()=>!document.querySelector('#new-task-heading'));
          const input=await wait(()=>[...document.querySelectorAll('textarea[placeholder*="下达新指令"]')].find(e=>e.offsetWidth));
          input.value=${JSON.stringify(resume?'重启回读验收：读取附件、报告、托管快照和恢复结果，上传两份文件，然后用 bash 测试 curl 下载审批撤权并给出两个结果链接。':'跨目录桌面验收：搜索 data 目录里的 verification.txt，读取后写入 reports 目录的 verified.txt，然后用 bash 把默认目录下的测试文件 shortcut-check.txt 移入废纸篓。')};
          input.dispatchEvent(new Event('input',{bubbles:true}));
          (await wait(()=>[...input.closest('form').querySelectorAll('button[type=submit]')].find(b=>b.offsetWidth && !b.disabled && b.textContent.includes('发送')))).click();
        })()`);
        await run(`(async()=>{
          const wid=${JSON.stringify(result.wid)}, desktop=window.tcodeDesktop;
          for(let i=0;i<280;i++){
            const panel=[...document.querySelectorAll('.permission-request')].find(p=>p.offsetWidth);
            if(panel){
              const args=JSON.parse(panel.querySelector('pre').textContent);
              const needle=${JSON.stringify(resume?'curl -o readonly-check.txt':'trash shortcut-check.txt')};
              const target=args.patterns?.some(p=>p.includes(needle) && p.includes(${JSON.stringify(folders[2])}));
              if(target){
                const project=await desktop.projectFolders(wid);
                if(project.roots.find(r=>r.id===project.default_id)?.path!==${JSON.stringify(folders[2])})throw Error('unexpected default');
                await desktop.projectFolders(wid,{action:${JSON.stringify(resume?'ro':'default')},id:${resume?'project.default_id':"'primary'"},revision:project.revision});
              }
              const once=[...panel.querySelectorAll('button')].find(b=>b.textContent.trim()==='仅一次');
              if(!once || !once.isConnected) throw Error('approval missing');
              once.click();
              if(target)return;
            }
            await new Promise(r=>setTimeout(r,100));
          }
          throw Error('未见带原目录的权限确认');
        })()`);
        const file=path.join(folders[2],'verified.txt');
        for(let i=0;i<150 && !fs.existsSync(file);i++) await new Promise(r=>setTimeout(r,150));
        if (!fs.existsSync(file)) console.error('Fixture chat:', await run('document.body.innerText.slice(-1800)'));
        assert.equal(fs.readFileSync(file,'utf8'),marker);
        await run(`(async()=>{for(let i=0;i<200;i++){if(document.body.innerText.includes(${JSON.stringify(resume?'重启回读通过：'+marker:'已完成本地文件验证。')}) && !document.querySelector('button[title="中止当前回合及其执行中的工作"]'))return;await new Promise(r=>setTimeout(r,100))}throw Error('聊天未显示最终回复或尚未结束')})()`);
        assert.deepEqual(calls,resume?['read','read','read','read','artifact_upload','artifact_upload','bash']:['glob','read','write','bash']);
        assert.equal(fs.readFileSync(path.join(folders[0],'shortcut-check.txt'),'utf8'),'scripts');
        assert.equal(fs.existsSync(path.join(folders[2],'shortcut-check.txt')),false);
        if (resume) {
          for (const dir of [folders[0],folders[2]]) assert.equal(fs.readFileSync(path.join(dir,'readonly-check.txt'),'utf8'),path.basename(dir));
          for (const [label,rel,content] of [['查看快照',snapshotRel,'scripts'],['查看异步产物',jobRel,'recovered:'+marker]]) {
            await run(`(async()=>{
              const link=[...document.querySelectorAll('a[data-local-path]')].find(a=>a.textContent===${JSON.stringify(label)});
              if(!link || link.dataset.localPath!==${JSON.stringify('/workspace/'+rel)}) throw Error('链接来源丢失');
              link.click();
              for(let i=0;i<80;i++){
                if([...document.querySelectorAll('.file-preview-panel pre')].some(p=>p.offsetWidth && p.textContent===${JSON.stringify(content)}))return;
                await new Promise(r=>setTimeout(r,100));
              }
              throw Error('预览没有显示目标文件内容');
            })()`);
          }
          console.log('PASS: revoked during curl confirmation; refused before publication and both managed previews matched');
          assert.deepEqual([...received],[['selected.txt','scripts'],['result.txt','recovered:'+marker]]);
          console.log('PASS: desktop chat read both managed files; local Artifact receiver verified both uploaded bodies, not default-directory namesakes');
          await run(`(async()=>{const wid=${JSON.stringify(result.wid)},desktop=window.tcodeDesktop;
            let p=await desktop.projectFolders(wid);const id=p.default_id;
            p=await desktop.projectFolders(wid,{action:'default',id:'primary',revision:p.revision});
            await desktop.projectFolders(wid,{action:'remove',id,revision:p.revision});
            const gone=await fetch('/api/files/content?'+new URLSearchParams({workspace_id:wid,path:'@roots/'+id+'/verified.txt'}));
            if(gone.status!==404) throw Error('旧路径仍能读取: '+gone.status);
          })()`);
          assert.equal(fs.readFileSync(file,'utf8'),marker);
          console.log('PASS: fresh chat after restart read the exact report; removing its grant preserved the file');
          return;
        }
        assert(sawOriginalTrashPath,'shortcut result lost the originally confirmed path');
        console.log('PASS: switched the default during confirmation; only the originally confirmed file was trashed and its full path returned');
        await run(`(async()=>{const id=${JSON.stringify(result.wid)}, desktop=window.tcodeDesktop;
          let p=await desktop.projectFolders(id);
          if(p.default_id!=='primary')throw Error('确认期间应已切换默认目录');
          p=await desktop.projectFolders(id,{action:'default',id:p.roots.find(r=>r.name==='reports').id,revision:p.revision});
          p=await desktop.projectFolders(id,{action:'ro',id:p.default_id,revision:p.revision});
          p=await desktop.projectFolders(id,{action:'rw',id:'primary',revision:p.revision});
          if(p.roots.find(r=>r.id===p.default_id)?.access!=='ro' || p.roots.find(r=>r.id==='primary')?.access!=='rw')throw Error('测试授权不匹配');
          const input=[...document.querySelectorAll('textarea[placeholder*="下达新指令"]')].find(e=>e.offsetWidth);
          input.value='普通 Bash 托管验收：用 bash 在副本中生成 same.txt，然后按命令回执读取内容。';
          input.dispatchEvent(new Event('input',{bubbles:true}));
          input.closest('form').requestSubmit();
          for(let i=0;i<220;i++){
            const panel=[...document.querySelectorAll('.permission-request')].find(p=>p.offsetWidth);
            if(panel)[...panel.querySelectorAll('button')].find(b=>b.textContent.trim()==='仅一次')?.click();
            if(document.body.innerText.includes(${JSON.stringify('已从托管回执读取：managed-shell-'+marker)}))break;
            if(i===219)throw Error('聊天没有读回普通 Bash 的托管结果');
            await new Promise(r=>setTimeout(r,100));
          }
          await desktop.projectFolders(id,{action:'ro',id:'primary',revision:p.revision});
        })()`);
        assert.equal(fs.readFileSync(path.join(folders[0],'same.txt'),'utf8'),'scripts');
        assert.equal(fs.readFileSync(path.join(folders[2],'same.txt'),'utf8'),'reports');
        assert.equal(fs.readFileSync(path.join(fixture.root,'runtime','workdirs','admin_tenant','local-user',result.wid,shellReceipt.slice('/workspace/'.length)),'utf8'),'managed-shell-'+marker);
        console.log('PASS: ordinary Bash receipt read through desktop chat; writable primary and read-only default namesakes unchanged');
      }
    } catch (error) { console.error(error, await win.webContents.executeJavaScript('document.body.innerText.slice(-2200)')); process.exitCode=1; }
    finally { clearTimeout(timeout); provider.close(); app.quit(); }
  });
});
process.argv.push('--test-instance');
provider.listen(0,'127.0.0.1',()=>require('../main.cjs'));
