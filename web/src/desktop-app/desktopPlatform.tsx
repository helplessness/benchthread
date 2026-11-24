import { Show } from 'solid-js';
import type { ClientPlatform, WorkspaceFileActionsProps } from '../clientPlatform';
import { desktop } from './bridge';
import { connectDesktopBrowserTabs } from './browserTabs';
import DesktopBrowserPanel from './DesktopBrowserPanel';
import DesktopModelSettings from './DesktopModelSettings';
import InternetContentSettings from './InternetContentSettings';
import ProjectFolders from './ProjectFolders';
import { connectDesktopNotifications } from './notifications';

function WorkspaceFileActions(props: WorkspaceFileActionsProps) {
  let folderInput!: HTMLInputElement;
  return <>
    <Show when={desktop?.projectFolders && props.projectBound}>
      <button class="btn btn-sm" aria-expanded={props.editingFolders} onClick={props.toggleFolders}>编辑工作区</button>
    </Show>
    <Show when={desktop?.projectInput && props.projectBound && props.canUpload}>
      <button class="btn btn-sm" disabled={props.uploading}
        onClick={() => void props.run(() => desktop!.projectInput!(props.workspaceId!))}>选取项目输入</button>
    </Show>
    <Show when={props.workspaceId} keyed>{workspaceId => <>
      <button class="btn btn-sm" onClick={() => void props.run(() => desktop!.workspace(workspaceId, 'finder'))}>Finder</button>
      <button class="btn btn-sm" onClick={() => void props.run(() => desktop!.workspace(workspaceId, 'terminal'))}>终端</button>
    </>}</Show>
    <input ref={element => { folderInput = element; element.setAttribute('webkitdirectory', ''); }}
      type="file" multiple hidden aria-label="选择本地文件夹"
      onChange={event => {
        const files = Array.from(event.currentTarget.files || []);
        event.currentTarget.value = '';
        props.pickFolder(files);
      }}/>
    <button class="btn btn-sm" disabled={!props.canUpload || props.uploading} onClick={() => folderInput.click()}>导入文件夹</button>
  </>;
}

export const desktopPlatform: ClientPlatform = {
  surface: 'desktop',
  artifactConfigUrl: '/api/desktop/services/config',
  serviceStatusUrl: retry => `/api/desktop/services?retry=${retry}`,
  openProject: desktop?.openProject,
  ProjectFolders: desktop?.projectFolders ? ProjectFolders : undefined,
  WorkspaceFileActions: desktop ? WorkspaceFileActions : undefined,
  FileLocationActions: desktop && (props => <>
    <Show when={desktop!.revealFile}>
      <button class={props.menu ? undefined : 'btn btn-sm'}
        onClick={() => props.onAction(() => desktop!.revealFile(props.workspaceId, props.path))}>
        {props.menu ? '在 Finder 中显示' : '在文件夹中显示'}
      </button>
    </Show>
    <Show when={props.copyPath && desktop!.copyFilePath}>
      <button onClick={() => props.onAction(() => desktop!.copyFilePath(props.workspaceId, props.path))}>复制完整路径</button>
    </Show>
  </>),
  resolveFileLocation: desktop?.resolveFile,
  copyText: desktop?.copyText,
  onDownloadResult: desktop?.onDownloadResult,
  connectBrowserTabs: desktop ? connectDesktopBrowserTabs : undefined,
  BrowserPanel: desktop ? DesktopBrowserPanel : undefined,
  ModelSettings: desktop ? DesktopModelSettings : undefined,
  InternetContentSettings: desktop ? InternetContentSettings : undefined,
  connectNotifications: desktop ? connectDesktopNotifications : undefined,
};
