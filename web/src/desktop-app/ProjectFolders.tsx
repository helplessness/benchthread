import FolderIcon from '../components/FolderIcon';
import { createResource, createSignal, createUniqueId, For, Show } from 'solid-js';
import { desktop, type ProjectFolderSet } from './bridge';
import './ProjectFolders.css';

export default function ProjectFolders(props: { workspace: string; changed: () => Promise<unknown> }) {
  const [inventory, { refetch, mutate }] = createResource(() => props.workspace, id => desktop!.projectFolders!(id));
  const [busy, setBusy] = createSignal(false);
  const [message, setMessage] = createSignal('');
  const disclosureGroup = createUniqueId();
  const change = async (id: string, action: 'ro'|'rw'|'default'|'remove'|'add') => {
    if (busy() || inventory.loading || !inventory.latest) return;
    const before = inventory.latest;
    setBusy(true); setMessage('');
    try {
      if (action === 'add') {
        const folder = await desktop!.addProjectFolder!(props.workspace);
        if (!folder) return;
        await refetch(); setMessage('已添加：' + folder);
      } else {
        const after: ProjectFolderSet = await desktop!.projectFolders!(props.workspace, {id, action, revision: before.revision});
        mutate(after);
        if (after.revision !== before.revision) setMessage(action === 'remove' ? '已解除关联，磁盘文件未删除。' : '目录设置已保存。');
      }
      await props.changed();
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setMessage(detail.replace(/^Error invoking remote method '[^']+': (?:Error: )?/, ''));
      void refetch();
    }
    finally { setBusy(false); }
  };
  return <section aria-label="项目文件夹" class="project-folders">
    <div class="pf-heading">
      <div><h3>项目文件夹 <Show when={!inventory.error && inventory.latest}>{data => <span>{data().roots.length}</span>}</Show></h3>
        <p>同一项目的所有对话共享这些目录。</p></div>
      <button type="button" class="pf-add" disabled={busy() || inventory.loading || !!inventory.error} onClick={() => void change('','add')}>
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M8 2v12M2 8h12"/></svg>添加文件夹到项目</button>
    </div>
    <Show when={inventory.loading && !inventory.latest}><p role="status">正在读取文件夹…</p></Show>
    <Show when={inventory.error}><p role="alert">无法读取目录。<button type="button" onClick={() => void refetch()}>刷新</button></p></Show>
    <Show when={!inventory.error && inventory.latest}>{project =>
      <div class="pf-list"><For each={project().roots} fallback={<p class="pf-empty">还没有关联文件夹。添加后即可在对话中引用文件。</p>}>{folder => <details class="pf-folder" name={disclosureGroup}>
        <summary title={folder.path}><span class="pf-icon"><FolderIcon/></span>
          <span class="pf-identity"><span class="pf-name"><strong>{folder.name}</strong>
            <Show when={folder.id === project().default_id}><span class="pf-default">默认目录</span></Show>
          </span><span class="pf-path" dir="ltr">{folder.path}</span></span>
          <span class="pf-access" data-available={folder.available}>{!folder.available ? '不可用' : folder.access === 'ro' ? '只读' : '读写'}</span>
          <svg class="pf-chevron" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m5 6 3 3 3-3"/></svg>
        </summary>
        <div class="pf-tools">
        <Show when={!folder.available}><p role="status">文件夹不可用或已被替换。请核对位置；不会自动授权同名目录。</p></Show>
        <button type="button" disabled={busy() || !folder.available} onClick={() => void change(folder.id, folder.access === 'ro' ? 'rw' : 'ro')}>{folder.access === 'ro' ? '允许读写' : '设为只读'}</button>
        <Show when={folder.id !== project().default_id}><button type="button" disabled={busy()} onClick={() => void change(folder.id,'default')}>设为默认</button></Show>
        <button type="button" class="pf-unlink" disabled={busy() || (folder.id === project().default_id && project().roots.length > 1)}
          title={folder.id === project().default_id && project().roots.length > 1 ? '请先将另一个文件夹设为默认' : '仅解除关联，不删除磁盘文件'}
          onClick={() => void change(folder.id,'remove')}>解除关联</button>
        </div>
      </details>}</For></div>}</Show>
    <p class="pf-note"><strong>权限范围</strong> 文件工具按授权读写原文件。Bash 和 Python 仍在隔离副本中运行。</p>
    <p class="pf-status" role="status">{busy() ? '正在处理…' : message()}</p>
  </section>;
}
