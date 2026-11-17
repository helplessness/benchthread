// TCode-owned Chromium renderer. No preload, host cookies, files, or native IPC.
const net = require('node:net');
const dns = require('node:dns').promises;
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');

function publicAddress(address) {
  if (net.isIP(address) === 4) {
    const [a, b] = address.split('.').map(Number);
    return !(a === 0 || a === 10 || a === 127 || a >= 224 || a === 169 && b === 254
      || a === 172 && b >= 16 && b <= 31 || a === 192 && [0, 168].includes(b)
      || a === 100 && b >= 64 && b <= 127 || a === 198 && [18, 19, 51].includes(b)
      || a === 203 && b === 0);
  }
  return net.isIP(address) === 6 && /^[23][0-9a-f]{3}:/i.test(address)
    && !/^2001:(db8|0):/i.test(address);
}
async function allowedURL(value) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('browser_url_blocked');
  const hostname = url.hostname.replace(/^\[|\]$/g, '').replace(/\.$/, '');
  if (/^(localhost)$/i.test(hostname) || /\.(local|localhost|internal)$/i.test(hostname)) throw new Error('browser_url_blocked');
  const addresses = net.isIP(hostname) ? [{ address: hostname }] : await dns.lookup(hostname, { all: true });
  if (!addresses.length || addresses.some(item => !publicAddress(item.address))) throw new Error('browser_url_blocked');
  return url.href;
}

const LOGIN_FORM = `Array.from(document.querySelectorAll('input[type=password],input[autocomplete=one-time-code],input[autocomplete=username]')).some(el => el.getClientRects().length > 0)`;
function looksLikeLogin(value) {
  try {
    const {hostname, pathname, searchParams} = new URL(value);
    if (hostname === 'x.com' && pathname.startsWith('/i/jf/onboarding/') && searchParams.get('mode') === 'login') return true;
    return /^(login|signin|passport|sso)\./i.test(hostname) || /\/(login|sign[-_]?in|log[-_]?in|authwall|sso)(\/|$)/i.test(pathname);
  } catch { return false; }
}

