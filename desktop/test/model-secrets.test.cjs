const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const crypto=require('node:crypto');
const {modelSecrets,requestModelSettings}=require('../model-secrets.cjs');

test('profile-local encrypted vault persists and refuses insecure storage',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'tcode-vault-test-'));
  const key=crypto.randomBytes(32),iv=crypto.randomBytes(16);
  const safe={isEncryptionAvailable:()=>true,encryptString:text=>{
    const cipher=crypto.createCipheriv('aes-256-cbc',key,iv);return Buffer.concat([cipher.update(text),cipher.final()]);
  },decryptString:data=>{const cipher=crypto.createDecipheriv('aes-256-cbc',key,iv);return Buffer.concat([cipher.update(data),cipher.final()]).toString();}};
  try{
    const vault=modelSecrets(root,safe);
    const locked=modelSecrets(root,{...safe,isEncryptionAvailable:()=>false});
    assert.equal(locked.saveKey('NEW_KEY',''),undefined);
    assert.equal(fs.existsSync(path.join(root,'model-secrets.bin')),false);
    assert.throws(()=>locked.saveKey('NEW_KEY','test-only-secret'),/不可用/);
    assert.deepEqual(vault.saveKey('TEST_KEY','test-only-secret'),{});
    assert.equal(fs.readFileSync(path.join(root,'model-secrets.bin')).includes(Buffer.from('test-only-secret')),false);
    assert.equal(fs.statSync(path.join(root,'model-secrets.bin')).mode&0o777,0o600);
    assert.equal(modelSecrets(root,safe).read().TEST_KEY,'test-only-secret');
    assert.equal(locked.saveKey('TEST_KEY',undefined),undefined);
    assert.throws(()=>locked.saveKey('TEST_KEY',''),/不可用/);
    assert.equal(vault.saveKey('OTHER_KEY',''),undefined);
    const before=vault.saveKey('TEST_KEY','');
    assert.deepEqual(before,{TEST_KEY:'test-only-secret'});
    assert.equal(vault.read().TEST_KEY,'');
    vault.write(before);
    assert.equal(vault.read().TEST_KEY,'test-only-secret');
    assert.throws(()=>modelSecrets(root,{...safe,isEncryptionAvailable:()=>false}).write({}),/不可用/);
    assert.throws(()=>modelSecrets(root,{...safe,getSelectedStorageBackend:()=> 'basic_text'}).write({}),/不可用/);
  }finally{fs.rmSync(root,{recursive:true});}
});

// Exercise the same request handler used by Electron without a personal keychain.
function saveScenario(reply, {cookieError=false, value='replacement'}={}) {
  const events=[];
  let stored='original';
  const vault={
    saveKey(_,next){events.push('write');const old=stored;stored=next;return old;},
    write(old){events.push('restore');stored=old;}, available:()=>true,
  };
  const cookies={get:async()=>{events.push('cookies');if(cookieError)throw Error('cookies unavailable');return [];}};
  return requestModelSettings({action:'save',id:'fixture',api_key:value},vault,cookies,'http://localhost','fixture',
    async()=>{events.push('send');return reply();}).then(result=>({result,stored,events}));
}

test('cookie failure leaves the original credential untouched and never sends',async()=>{
  const {result,stored,events}=await saveScenario(()=>Response.json({}),{cookieError:true});
  assert.equal(result.ok,false);assert.doesNotMatch(result.message,/保存状态待确认/);
  assert.deepEqual(events,['cookies']);assert.equal(stored,'original');
});

test('explicit rejection restores even a cleared key before parsing the error body',async()=>{
  for(const body of ['not JSON',JSON.stringify({detail:'模型参数无效'})]){
    const {result,stored,events}=await saveScenario(()=>new Response(body,{status:400}),{value:''});
    assert.equal(result.ok,false);assert.equal(result.message,body.startsWith('{')?'模型参数无效':'模型配置未保存，请重试');
    assert.deepEqual(events,['cookies','write','send','restore']);assert.equal(stored,'original');
  }
});

test('ambiguous saves retain the attempted credential and do not resend',async()=>{
  for(const reply of [
    ()=>{throw new DOMException('timeout','TimeoutError');},
    ()=>{throw new TypeError('disconnected');},
    ()=>new Response(null,{status:408}),
    ()=>Response.json({detail:'internal error'},{status:500}),
    ()=>new Response('bad gateway',{status:502}),
    ()=>new Response('incomplete success body'),
  ]){
    const {result,stored,events}=await saveScenario(reply);
    assert.equal(result.ok,false);assert.match(result.message,/保存状态待确认/);
    assert.deepEqual(events,['cookies','write','send']);assert.equal(stored,'replacement');
  }
});
