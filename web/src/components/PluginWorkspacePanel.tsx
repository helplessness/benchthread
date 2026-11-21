import panelSDK from '../../../packages/tcode-panel-sdk/index.js?raw';
import {createEffect, createSignal, onCleanup, Show} from 'solid-js';

export type PanelDescriptor = {plugin:string; id:string; title:string; instance:string};

export default function PluginWorkspacePanel(props: {panel:PanelDescriptor; query:string; onShare:(text:string,file?:{name:string;path:string})=>void}) {
  let frame: HTMLIFrameElement | undefined;
  const [html, setHtml] = createSignal('');
  const [status, setStatus] = createSignal('正在打开插件面板…');
  createEffect(() => {
    const {plugin, id, instance} = props.panel;
    const url = '/api/plugins/panels/' + encodeURIComponent(plugin) + '/' + encodeURIComponent(id) + '?' + props.query + '&instance=' + encodeURIComponent(instance);
    const controller = new AbortController();
    onCleanup(() => controller.abort());
    setHtml('');
    const receive = async (event: MessageEvent) => {
      const sender = frame?.contentWindow;
      const msg = event.data;
      if (!sender || event.source !== sender || msg?.channel !== 'tcode.panel.v1' || typeof msg.id !== 'string') return;
      const runsURL = url.replace('?', '/runs?');
      const reply = (fields:Record<string, unknown>) => {
        if (!controller.signal.aborted && frame?.contentWindow === sender)
          sender.postMessage({channel:'tcode.panel.reply.v1', id:msg.id, ...fields}, '*');
      };
      try {
        const method = msg.method;
        if (!['submit','history','share','cancel'].includes(method)) throw new Error('未知的面板操作');
        const endpoint = method === 'share' ? url.replace('?', '/share?')
          : method === 'cancel' ? url.replace('?', '/runs/'+encodeURIComponent(msg.params.run)+'/cancel?') : runsURL;
        const response = await fetch(endpoint, method === 'history' ? {signal:controller.signal} : {
          method:'POST', signal:controller.signal, headers:{'Content-Type':'application/json'}, body:JSON.stringify(msg.params),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '面板请求未完成');
        if (method === 'share') {
          if (!controller.signal.aborted) props.onShare(data.text,data.file);
          reply({result:true});
        } else reply({result:data});
      } catch(error) {reply({error:error instanceof Error ? error.message : '请求失败'});}
    };
    window.addEventListener('message', receive);
    onCleanup(() => window.removeEventListener('message', receive));
    void fetch(url, {signal:controller.signal}).then(async response => {
      const result = await response.json();
      if (!response.ok || result.instance !== instance) throw new Error(result.detail || '面板已停用');
      if (controller.signal.aborted) return;
      const policy = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; form-action 'none'; base-uri 'none'";
      setHtml('<meta http-equiv="Content-Security-Policy" content="' + policy + '"><script>' + panelSDK + '</script>' + result.html);
    }).catch(error => { if (!controller.signal.aborted) setStatus(String(error.message)); });
  });
  return <Show when={html()} fallback={<p role="status">{status()}</p>}>
    <iframe ref={frame} title={props.panel.title} sandbox="allow-scripts" referrerpolicy="no-referrer" srcdoc={html()} style={{width:'100%',flex:'1',border:'0'}}/>
  </Show>;
}
