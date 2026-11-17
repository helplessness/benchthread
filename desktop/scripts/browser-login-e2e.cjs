// Real Electron renderer with deterministic HTTPS responses; no live credentials.
const assert = require('node:assert/strict');
const {app, BrowserWindow, WebContentsView, session} = require('electron');
const {DesktopBrowser} = require('../browser.cjs');
app.commandLine.appendSwitch('disable-background-networking');
app.on('window-all-closed', () => {}); // Keep the fixture alive between isolated phases.

app.whenReady().then(async () => {
  const browser = new DesktopBrowser({BrowserWindow, WebContentsView, session});
  const deadline = setTimeout(() => app.exit(1), 25000);
  // IP literal avoids DNS; all requests in this renderer are intercepted below.
  const origin = 'https://93.184.216.34';
  const original = browser.create.bind(browser);
  let content = 'Access denied';
  let visits = 0;
  const pendingAssets = [];
  const intercepted = new WeakSet();
  browser.create = async (...args) => {
    const page = await original(...args);
    const partition = page.contents.session;
    if (intercepted.has(partition)) return page;
    intercepted.add(partition);
    partition.protocol.handle('https', async request => {
      if (request.url === origin + '/login') return new Response('<input type="password">', {headers:{'content-type':'text/html'}});
      if (request.url === origin + '/blank') return new Response('<html></html>', {headers:{'content-type':'text/html'}});
      if (request.url === origin + '/slow.png') return new Promise(resolve => pendingAssets.push(resolve));
      if (request.url !== origin + '/protected') return new Response('', {status:404});
      visits++;
      if (!(await partition.cookies.get({url:origin, name:'test-access'})).length) {
        return Response.redirect(origin + '/login');
      }
      return new Response(`<main><h1>${content}</h1></main><img src="/slow.png">`, {headers:{'content-type':'text/html'}});
    });
    return page;
  };
  try {
    for (const [call, pageText] of [['bad', 'Access denied'], ['good', 'Protected fixture item']]) {
      content = pageText;
      let prompted;
      const login = new Promise(resolve => { prompted = resolve; });
      const run = browser.execute({owner:'fixture',call_id:call,args:{
        session:call, action:'open', url:origin + '/protected',
        verify_selector:'main h1', verify_text:'Protected fixture item',
      }}, prompted);
      const first = await Promise.race([login,run]);
      assert.equal(first.login_wait, true, JSON.stringify(first));
      const page = [...browser.pages.values()].find(item => item.session === call);
      await browser.control(call, 'takeover');
      await page.contents.session.cookies.set({url:origin, name:'test-access', value:'fixture'});
      if (call === 'good') {
        await browser.navigate(page.contents, origin + '/protected', {documentReady:true});
        assert.equal(page.contents.isLoading(), true);
      }
      const beforeReturn = visits;
      await browser.control(call, 'return_login');
      const result = await run;
      if (call === 'bad') assert.deepEqual(result, {error:'browser_login_unverified'});
      else {
        assert.equal(result.text, 'Protected fixture item');
        assert.equal(result.login_verified, true);
        assert.equal(visits, beforeReturn, 'return must verify the already-open page without reloading');
      }
    }
    const blank = await browser.execute({owner:'fixture', call_id:'blank', args:{session:'blank',action:'open',url:origin + '/blank'}});
    assert.deepEqual(blank, {error:'browser_document_empty'});
    await browser.reset();
    const profile = 'a'.repeat(64);
    const account = await browser.create('account:' + profile, {session:'account-' + profile}, profile);
    const partition = account.contents.session;
    await partition.cookies.set({url:origin, name:'test-access', value:'fixture'});
    await browser.action(account, {action:'close'});
    const task = await browser.create('task', {session:'task'}, profile);
    const other = await browser.create('other', {session:'other'}, 'b'.repeat(64));
    assert.equal((await task.contents.session.cookies.get({url:origin,name:'test-access'})).length, 1);
    assert.equal((await other.contents.session.cookies.get({url:origin,name:'test-access'})).length, 0);
    assert.deepEqual(await browser.manageAccount({account:'logout',profile}), {ok:true,state:'local_session_cleared'});
    assert.equal((await partition.cookies.get({url:origin,name:'test-access'})).length, 0);
    assert.equal(task.closed, true);
    assert.equal(other.closed, false);

    let signalPrompt;
    const prompt = new Promise(resolve => { signalPrompt = resolve; });
    const read = browser.execute({owner:'fixture',call_id:'channel-read',profile,args:{
      session:'channel',action:'open',url:origin+'/protected',channel_read:{selector:'main h1', limit:1},
    }}, signalPrompt);
    assert.equal((await Promise.race([prompt,read])).login_wait, true);
    const channel = [...browser.pages.values()].find(page => page.session === 'channel');
    await browser.control('channel','takeover');
    await channel.contents.session.cookies.set({url:origin, name:'test-access', value:'fixture'});
    assert.deepEqual(await browser.control('channel','status'), {success:true,control_mode:'human',waiting_login:true});
    await browser.control('channel','return_login');
    const evidence = await read;
    assert.deepEqual(evidence.items,[{url:origin+'/protected',title:'Protected fixture item'}]);
    assert.equal(evidence.login_verified,true);
    assert.throws(() => browser.control('channel','return_login'), /browser_login_not_pending/);
    console.log('PASS: Electron verifies structured same-session return, rejects duplicate return, isolates profiles and clears only the selected one');
  } catch (error) { console.error(error); process.exitCode = 1; }
  finally {
    clearTimeout(deadline);
    for (const resolve of pendingAssets) resolve(new Response('', {status:404}));
    await browser.stop();
    app.exit(process.exitCode || 0);
  }
});
