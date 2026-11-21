// Launch with Electron. All credentials, service data and runtime settings are temporary.
const {app} = require('electron');
app.commandLine.appendSwitch('remote-debugging-port','0');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const {spawn, execFileSync} = require('node:child_process');
const {randomBytes} = require('node:crypto');
const net = require('node:net');
const root = path.resolve(__dirname, '../..');
const python = path.join(root, '.venv/bin/python');
const sdk = path.join(root, 'packages/artifact-sdk');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'personal-services-'));
const env = {PATH:process.env.PATH, HOME:temp, PYTHONPATH:path.join(sdk,'src')};
const instances = require('../test-instance.cjs'), create = instances.createTestInstance;
const children = [];
process.on('exit', () => children.forEach(p => p.kill()));
async function port() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const n = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return n;
}
async function start(name, combined) {
  const p = await port(), dir = path.join(temp,name), base = 'http://127.0.0.1:'+p;
  const tokens = ['alice','bob'].map(user => execFileSync(python, ['-m','tcode_artifacts.server','users','issue',user,'--data-dir',dir],{env,encoding:'utf8'}).trim());
  const args = combined ? [path.join(sdk,'examples/mcp_server.py'),'--with-artifact'] : ['-m','tcode_artifacts.server','serve'];
  const child = spawn(python,[...args,'--data-dir',dir,'--auth','personal-tokens','--service-id',name,'--port',String(p)],{env,stdio:'ignore'});
  children.push(child);
  for (let i=0;i<100;i++) {
    if (child.exitCode !== null) throw Error('Service exited: '+name);
    const response = await fetch(base+'/artifact/health',{headers:{Authorization:'Bearer '+tokens[0]}}).catch(()=>null);
    if (response?.ok) break;
    if (i===99) throw Error('Service not ready: '+name);
    await new Promise(r=>setTimeout(r,100));
  }
  let input;
  for (let i=0;i<2;i++){
    const filename = ['alice-visible.txt','bob-private.txt'][i];
    const form = new FormData(); form.append('file',new Blob([name+':'+filename]),filename);
    const r = await fetch(base+'/artifact/api/v1/files/by-path?path='+filename,{method:'PUT',body:form,headers:{Authorization:'Bearer '+tokens[i]}});
    if (!r.ok) throw Error('Seed failed: '+r.status);
    if (!i) input=(await r.json()).ref;
  }
  return {base,token:tokens[0],input};
}
(async()=>{
  const files=await start('standalone',false), mcp=await start('combined',true);
  // Only the model is scripted. TCode still discovers, authorizes and executes tools.
  const provider=require('node:http').createServer(async(req,res)=>{
    let raw=''; for await(const chunk of req) raw+=chunk;
    const body=JSON.parse(raw), messages=body.messages||[];
    const last=messages.findLastIndex(m=>m.role==='user');
    const prompt=JSON.stringify(messages[last]?.content||'');
    const tools=(body.tools||[]).map(t=>t.function?.name||'');
    const browse=prompt.includes('列出'), echo=prompt.includes('回显');
    const fileEcho=echo&&prompt.includes('文件');
    const name=tools.find(n=>browse?n==='artifact_browse':echo&&n.endsWith(fileEcho?'echo_file':'echo_text'));
    const call=name&&!messages.slice(last+1).some(m=>m.role==='tool');
    const args=browse?{service_id:prompt.includes('combined')?'combined':'standalone',directory:''}:fileEcho?{input_file:mcp.input}:{text:'desktop-personal-echo'};
    const message=call?{role:'assistant',tool_calls:[{id:'fixture-call',type:'function',function:{name,arguments:JSON.stringify(args)}}]}:{role:'assistant',content:'本轮测试结束。'};
    const finish=call?'tool_calls':'stop';
    if(body.stream){
      const delta=call?{...message,tool_calls:[{index:0,...message.tool_calls[0]}]}:message;
      const chunk=(delta,finish_reason)=>'data: '+JSON.stringify({id:'local',object:'chat.completion.chunk',model:'offline',choices:[{index:0,delta,finish_reason}]})+'\n\n';
      res.writeHead(200,{'Content-Type':'text/event-stream'});res.end(chunk(delta,null)+chunk({},finish)+'data: [DONE]\n\n');
    }else{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({id:'local',model:'offline',choices:[{index:0,message,finish_reason:finish}]}))}
  });
  await new Promise(r=>provider.listen(0,'127.0.0.1',r));
  const password=randomBytes(24).toString('hex');
  instances.readTestLogin=()=>({email:'sdk@example.test',password,userId:'fixture'});
  instances.createTestInstance=(...args)=>{
    const instance=create(...args), dir=instance.environment.TCODE_CONFIG_ROOT;
    fs.mkdirSync(dir,{recursive:true});
    Object.assign(instance.environment,{TCODE_AUTH_REQUIRED:'0',TCODE_DESKTOP_DEV_LOCAL_SERVICES:'1',
      TCODE_ARTIFACT_IMPORT_ACCESS:JSON.stringify({standalone:{admin_tenant:['local-user']}}),
      TCODE_DESKTOP_PAUSE_REMOTE_SERVICES:'0',FIXTURE_STANDALONE:files.token,FIXTURE_COMBINED:mcp.token});
    fs.writeFileSync(path.join(dir,'desktop-mcp.json'),JSON.stringify({
      $desktop:{managed_job_protocol:false,artifact_services:{
        standalone:{url:files.base+'/artifact',token_env:'FIXTURE_STANDALONE'}},
        artifact_default_service_id:'standalone',artifact_target_services:{}},
      'sdk-echo':{url:mcp.base+'/mcp',enabled:true,invocation_mode:'direct',headers:{Authorization:'Bearer ${FIXTURE_COMBINED}'}}
    }),{mode:0o600});
    fs.writeFileSync(path.join(dir,'tcode.json'),JSON.stringify({model:'fixture/offline',provider:{fixture:{
      npm:'@ai-sdk/openai-compatible',name:'本机测试模型',options:{baseURL:'http://127.0.0.1:'+provider.address().port+'/v1',apiKey:'fixture'},
      models:{offline:{name:'本机测试模型'}}}}}),{mode:0o600});
    return instance;
  };
  process.argv.push('--test-instance');
  require('../main.cjs');
})().catch(e=>{console.error(e.message);app.quit()});
