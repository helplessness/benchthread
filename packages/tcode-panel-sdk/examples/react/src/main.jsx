import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {panel, preview} from './panel.js';

function Assistant() {
  const [question, setQuestion] = useState('');
  const [runs, setRuns] = useState([]);
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const running = runs.some(run => run.status === 'running');
  async function refresh() { setRuns((await panel.history()).runs); }
  useEffect(() => {
    const read = () => refresh().catch(error => setNotice(error.message));
    read();
    if (running) {
      const timer = setInterval(read, 500);
      return () => clearInterval(timer);
    }
  }, [running]);
  async function act(action) {
    setBusy(true);
    setNotice('');
    try { await action(); await refresh(); }
    catch (error) { setNotice(error.message); }
    finally { setBusy(false); }
  }
  return <main><h1>问题助手</h1>{preview && <p>本地模拟模式</p>}
    <label>问题 <input value={question} onChange={e => setQuestion(e.target.value)}/></label>
    <button disabled={busy || !question.trim()} onClick={() =>
      act(() => panel.submit({request_id: crypto.randomUUID(), question}))
    }>提问</button>
    <p role="status">{notice}</p>
    {runs.map(run => <article key={run.id}><h2>{run.question}</h2>
      <p>{run.result || run.error || run.status}</p>
      {run.status === 'running' && <button disabled={busy} onClick={() => act(() => panel.cancel(run.id))}>取消</button>}
      {run.status === 'completed' && <button disabled={busy} onClick={() => act(async () => {
        await panel.share({run: run.id, item_id: 'answer', text: run.result});
        setNotice(preview ? '模拟对话草稿：' + run.result : '已带入对话草稿');
      })}>带入对话</button>}</article>)}
  </main>;
}
createRoot(document.getElementById('app')).render(<Assistant/>);
