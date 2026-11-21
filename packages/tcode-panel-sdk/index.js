// TCode injects this API into each isolated panel document.
(() => {
  const pending = new Map();
  window.addEventListener('message', event => {
    if (event.source !== parent || event.data?.channel !== 'tcode.panel.reply.v1') return;
    const waiter = pending.get(event.data.id);
    if (!waiter) return;
    pending.delete(event.data.id);
    if (event.data.error) waiter.reject(new Error(event.data.error));
    else waiter.resolve(event.data.result);
  });
  function request(method, params = {}) {
    const id = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      pending.set(id, {resolve, reject});
      parent.postMessage({channel:'tcode.panel.v1', id, method, params}, '*');
    });
  }
  window.tcodePanel = Object.freeze({
    history: () => request('history'),
    submit: turn => request('submit', turn),
    cancel: run => request('cancel', {run}),
    share: selection => request('share', selection),
  });
})();
