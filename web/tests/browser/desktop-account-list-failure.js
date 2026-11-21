// Desktop renderer with an isolated signed-in identity; no real account or backend writes.
const email = 'member@example.test';
let identityStatus = 200;
let releaseList;
const pendingList = new Promise(resolve => { releaseList = resolve; });
await page.route('**/api/**', async route => {
  const path = new URL(route.request().url()).pathname;
  if (path === '/api/auth/me') {
    return route.fulfill({status: identityStatus, json: {user: {
      user_id: 'member', tenant_id: 'fixture', tenant_status: 'active', email,
    }}});
  }
  if (path === '/api/auth/desktop-accounts') {
    await pendingList;
    if (identityStatus === 401) return route.fulfill({json: {accounts: [email], can_register: false, local_workspace: false}});
    return route.fulfill({status: 503, json: {detail: 'fixture failure'}});
  }
  return route.fulfill({json: {}});
});
const request = page.waitForRequest(req => new URL(req.url()).pathname === '/api/auth/desktop-accounts');
await page.goto('http://127.0.0.1:5178/desktop.html', {waitUntil: 'domcontentloaded'});
await request;
const workspace = page.locator('.studio-shell');
try {
  await workspace.waitFor({state: 'visible'});
} finally {
  releaseList();
}
await page.waitForFunction(() => performance.getEntriesByType('resource').some(
  entry => new URL(entry.name).pathname === '/api/auth/desktop-accounts' && entry.responseEnd > 0));
await workspace.waitFor({state: 'visible'});
identityStatus = 503;
await page.reload({waitUntil: 'domcontentloaded'});
await page.getByText('暂时无法打开本机工作区', {exact: true}).waitFor();
assert(await workspace.count() === 0);
identityStatus = 401;
await page.reload({waitUntil: 'domcontentloaded'});
await page.getByRole('heading', {name: '登录 Tcode'}).waitFor();
await page.getByLabel('最近登录的账号').selectOption(email);
assert(await workspace.count() === 0);
await page.getByRole('heading', {name: '登录 Tcode'}).waitFor();
