const fs = require('node:fs');
const path = require('node:path');

// Only the main process can decrypt this profile-local vault. Never use the
// Linux basic_text fallback, or write credentials into tcode.json/localStorage.
function modelSecrets(root, safeStorage) {
  const file = path.join(root, 'model-secrets.bin');
  function available() {
    return safeStorage.isEncryptionAvailable() && safeStorage.getSelectedStorageBackend?.() !== 'basic_text';
  }
  function read() {
    if (!fs.existsSync(file)) return {};
    if (!available()) throw new Error('系统密钥存储不可用，请解锁系统钥匙串后重试');
    try { return JSON.parse(safeStorage.decryptString(fs.readFileSync(file))); }
    catch { throw new Error('无法解锁模型密钥，请检查系统钥匙串'); }
  }
  function write(values) {
    if (!available()) throw new Error('系统密钥存储不可用，未保存密钥');
    fs.mkdirSync(root, { recursive: true });
    const temp = `${file}.tmp`;
    fs.writeFileSync(temp, safeStorage.encryptString(JSON.stringify(values)), { mode: 0o600 });
    fs.chmodSync(temp, 0o600);
    fs.renameSync(temp, file);
  }
  function saveKey(key, value) {
    if (value === undefined) return; // Keep the saved credential.
    const before = read();
    if (value === '' && !Object.hasOwn(before, key)) return;
    write({...before, [key]: value});
    return before; // Roll back only if the backend explicitly rejects the save.
  }
  return { read, write, saveKey, available };
}
async function requestModelSettings(payload, secrets, cookies, origin, token, send = fetch) {
  const saving = payload.action === 'save';
  const uncertain = {ok:false, message:'保存状态待确认，请重新打开模型设置核对后再操作。'};
  let dispatched = false;
  try {
    const jar = await cookies.get({url:origin});
    const options = {
      method:'POST', headers:{'Content-Type':'application/json', Cookie:jar.map(c=>`${c.name}=${c.value}`).join('; '), 'X-TCode-Desktop-Native':token},
      body:JSON.stringify(payload), signal:AbortSignal.timeout(30000),
    };
    let before;
    if (saving && payload.api_key !== undefined) {
      if (!/^[a-z0-9-]{1,64}$/.test(payload.id) || typeof payload.api_key !== 'string' || payload.api_key.length > 8192) throw new Error('密钥或服务标识无效');
      before = secrets.saveKey(`TCODE_DESKTOP_MODEL_${payload.id.replaceAll('-', '_').toUpperCase()}_KEY`, payload.api_key);
    }
    dispatched = true;
    const response = await send(`${origin}/api/desktop/model-settings`, options);
    if (!response.ok) {
      if (saving && (response.status < 400 || response.status >= 500 || response.status === 408)) return uncertain;
      dispatched = false;
      if (before !== undefined) {
        try { secrets.write(before); }
        catch { return {ok:false, message:'模型配置未保存，原密钥恢复失败，请检查系统密钥存储。'}; }
      }
      const value = await response.json().catch(()=>null);
      return {ok:false, message:typeof value?.detail === 'string' ? value.detail : '模型配置未保存，请重试'};
    }
    return {...await response.json(), secure_storage:secrets.available()};
  } catch {
    return saving && dispatched ? uncertain : {ok:false, message:'无法完成模型配置，请检查本地服务和系统密钥存储后重试'};
  }
}
module.exports = { modelSecrets, requestModelSettings };
