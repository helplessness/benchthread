// Read-only fresh-profile probe. Log document metadata, never HTML or cookies.
const { app, BrowserWindow, WebContentsView, session } = require('electron');
const { DesktopBrowser } = require('../browser.cjs');
const deadline = setTimeout(() => { console.log('probe deadline'); app.exit(1); }, 42000);
const clean = value => {
  const { origin, pathname } = new URL(value);
  return origin + pathname;
};
app.whenReady().then(async () => {
  console.log('electron ready');
  const browser = new DesktopBrowser({ BrowserWindow, WebContentsView, session });
  try {
    const page = await browser.create('probe', { session: 'probe' });
    const { contents } = page;
    const request = contents.session.webRequest;
    request.onBeforeRedirect(info => {
      if (info.resourceType === 'mainFrame') console.log('redirect', info.statusCode, clean(info.url), clean(info.redirectURL));
    });
    request.onResponseStarted(info => {
      if (info.resourceType === 'mainFrame') console.log('document', info.statusCode, clean(info.url), info.responseHeaders?.['content-type'] || '');
    });
    request.onErrorOccurred(info => {
      if (info.resourceType === 'mainFrame') console.log('document error', info.error);
    });
    console.log('browser ready', (await contents.session.resolveProxy('https://github.com/new')).split(';')[0].split(' ')[0]);
    try { await browser.navigate(contents, 'https://github.com/new', { documentReady: true }); }
    catch (error) { console.log('navigation', String(error.message).split(' (')[0].slice(0, 100)); }
    console.log('DOM', JSON.stringify(await browser.evaluate(page, `({
      ready:document.readyState, type:document.contentType, textLength:document.body?.innerText.trim().length ?? 0,
      passwordForm:!!document.querySelector('input[type=password]'), headings:document.querySelectorAll('main h1').length
    })`)));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
  finally { clearTimeout(deadline); await browser.stop(); app.exit(process.exitCode || 0); }
});