class DesktopBrowser {
  constructor({ BrowserWindow, WebContentsView, session, dialog, shell, Menu }) {
    this.dialog = dialog; this.shell = shell; this.Menu = Menu;
    this.BrowserWindow = BrowserWindow; this.WebContentsView = WebContentsView; this.session = session;
    this.pages = new Map(); this.accountSessions = new Map(); this.accountBusy = new Set(); this.secret = crypto.randomBytes(32).toString('hex');
    this.root = fs.mkdtempSync(path.join(os.tmpdir(), 'tcode-browser-'));
    fs.chmodSync(this.root, 0o700); this.socket = path.join(this.root, 'bridge.sock');
    this.server = net.createServer(socket => {
      let buffer = ''; socket.setTimeout(12 * 60 * 1000, () => socket.destroy());
      socket.on('error', () => {});
      socket.on('data', chunk => {
        buffer += chunk;
        if (buffer.length > 65536) return socket.destroy();
        if (!buffer.includes('\n')) return;
        socket.removeAllListeners('data');
        let request;
        try {
          request = JSON.parse(buffer.split('\n')[0]);
          const token = Buffer.from(String(request.token || ''));
          const expected = Buffer.from(this.secret);
          if (token.length !== expected.length || !crypto.timingSafeEqual(token, expected)) throw new Error('unauthorized');
        } catch { socket.end(JSON.stringify({ error: 'unauthorized' }) + '\n'); return; }
        const controller = new AbortController();
        socket.once('close', () => controller.abort());
        const progress = request.login_confirmation === true ? value => socket.write(JSON.stringify(value) + '\n') : undefined;
        const result = request.account ? this.manageAccount(request) : this.execute(request, progress, controller.signal);
        result.then(result => socket.end(JSON.stringify(result) + '\n'))
          .catch(error => socket.end(JSON.stringify({ error: String(error.message).slice(0, 300) }) + '\n'));
      });
    });
  }
  async start() {
    await new Promise((resolve, reject) => { this.server.once('error', reject); this.server.listen(this.socket, resolve); });
    fs.chmodSync(this.socket, 0o600);
  }
  startupPayload() { return { socket_path: this.socket, secret: this.secret }; }
  async openBlank(sessionName) {
    if (typeof sessionName !== 'string' || !/^manual-browser-[\w-]{1,145}$/.test(sessionName)) throw new Error('invalid_browser_scope');
    const key = crypto.createHash('sha256').update('desktop-ui:' + sessionName).digest('hex');
    const page = await this.create(key, { session: sessionName });
    await page.contents.loadURL('about:blank');
  }
  async reset() {
    for (const page of this.pages.values()) { this.detach(page); page.closed = true; if (page.view && !page.contents.isDestroyed()) page.contents.close(); if (!page.window.isDestroyed()) page.window.destroy(); }
    this.pages.clear();
    await Promise.all([...this.accountSessions.values()].map(session => session.clearStorageData()));
    this.accountSessions.clear();
  }
  async stop() { await this.reset(); await new Promise(resolve => this.server.close(resolve)); fs.rmSync(this.root, { recursive: true, force: true }); }
  control(sessionName, action) {
    if (typeof sessionName !== 'string' || !/^[\w.-]{1,160}$/.test(sessionName) || !['takeover', 'release', 'return_login', 'cancel_login', 'status'].includes(action)) throw new Error('invalid_browser_control');
    const matches = [...this.pages.values()].filter(item => item.session === sessionName && !item.closed && !item.window.isDestroyed());
    if (matches.length > 1) throw new Error('ambiguous_browser_session');
    const page = matches[0];
    if (!page) throw new Error('browser_session_missing');
    if (action === 'status') return Promise.resolve({success: true, control_mode: page.takenOver ? 'human' : 'agent', waiting_login: !!page.loginWait});
    if (action === 'cancel_login') {
      if (!page.loginWait) throw new Error('browser_login_not_pending');
      page.loginWait(false);
      return this.control(sessionName, 'release');
    }
    if (action === 'return_login') {
      if (!page.loginWait || !page.takenOver) throw new Error('browser_login_not_pending');
      this.control(sessionName, 'release');
      page.loginWait(true);
      return Promise.resolve({ success: true, control_mode: 'agent' });
    }
    if (action === 'takeover' && page.takenOver) return Promise.resolve({ success: true, control_mode: 'human' });
    const run = () => {
      if (page.closed || page.window.isDestroyed()) throw new Error('browser_session_closed');
      page.takenOver = action === 'takeover';
      if (page.takenOver) {
        page.agentControl = new Promise(resolve => { page.resumeAgent = resolve; });
      } else {
        page.resumeAgent?.(); page.resumeAgent = null; page.agentControl = Promise.resolve();
      }
      if (!page.takenOver) { this.detach(page); page.revision++; page.context = null; }
      return { success: true, control_mode: page.takenOver ? 'human' : 'agent' };
    };
    if (action === 'release' && page.takenOver) return Promise.resolve(run());
    const next = page.tail.then(run, run);
    page.tail = next.catch(() => {});
    return next;
  }
  input(sessionName, payload) {
    if (typeof sessionName !== 'string' || !/^[\w.-]{1,160}$/.test(sessionName) || !payload || typeof payload !== 'object') throw new Error('invalid_browser_input');
    const page = [...this.pages.values()].reverse().find(item => item.session === sessionName && !item.closed && !item.window.isDestroyed());
    if (!page) throw new Error('browser_session_missing');
    const run = async () => {
      if (!page.takenOver) throw new Error('browser_human_control_required');
      const wc = page.contents || page.window.webContents;
      const action = String(payload.action || '');
      if (page.loginBlocking && action === 'observe') return { success: true };
      if (action === 'observe') {
        return this.action(page, { action: 'observe' });
      } else if (action === 'click') {
        const x = Number(payload.x); const y = Number(payload.y);
        if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || y < 0 || x > 10000 || y > 10000) throw new Error('invalid_browser_input');
        wc.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount: 1 });
        wc.sendInputEvent({ type: 'mouseUp', x, y, button: 'left', clickCount: 1 });
      } else if (action === 'scroll') {
        const deltaX = Number(payload.deltaX || 0); const deltaY = Number(payload.deltaY || 0);
        if (!Number.isFinite(deltaX) || !Number.isFinite(deltaY)) throw new Error('invalid_browser_input');
        wc.sendInputEvent({ type: 'mouseWheel', x: 0, y: 0, deltaX: Math.max(-2000, Math.min(2000, deltaX)), deltaY: Math.max(-2000, Math.min(2000, deltaY)) });
      } else if (action === 'key') {
        const key = String(payload.key || '').slice(0, 64);
        const keyCode = ({ Enter: 'Return', ArrowDown: 'Down', ArrowUp: 'Up', ArrowLeft: 'Left', ArrowRight: 'Right' })[key] || key;
        if (!keyCode) throw new Error('invalid_browser_input');
        wc.sendInputEvent({ type: 'keyDown', keyCode }); wc.sendInputEvent({ type: 'keyUp', keyCode });
      } else if (action === 'text') {
        const value = String(payload.text || '').slice(0, 4000);
        if (!value) throw new Error('invalid_browser_input');
        await wc.insertText(value);
      } else throw new Error('invalid_browser_input');
      return page.loginBlocking ? { success: true } : { success: true, ...await this.action(page, { action: 'observe' }) };
    };
    const next = page.tail.then(run, run); page.tail = next.catch(() => {}); return next;
  }
  async manageAccount({account, profile, url}) {
    if (!/^[a-f0-9]{64}$/.test(profile) || !['login','logout'].includes(account)) throw new Error('invalid_browser_account');
    if (this.accountBusy.has(profile)) throw new Error('browser_account_busy');
    this.accountBusy.add(profile);
    try {
      if (account === 'logout') {
        for (const page of [...this.pages.values()]) {
          if (page.profile === profile) await this.action(page, {action:'close'});
        }
        const isolated = this.accountSessions.get(profile);
        if (isolated) {
          await isolated.clearStorageData();
          await isolated.clearCache();
          this.accountSessions.delete(profile);
        }
        return {ok:true, state:'local_session_cleared'};
      }
      await allowedURL(url);
      const key = 'account:' + profile;
      let page = this.pages.get(key);
      if (!page) {
        page = await this.create(key, {session:'account-' + profile}, profile);
        const host = new URL(url).hostname;
        const sameSite = host.replace(/^(www|passport)\./, '');
        const renderHost = value => page.window.setTitle(`TCode 登录 — ${new URL(value).hostname || host}`);
        page.window.setTitle(`TCode 登录 — ${host}`);
        for (const event of ['will-navigate', 'will-redirect']) {
          page.contents.on(event, (navigation, target) => {
            const next = new URL(typeof target === 'string' ? target : navigation.url);
            if (next.protocol !== 'https:' || next.hostname !== sameSite && !next.hostname.endsWith('.' + sameSite)) {
              navigation.preventDefault();
            }
          });
        }
        page.contents.on('did-navigate', (_event, value) => renderHost(value));
      }
      page.takenOver = true;
      page.window.show();
      // Opening an interactive window is not proof the user is authenticated.
      void this.navigate(page.contents, url).catch(() => {});
      return {ok:true, state:'login_window_opened'};
    } finally {
      this.accountBusy.delete(profile);
    }
  }
  async create(key, args, profile = '') {
    if (this.pages.size >= 4) throw new Error('browser_session_limit: close an existing browser session');
    const existing = profile && this.accountSessions.get(profile);
    const isolated = existing || this.session.fromPartition(`tcode-browser-${crypto.randomBytes(16).toString('hex')}`, { cache: false });
    if (profile) this.accountSessions.set(profile, isolated);
    isolated.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
    isolated.setPermissionCheckHandler(() => false);
    if (!existing) isolated.on('will-download', (event, item) => {
      if (profile) return event.preventDefault();
      if (!page.takenOver || !page.downloadsEnabled) return event.preventDefault();
      const record = { id: crypto.randomUUID(), name: item.getFilename(), state: 'progressing', received: 0, total: item.getTotalBytes() };
      page.downloads.unshift(record); page.downloads.length = Math.min(page.downloads.length, 50);
      item.on('updated', (_event, state) => { record.state = state; record.received = item.getReceivedBytes(); });
      item.once('done', (_event, state) => { record.state = state; record.path = item.getSavePath(); });
    });
    isolated.webRequest.onBeforeRequest((details, callback) => {
      // Data/blob resources remain inside the sandbox; never allow them as a document.
      if (['image', 'font', 'stylesheet'].includes(details.resourceType) && /^(data|blob):/.test(details.url)) return callback({});
      allowedURL(details.url).then(() => callback({})).catch(() => callback({ cancel: true }));
    });
    const window = new this.BrowserWindow({ title: 'Tcode 浏览器', width: 1200, height: 850, show: false,
      webPreferences: { session: isolated, sandbox: true, contextIsolation: true, nodeIntegration: false,
        webSecurity: true, allowRunningInsecureContent: false, devTools: false } });
    const view = this.WebContentsView ? new this.WebContentsView({ webPreferences: { session: isolated, sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true, devTools: false, backgroundThrottling: false } }) : null;
    const contents = view ? view.webContents : window.webContents;
    if (view) { window.contentView.addChildView(view); view.setBounds({ x: 0, y: 0, width: 1200, height: 850 }); }
    contents.on('will-attach-webview', event => event.preventDefault());
    const page = { window, view, contents, profile, host: null, session: args.session, revision: 0, context: null, closed: false, takenOver: false, forceClose: false, fitWidth: false, history: [], downloads: [], downloadsEnabled: false, navigationPending: false, navigationError: '', agentControl: Promise.resolve(), resumeAgent: null, results: new Map(), tail: Promise.resolve() };
    contents.setWindowOpenHandler(({ url }) => this.openInNewPage(page, url));
    window.on('close', event => {
      if (page.forceClose) return;
      event.preventDefault();
      if (key.startsWith('account:')) { void this.action(page, {action:'close'}); return; }
      page.takenOver = false; page.resumeAgent?.(); page.resumeAgent = null;
      window.hide();
    });
    window.on('closed', () => { page.closed = true; this.pages.delete(key); if (!profile) void isolated.clearStorageData(); });
    contents.on('did-navigate', (_event, _url, status) => { page.httpStatus = status; page.context = null; page.revision++; });
    contents.on('did-navigate-in-page', () => { page.revision++; });
    contents.on('did-finish-load', () => {
      page.history.unshift({ url: contents.getURL(), title: contents.getTitle(), time: Date.now() });
      page.history.length = Math.min(page.history.length, 100);
      if (page.fitWidth && page.host) void this.fit(page).catch(() => {});
    });
    contents.debugger.attach('1.3');
    this.pages.set(key, page);
    return page;
  }
  detach(page) {
    if (page.host && !page.host.isDestroyed()) page.host.contentView.removeChildView(page.view);
    page.host = null;
  }
  async fit(page) {
    page.contents.setZoomFactor(1);
    const width = await this.evaluate(page, 'Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0, innerWidth)');
    page.contents.setZoomFactor(Math.max(0.25, Math.min(1, page.view.getBounds().width / width)));
  }
  async navigate(wc, url, { documentReady = false } = {}) {
    let timer, onReady, expired = false, started = false;
    try {
      const opening = allowedURL(url).then(target => {
        if (expired) return;
        started = true;
        if (!documentReady) return wc.loadURL(target);
        return new Promise((resolve, reject) => {
          onReady = resolve;
          wc.once('dom-ready', onReady);
          wc.loadURL(target).then(resolve, reject);
        });
      });
      await Promise.race([opening, new Promise((_, reject) => {
        timer = setTimeout(() => {
          expired = true;
          if (started && !wc.isDestroyed()) wc.stop();
          reject(new Error('browser_navigation_timeout: 页面加载超时，请检查网络或换个网址重试'));
        }, 30000);
      })]);
    } finally {
      clearTimeout(timer);
      if (onReady) wc.removeListener('dom-ready', onReady);
    }
  }
  openInNewPage(page, url) {
    if (page.host && !page.host.isDestroyed()) page.host.webContents.send('desktop:browser-new-tab', url);
    return { action: 'deny' };
  }
  async panel(sessionName, host, payload) {
    const page = [...this.pages.values()].reverse().find(item => item.session === sessionName && !item.closed);
    const action = payload.action;
    if (!page || !page.view || (!page.takenOver && !['state', 'detach', 'close'].includes(action))) throw new Error('browser_human_control_required');
    const wc = page.contents;
    if (action === 'close') return this.action(page, {action:'close'});
    if (action === 'menu') {
      const selection = await new Promise(resolve => {
        const entry = (label, action) => ({ label, click: () => resolve(action) });
        const menu = this.Menu.buildFromTemplate([
          entry('在页面中查找', 'find'), entry('打印', 'print'), { type: 'separator' },
          { label: `缩放 ${Math.round(wc.getZoomFactor() * 100)}%`, submenu: [entry('缩小', 'zoom-out'), entry('重置为 100%', 'zoom-reset'), entry('放大', 'zoom-in'), entry('适应宽度', 'fit')] },
          entry('保存页面截图', 'screenshot'), { type: 'separator' },
          entry('下载', 'downloads'), entry('历史记录', 'history'), entry('浏览器设置', 'settings'),
        ]);
        const anchor = payload.bounds;
        const rendererZoom = host.webContents.getZoomFactor();
        const position = anchor && Number.isFinite(anchor.x) && Number.isFinite(anchor.y)
          ? { x: Math.round(Math.max(0, Math.min(host.getContentBounds().width, anchor.x * rendererZoom))), y: Math.round(Math.max(0, Math.min(host.getContentBounds().height, anchor.y * rendererZoom))) }
          : {};
        menu.popup({ window: host, ...position, callback: () => resolve(null) });
      });
      if (['print', 'screenshot', 'fit'].includes(selection)) return this.panel(sessionName, host, { action: selection });
      if (selection?.startsWith('zoom-')) return this.panel(sessionName, host, { action: 'zoom', factor: selection === 'zoom-reset' ? 1 : Math.max(.25, Math.min(2, wc.getZoomFactor() + (selection === 'zoom-in' ? .1 : -.1))) });
      return { ...await this.panel(sessionName, host, { action: 'state' }), menuAction: selection };
    }
    if (action === 'attach') {
      const bounds = payload.bounds;
      if (!bounds || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(bounds[key]) && bounds[key] >= 0 && bounds[key] <= 20000)) throw new Error('invalid_browser_bounds');
      if (page.host !== host) { this.detach(page); host.contentView.addChildView(page.view); page.host = host; }
      for (const other of this.pages.values()) if (other !== page && other.host === host) this.detach(other);
      const scale = host.webContents.getZoomFactor();
      const [width, height] = host.getContentSize();
      const x = Math.min(width, Math.round(bounds.x * scale)); const y = Math.min(height, Math.round(bounds.y * scale));
      const nextBounds = { x, y, width: Math.max(0, Math.min(width - x, Math.round(bounds.width * scale))), height: Math.max(0, Math.min(height - y, Math.round(bounds.height * scale))) };
      const changed = Object.keys(nextBounds).some(key => page.view.getBounds()[key] !== nextBounds[key]);
      if (changed) page.view.setBounds(nextBounds);
      page.view.setVisible(true);
      if (page.fitWidth && changed) await this.fit(page);
    } else if (action === 'detach') this.detach(page);
    else if (action === 'navigate') { page.navigationError = ''; await this.navigate(wc, payload.url); }
    else if (action === 'back') { if (wc.navigationHistory.canGoBack()) wc.navigationHistory.goBack(); }
    else if (action === 'forward') { if (wc.navigationHistory.canGoForward()) wc.navigationHistory.goForward(); }
    else if (action === 'reload') wc.reload();
    else if (action === 'zoom') {
      if (!Number.isFinite(payload.factor) || payload.factor < 0.25 || payload.factor > 2) throw new Error('invalid_browser_zoom');
      page.fitWidth = false;
      wc.setZoomFactor(payload.factor);
    } else if (action === 'fit') { page.fitWidth = true; await this.fit(page); }
    else if (action === 'find') {
      const text = String(payload.text || '').slice(0, 200);
      if (text) wc.findInPage(text, { forward: payload.forward !== false }); else wc.stopFindInPage('clearSelection');
    } else if (action === 'print') await new Promise((resolve, reject) => wc.print({}, (ok, reason) => ok ? resolve() : reject(new Error(reason || 'print_cancelled'))));
    else if (action === 'screenshot') {
      const image = await wc.capturePage();
      const saved = await this.dialog.showSaveDialog(host, { defaultPath: 'browser-screenshot.png', filters: [{ name: 'PNG', extensions: ['png'] }] });
      if (!saved.canceled && saved.filePath) await fs.promises.writeFile(saved.filePath, image.toPNG());
    } else if (action === 'downloads-enabled') page.downloadsEnabled = payload.enabled === true;
    else if (action === 'reveal-download') {
      const item = page.downloads.find(item => item.id === payload.id && item.state === 'completed');
      if (!item?.path) throw new Error('download_not_ready');
      this.shell.showItemInFolder(item.path);
    } else if (action === 'clear-data') {
      await wc.session.clearStorageData(); await wc.session.clearCache(); page.history = []; wc.reload();
    } else if (action !== 'state') throw new Error('invalid_browser_panel_action');
    return { url: wc.getURL(), title: wc.getTitle(), loading: wc.isLoading() || page.navigationPending, error: page.navigationError, canGoBack: wc.navigationHistory.canGoBack(), canGoForward: wc.navigationHistory.canGoForward(), zoom: wc.getZoomFactor(), fitWidth: page.fitWidth, history: page.history, downloads: page.downloads.map(({ path, ...item }) => item), downloadsEnabled: page.downloadsEnabled };
  }
  async evaluate(page, expression) {
    if (!page.context) {
      const { frameTree } = await this.command(page, 'Page.getFrameTree');
      const result = await this.command(page, 'Page.createIsolatedWorld', { frameId: frameTree.frame.id, worldName: 'tcode-browser-control' });
      page.context = result.executionContextId;
    }
    const result = await this.command(page, 'Runtime.evaluate', { contextId: page.context, expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error('browser_dom_action_failed: observe again before retrying');
    return result.result.value;
  }
  async readJSON(page, target) {
    if (target && page.contents.getURL() !== target) {
      page.httpStatus = undefined;
      await this.navigate(page.contents, target, {documentReady:true});
      if (page.contents.getURL() !== target || page.httpStatus !== 200) return null;
    }
    return this.evaluate(page, `(()=>{try{
      if(!/json/i.test(document.contentType))return null;
      const value=document.body?.innerText;
      return value?.length<524288?JSON.parse(value):null;
    }catch{return null;}})()`);
  }
  async bilibiliSubtitle(page, rule, video, afterLogin) {
    const cid = video?.data?.pages?.[0]?.cid;
    if (video?.code !== 0 || video.data.bvid !== rule.bvid || !Number.isSafeInteger(cid) || cid <= 0) {
      return {error:'browser_channel_no_evidence'};
    }
    const prefix = 'https://api.bilibili.com';
    const info = await this.readJSON(page, `${prefix}/x/player/wbi/v2?bvid=${rule.bvid}&cid=${cid}`);
    const tracks = info?.code === 0 && info?.data?.subtitle?.subtitles;
    if (!Array.isArray(tracks) || !tracks.length) {
      const auth = await this.readJSON(page, prefix + '/x/web-interface/nav');
      if (auth?.data?.isLogin === false && [-101,0].includes(auth?.code)) {
        return {login_required:true, login_url:rule.login_url, error:'browser_login_required'};
      }
      return {error:'browser_channel_no_evidence'};
    }
    const url = `https://www.bilibili.com/video/${rule.bvid}/`;
    const items = [];
    for (const track of tracks.slice(0, Math.min(rule.limit,2))) {
      let target;
      try { target = new URL(track.subtitle_url, prefix); } catch { continue; }
      if (target.protocol !== 'https:' || target.username || target.password || target.port ||
          !['aisubtitle.hdslb.com','subtitle.hdslb.com'].includes(target.hostname)) continue;
      const captions = await this.readJSON(page, target.href);
      if (!Array.isArray(captions?.body)) continue;
      const text = captions.body.filter(line => typeof line?.content === 'string').map(line => line.content).join('\n').trim().slice(0,12000);
      if (text) items.push({url,title:String(video.data.title || rule.bvid).slice(0,200),text,data:{
        kind:'platform_caption', language:String(track.lan || '').slice(0,40), part:1,
      }});
    }
    return items.length ? {url,title:String(video.data.title || rule.bvid).slice(0,200),page_revision:page.revision,items,partial:true,
      ...(afterLogin ? {login_verified:true} : {})} : {error:'browser_channel_no_evidence'};
  }
  async command(page, method, params = {}) {
    let timer;
    try {
      return await Promise.race([(page.contents || page.window.webContents).debugger.sendCommand(method, params), new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('browser_observation_timeout: 页面观察超时，未能确认本次操作结果')), 8000);
      })]);
    } finally { clearTimeout(timer); }
  }
  async execute(request, progress, signal) {
    const { args = {}, owner, call_id: callId, profile = '' } = request;
    if (typeof owner !== 'string' || !owner || owner.length > 512 || typeof args.session !== 'string' || !/^[\w.-]{1,160}$/.test(args.session)) throw new Error('invalid_browser_scope');
    if (profile && ((!args.channel_read && args.action !== 'close') || !/^[a-f0-9]{64}$/.test(profile) || this.accountBusy.has(profile))) throw new Error('invalid_browser_account');
    const key = crypto.createHash('sha256').update(owner + ':' + args.session + (profile ? ':' + profile : '')).digest('hex');
    let page = this.pages.get(key);
    if (!page) {
      if (!['open', 'navigate'].includes(args.action)) throw new Error('browser_session_missing: open first');
      await allowedURL(args.url);
      if (profile && this.accountBusy.has(profile)) throw new Error('browser_account_busy');
      page = await this.create(key, args, profile);
    }
    if (page.loginBlocking) return { error: 'browser_login_pending' };
    const run = async () => {
      if (page.loginBlocking) return { error: 'browser_login_pending' };
      if (page.closed || page.window.isDestroyed()) throw new Error('browser_session_closed');
      if (!callId || typeof callId !== 'string') throw new Error('browser_call_id_required');
      if (page.results.has(callId)) return page.results.get(callId);
      // Record before dispatch: replay after an unknown outcome must never repeat an input.
      page.results.set(callId, { error: 'browser_action_outcome_unknown: observe before any new action' });
      while (page.results.size > 100) page.results.delete(page.results.keys().next().value);
      const result = await this.action(page, args);
      if (result.login_required && progress && ['open', 'navigate'].includes(args.action)) page.loginBlocking = true;
      page.results.set(callId, result); return result;
    };
    const next = page.tail.then(run, run); page.tail = next.catch(() => {});
    let timer;
    let result;
    try {
      result = await Promise.race([next, new Promise((_, reject) => {
        timer = setTimeout(() => {
          const error = new Error('browser_observation_timeout: 浏览器操作超时，未能确认结果；本次浏览器会话已停止');
          page.closed = true; this.detach(page); this.pages.delete(key);
          if (page.view && !page.contents.isDestroyed()) page.contents.close();
          if (!page.window.isDestroyed()) page.window.destroy();
          reject(error);
        }, 11 * 60 * 1000);
      })]);
    } finally { clearTimeout(timer); }
    if (!result.login_required || !progress || !['open', 'navigate'].includes(args.action)) return result;
    const target = new URL(args.url);
    const cancel = () => page.loginWait?.(false);
    const deadline = Date.now() + 10 * 60 * 1000;
    try {
      if (target.origin + target.pathname === result.login_page) return result;
      while (!signal?.aborted && !page.closed && Date.now() < deadline) {
        if (result.login_url) {
          await this.navigate(page.contents, result.login_url, {documentReady:true});
          if (signal?.aborted || page.closed) return {error:'browser_login_cancelled'};
        }
        const returned = await new Promise(resolve => {
          page.loginWait = resolve;
          timer = setTimeout(cancel, Math.max(0, deadline - Date.now()));
          signal?.addEventListener('abort', cancel, {once:true});
          progress({ login_wait: true, site: target.hostname });
        });
        clearTimeout(timer);
        signal?.removeEventListener('abort', cancel);
        if (!returned || signal?.aborted || page.closed) return {error:'browser_login_cancelled'};
        if (page.takenOver) continue;
        const loaded = await this.action(page, {action:page.contents?.getURL() === target.href ? 'observe' : 'navigate', url:target.href,
          verify_selector:args.verify_selector, verify_text:args.verify_text, channel_read:args.channel_read}, target.href);
        if (signal?.aborted || page.closed) return {error:'browser_login_cancelled'};
        if (loaded.login_required) continue;
        if (loaded.error) return loaded;
        page.results.set(callId, loaded);
        return loaded;
      }
      return {error:'browser_login_cancelled'};
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', cancel);
      page.loginWait = null;
      page.loginBlocking = false;
      if (page.takenOver && !page.closed) await this.control(page.session, 'release');
    }
  }
  async action(page, args, verifyTarget) {
    const wc = page.contents || page.window.webContents;
    const action = args.action;
    if (action === 'close') { page.loginWait?.(false); page.resumeAgent?.(); page.resumeAgent = null; this.detach(page); page.closed = true; for (const [key, item] of this.pages) if (item === page) this.pages.delete(key); if (page.view) wc.close(); page.forceClose = true; page.window.close(); return { closed: true }; }
    if (page.takenOver && !['observe', 'snapshot', 'screenshot', 'text', 'get', 'wait'].includes(action)) {
      await page.agentControl;
      if (page.closed) throw new Error('browser_session_closed');
    }
    if (['open', 'navigate'].includes(action)) {
      page.httpStatus = undefined;
      await this.navigate(wc, args.url, { documentReady: true });
    }
    else if (!['observe', 'snapshot', 'screenshot', 'text', 'get', 'wait', 'fill', 'type', 'click', 'press', 'scroll', 'back', 'forward', 'reload'].includes(action)) throw new Error('unsupported_desktop_browser_action');
    else if (['fill', 'type', 'click', 'press', 'scroll'].includes(action)) {
      const embeddedRevision = /^r(\d+)-e\d+$/.exec(args.ref || '');
      const revision = args.page_revision ?? (embeddedRevision ? Number(embeddedRevision[1]) : null);
      if (args.ref && revision !== page.revision) throw new Error(`stale_ref: 页面已更新，请先 observe，再使用新元素编号与 page_revision=${page.revision}，不要原样重试`);
      if (!args.ref && !args.selector && !['scroll', 'press'].includes(action)) throw new Error('browser_target_required');
      const target = args.ref ? `globalThis.tcodeRefs?.get(${JSON.stringify(args.ref)})` : args.selector ? `document.querySelector(${JSON.stringify(args.selector)})` : 'document.activeElement';
      const risk = await this.evaluate(page, `(()=>{const el=${target};if(!el?.isConnected)return 'stale_ref';
        const label=[el.innerText,el.getAttribute('aria-label'),el.name,el.type].filter(Boolean).join(' ');
        if(/password|credit|payment|购买|支付|转账|删除|发送|发布|签署|同意协议|captcha|验证码/i.test(label))return 'human_review_required';
        if(['fill','type','press'].includes(${JSON.stringify(action)}) && !/search|搜索|搜一搜|查找/i.test([el.type,el.placeholder,el.getAttribute('aria-label'),el.form?.getAttribute('role')].join(' ')) && !/^(q|wd|query|search|keyword)$/i.test(el.name||''))return 'human_review_required';
        return '';})()`);
      if (risk && action !== 'scroll') throw new Error(risk + ': this browser action requires user takeover');
      await this.evaluate(page, `(async()=>{const el=${target};if(!el?.isConnected)throw new Error('stale target');
        if(${JSON.stringify(action)}==='click')el.click();
        else if(['fill','type'].includes(${JSON.stringify(action)})){el.focus();const value=${JSON.stringify(String(args.text || ''))};
          const setter=Object.getOwnPropertyDescriptor(el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value')?.set;
          if(!setter)throw new Error('not an input');setter.call(el,${action === 'type' ? 'el.value+value' : 'value'});el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));}
        else if(${JSON.stringify(action)}==='press')el.focus();
        else if(${JSON.stringify(action)}==='scroll')window.scrollBy(${Number(args.delta_x) || 0},${Number(args.delta_y) || 600});})()`);
      if (action === 'press') {
        const keys = { Enter: 'Return', Return: 'Return', Tab: 'Tab', Escape: 'Escape', Backspace: 'Backspace', ArrowDown: 'Down', ArrowUp: 'Up' };
        const keyCode = keys[args.key]; if (!keyCode) throw new Error('unsupported_browser_key');
        wc.sendInputEvent({ type: 'keyDown', keyCode }); wc.sendInputEvent({ type: 'keyUp', keyCode });
      }
      page.revision++;
    } else if (action === 'back' && wc.navigationHistory.canGoBack()) wc.navigationHistory.goBack();
    else if (action === 'forward' && wc.navigationHistory.canGoForward()) wc.navigationHistory.goForward();
    else if (action === 'reload') await this.navigate(wc, wc.getURL());
    else if (action === 'wait') await new Promise(resolve => setTimeout(resolve, /^\d+$/.test(String(args.text || '')) ? Math.min(Number(args.text), 15000) : 1000));
    // Observe after asynchronous navigation, without treating a dispatched click as proof.
    await new Promise(resolve => setTimeout(resolve, 500));
    if (!verifyTarget && !['open', 'navigate'].includes(action) && wc.isLoading()) await new Promise(resolve => {
      const timer = setTimeout(done, 15000);
      function done() { clearTimeout(timer); wc.removeListener('did-stop-loading', done); resolve(); }
      wc.once('did-stop-loading', done);
    });
    page.revision++;
    if ((verifyTarget || looksLikeLogin(wc.getURL())) && await this.evaluate(page, LOGIN_FORM)) {
      const url = new URL(wc.getURL());
      return {login_required:true, login_page:url.origin + url.pathname, error:'browser_login_required'};
    }
    if (args.channel_read?.json_kind && wc.getURL() === new URL(args.url).href) {
      const rule = args.channel_read;
      const data = await this.readJSON(page);
      if (rule.json_kind === 'bilibili_subtitle') return this.bilibiliSubtitle(page, rule, data, !!verifyTarget);
      if (page.httpStatus === 401 || page.httpStatus === 400 && String(data?.error_code) === '400016') {
        return {login_required:true, login_url:rule.login_url,error:'browser_login_required'};
      }
      if (page.httpStatus !== 200 || !data) return {error:'browser_channel_no_evidence'};
      const rows = rule.json_kind === 'hot_posts' ? data.list : data.data?.items;
      const items = [];
      for (const row of Array.isArray(rows) ? rows.slice(0, rule.limit) : []) {
        if (rule.json_kind === 'hot_posts') {
          let post;
          try { post = JSON.parse(row.data); } catch { continue; }
          if (!/^\/\d+\/\d+$/.test(post?.target)) continue;
          const text = String(post.text || post.description || '').replace(/<[^>]*>/g,' ').trim().slice(0,2000);
          if (text) items.push({url:'https://xueqiu.com'+post.target,title:String(post.title||text).slice(0,200),text});
        } else {
          const symbol = row?.code || row?.symbol;
          if (!/^[A-Z0-9.-]{1,16}$/.test(symbol) || typeof row.name !== 'string' || !row.name.trim()) continue;
          items.push({url:'https://xueqiu.com/S/'+symbol,title:row.name.slice(0,120),symbol});
        }
      }
      return items.length ? {url:wc.getURL(),title:'雪球',page_revision:page.revision,items,partial:true,
        ...(verifyTarget ? {login_verified:true} : {})} : {error:'browser_channel_no_evidence'};
    }
    if (['open', 'navigate'].includes(action) &&
        (!Number.isInteger(page.httpStatus) || page.httpStatus < 200 || page.httpStatus >= 300)) {
      return {error: Number.isInteger(page.httpStatus) ?
        `browser_document_http_error: HTTP ${page.httpStatus}` : 'browser_document_not_committed'};
    }
    if (args.channel_read) {
      const requested = new URL(args.url);
      const current = new URL(wc.getURL());
      const path = value => value.replace(/\/$/, '');
      if (current.origin !== requested.origin || path(current.pathname) !== path(requested.pathname)
          || [...requested.searchParams].some(([name, value]) => current.searchParams.get(name) !== value)
          || !Number.isInteger(page.httpStatus) || page.httpStatus < 200 || page.httpStatus >= 300) {
        return {error:'browser_channel_target_mismatch'};
      }
      const code = `(()=>{const rule=${JSON.stringify(args.channel_read)}, out=[], seen=new Set();
        const visible=el=>el?.getClientRects().length && getComputedStyle(el).visibility!=='hidden';
        for(const el of document.querySelectorAll(rule.selector)){
          if(!visible(el))continue;
          const tweet=rule.tweet?el.closest('article')?.querySelector('[data-testid="tweetText"]'):null;
          const title=(rule.tweet?(tweet?.innerText||''):(el.innerText||el.getAttribute('aria-label')||el.title||el.querySelector('img[alt]')?.alt||'')).trim().slice(0,300);
          if(!title)continue;
          const target=new URL(rule.link_host ? el.href : location.href);
          if(target.protocol!=='https:'||target.username||target.password)continue;
          if(rule.link_host && (target.hostname!==rule.link_host||target.port||!(new RegExp('^'+rule.link_path+'$')).test(target.pathname)))continue;
          const url=target.origin+target.pathname;
          const key=rule.link_host?url:title;
          if(seen.has(key))continue;
          const body=rule.body?Array.from(document.querySelectorAll(rule.body)).find(visible):null;
          const text=rule.body ? (body?.innerText||'').trim().slice(0,3600) : '';
          if(rule.body&&!text)continue;
          seen.add(key);out.push({url,title,...(text?{text}:{})});
          if(out.length>=rule.limit)break;
        }return out;})()`;
      const deadline = Date.now() + 4200;
      let items;
      do {
        items = await this.evaluate(page, code);
        if (items?.length) {
          return {url:current.origin+current.pathname+requested.search, title:wc.getTitle(),
            page_revision:page.revision, items, partial:true, ...(verifyTarget ? {login_verified:true} : {})};
        }
        await new Promise(resolve => setTimeout(resolve, 350));
      } while (Date.now() < deadline);
      return {error:'browser_channel_no_evidence'};
    }
    if (verifyTarget) {
      const selector = typeof args.verify_selector === 'string' ? args.verify_selector.trim() : '';
      const expected = typeof args.verify_text === 'string' ? args.verify_text.trim() : '';
      if (wc.getURL() !== verifyTarget || !Number.isInteger(page.httpStatus) ||
          page.httpStatus < 200 || page.httpStatus >= 300 || !selector || selector.length > 250 ||
          /^(html|body|\*)$/i.test(selector) || expected.length < 4 || expected.length > 250 ||
          !await this.evaluate(page, `(()=>{try {
            const node = document.querySelector(${JSON.stringify(selector)});
            return !!node?.getClientRects().length && node.innerText?.includes(${JSON.stringify(expected)});
          } catch { return false; }})()`)) {
        return {error:'browser_login_unverified'};
      }
      // Return only the evidence requested at the checkpoint, not account page
      // screenshots or unrelated form values seen right after sign-in.
      return {url:verifyTarget, title:wc.getTitle(), page_revision:page.revision, text:expected, login_verified:true};
    }
    const state = await this.evaluate(page, `(()=>{globalThis.tcodeRefs=new Map();const elements=[];
      for(const el of document.querySelectorAll('a,button,input,textarea,select,[role="button"]')){const r=el.getBoundingClientRect();if(!r.width||!r.height)continue;
        const ref='r'+${page.revision}+'-e'+elements.length;globalThis.tcodeRefs.set(ref,el);elements.push({ref,tag:el.tagName.toLowerCase(),text:(el.innerText||el.getAttribute('aria-label')||el.placeholder||'').slice(0,160),value:el.type==='password'?'[redacted]':String(el.value||'').slice(0,160),href:el.tagName==='A'?el.href.slice(0,180):undefined});if(elements.length>=200)break;}
      return {headings:Array.from(document.querySelectorAll('h1,h2,h3')).map(el=>el.innerText.trim()).filter(Boolean).slice(0,40),text:document.body?.innerText.slice(0,6000)||'',elements};})()`);
    if (['open', 'navigate'].includes(action) && !state.text?.trim() && !state.elements?.length) {
      return {error:'browser_document_empty'};
    }
    if (action === 'get') {
      const requested = String(args.text || '').toLowerCase();
      state.text = requested === 'url' ? wc.getURL() : requested === 'title' ? wc.getTitle() : JSON.stringify({ url: wc.getURL(), title: wc.getTitle() });
    } else if (action === 'text' && (args.ref || args.selector)) {
      const target = args.ref ? `globalThis.tcodeRefs?.get(${JSON.stringify(args.ref)})` : `document.querySelector(${JSON.stringify(args.selector)})`;
      state.text = await this.evaluate(page, `(()=>{const el=${target};return (el?.innerText||el?.textContent||'').trim().slice(0,18000)})()`);
    }
    let screenshot = ''; let screenshotWarning = '';
    try { screenshot = (await this.command(page, 'Page.captureScreenshot', { format: 'jpeg', quality: 65, fromSurface: true, captureBeyondViewport: false })).data; }
    catch { screenshotWarning = '截图未能及时返回；页面文字已读取，但不能据此声称已验证截图或视觉位置'; }
    return { url: wc.getURL(), title: wc.getTitle(), page_revision: page.revision, ...state, screenshot, screenshot_warning: screenshotWarning };
  }
}
module.exports = { DesktopBrowser, publicAddress, allowedURL };
