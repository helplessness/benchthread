/** In-memory development host. Each instance represents one panel session. */
export function createMockPanel({
  grants = ['team.run', 'document.read', 'selection.share'],
  delayMs = 400,
  respond = turn => '模拟回答：' + turn.question,
  onShare = () => {},
} = {}) {
  const records = new Map();
  function requireGrant(grant) {
    if (!grants.includes(grant)) throw new Error('未授权 ' + grant);
  }
  function find(id) {
    const record = records.get(id);
    if (!record) throw new Error('运行记录不存在');
    return record;
  }
  return {
    async history() {
      const runs = Array.from(records.values(), record => ({...record.run}));
      return {runs: runs.reverse().slice(0, 32), grants: [...grants]};
    },
    async submit(input) {
      requireGrant('team.run');
      if (input.pdf || input.source_run) requireGrant('document.read');
      if (!input.request_id || !input.question?.trim()) throw new Error('需要提交标识和问题');
      const turn = {...input};
      const fingerprint = JSON.stringify(turn, Object.keys(turn).sort());
      const prior = records.get(turn.request_id);
      if (prior) {
        if (prior.fingerprint !== fingerprint) throw new Error('同一提交标识已用于其他输入');
        return {...prior.run};
      }
      const source = turn.source_run && find(turn.source_run).run;
      if (source && source.status !== 'completed') throw new Error('来源运行尚未完成');
      const run = {id: turn.request_id, question: turn.question, status: 'running',
        document_name: source ? source.document_name : (turn.document_name || ''),
        source_run: turn.source_run, item_id: turn.item_id, selection: turn.selection};
      const timer = setTimeout(async () => {
        try {
          const result = await respond(turn);
          if (run.status === 'running') Object.assign(run, {status: 'completed', result});
        } catch (error) {
          if (run.status === 'running') Object.assign(run, {status: 'failed', error: String(error.message ?? error)});
        }
      }, delayMs);
      records.set(run.id, {run, fingerprint, timer});
      return {...run};
    },
    async cancel(id) {
      const {run, timer} = find(id);
      clearTimeout(timer);
      if (run.status === 'running') run.status = 'cancelled';
      return {...run};
    },
    async share(selection) {
      requireGrant('selection.share');
      if (find(selection.run).run.status !== 'completed') throw new Error('运行尚未完成');
      if (!selection.text?.trim() || selection.text.length > 12000 ||
          !selection.item_id || selection.item_id.length > 160) throw new Error('无效的选中项');
      onShare({...selection});
      return true;
    },
  };
}
