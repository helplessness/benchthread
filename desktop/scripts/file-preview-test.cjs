// Real Electron renderer, isolated fixture routes; no user profile or backend.
const { app, BrowserWindow } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'tcode-preview-test-'));
app.setPath('userData', profile);
const web = path.resolve(__dirname, '../../web');

app.whenReady().then(async () => {
  let server;
  const win = new BrowserWindow({ show: false, webPreferences: { sandbox: true } });
  try {
    const [docx, xlsx, pptx] = process.argv.slice(-3).map(file => fs.readFileSync(file));
    const samples = { 'a.docx': docx, 'a.xlsx': xlsx, 'a.pptx': pptx };
    const text = { 'a.md': '# Preview heading\n\n| A | B |\n| - | - |\n| 1 | 2 |\n\n<img src="bad" onerror="window.markdownRan=true">', 'a.csv': 'name,note\n"a,b",hello' };
    const { createServer } = await import(pathToFileURL(path.join(web, 'node_modules/vite/dist/node/index.js')));
    server = await createServer({ root: web, configFile: false, server: { host: '127.0.0.1', port: 0 }, plugins: [
      (await import(pathToFileURL(path.join(web, 'node_modules/vite-plugin-solid/dist/esm/index.mjs')))).default(),
      { name: 'file-preview-fixture',
        configureServer(vite) {
          vite.middlewares.use(async (req, res, next) => {
            const url = new URL(req.url, 'http://localhost');
            const file = url.searchParams.get('path');
            if (url.pathname === '/') {
              res.setHeader('Content-Type', 'text/html');
              res.end(await vite.transformIndexHtml('/', '<body><script type="module" src="/src/file-preview-fixture.tsx"></script></body>'));
            } else if (url.pathname === '/api/files') {
              res.setHeader('Content-Type', 'application/json');
              res.end(JSON.stringify({ items: [] }));
            } else if (url.pathname === '/api/files/content') {
              res.setHeader('Content-Type', 'application/json');
              res.end(JSON.stringify({ content: text[file] || '' }));
            } else if (samples[file] && ['/api/files/inline', '/api/files/download'].includes(url.pathname)) {
              res.end(samples[file]);
            } else next();
          });
        },
        resolveId(id) { if (id === '/src/file-preview-fixture.tsx') return path.join(web, id); },
        load(id) {
          if (!id.endsWith('/src/file-preview-fixture.tsx')) return;
          return `import {render} from 'solid-js/web';
import {createSignal} from 'solid-js';
import FilePreviewPanel from './components/FilePreviewPanel';
import FileBrowser from './components/FileBrowser';
const [file,setFile]=createSignal('a.md');
window.pickFile=setFile;
render(()=><><FilePreviewPanel path={file()}/><FileBrowser previewRequest={{path:file()}}/></>,document.body);`;
        },
      },
    ] });
    await server.listen();
    await win.loadURL(`http://127.0.0.1:${server.httpServer.address().port}/`);
    const run = js => win.webContents.executeJavaScript(js);
    const wait = async js => {
      for (let i = 0; i < 120; i++) {
        if (await run(js)) return;
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      throw Error(`${js}: ${await run('document.body.innerText.slice(0, 600)')}`);
    };
    await wait("document.querySelectorAll('article h1').length === 2");
    assert(await run("document.querySelectorAll('article table').length === 2 && !document.querySelector('article img') && !window.markdownRan"));
    await run("document.querySelector('button').click()");
    await wait("document.querySelector('pre')?.textContent.startsWith('# Preview heading')");
    await run("window.pickFile('a.csv'); undefined");
    await wait("Array.from(document.querySelectorAll('tbody tr > td:first-child')).filter(td => td.textContent === 'a,b').length === 2");
    const rendered = { 'a.docx': '.ofv-docx-document', 'a.xlsx': '.ofv-sheet', 'a.pptx': '.ofv-pptx-viewer' };
    for (const file of Object.keys(samples)) {
      await run(`window.pickFile(${JSON.stringify(file)}); undefined`);
      await wait(`document.querySelector('[aria-label=文件预览] ${rendered[file]}')?.getBoundingClientRect().height > 0 && document.querySelector('[data-testid=asset-browser] ${rendered[file]}')?.getBoundingClientRect().height > 0 && !document.body.innerText.includes('正在加载 Office 预览') && !document.querySelector('[role=alert]')`);
      assert(await run("!!document.querySelector('a[download]')"));
      console.log(`PASS Electron: ${file}`);
    }
    await run("window.pickFile('a.html'); undefined");
    await wait("document.querySelectorAll('iframe[sandbox=allow-scripts][src^=\"/api/files/html-preview?\"]').length === 2");
    console.log('PASS Electron: Markdown, source toggle, CSV and Office preview');
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  } finally {
    win.destroy();
    await server?.close();
    app.exit(process.exitCode || 0);
  }
});
