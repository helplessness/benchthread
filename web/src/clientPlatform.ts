import type { BrowserLivePanelProps } from './components/BrowserLivePanel';
import type { AsyncCompletionEvent } from './api/generated/protocol';
import { createContext, useContext, type Component } from 'solid-js';

export interface WorkspaceFileActionsProps {
  workspaceId?: string;
  projectBound: boolean;
  editingFolders: boolean;
  toggleFolders: () => void;
  uploading: boolean;
  canUpload: boolean;
  pickFolder: (files: File[]) => void;
  run: (action: () => Promise<string | void | null>) => Promise<void>;
}

export type DownloadResult = { url: string; state: 'completed' | 'cancelled' | 'interrupted' };

export type ConnectNotifications = (
  identity: string,
  isCurrent: () => boolean,
  format: (event: AsyncCompletionEvent) => { title: string; body: string },
  open: (event: AsyncCompletionEvent) => void,
) => { subscribe: () => () => void; sync: (events: AsyncCompletionEvent[]) => Promise<void> };

export type ConnectBrowserTabs = (
  active: () => string,
  toggleTab: (session: string, open: boolean) => void,
) => { label: (session: string) => string; close: (session: string) => void; reopen: () => void };

export interface ClientPlatform {
  surface: 'desktop' | 'web';
  artifactConfigUrl: string;
  serviceStatusUrl: (retry: boolean) => string;
  openProject?: () => Promise<{ name: string } | null>;
  ProjectFolders?: Component<{ workspace: string; changed: () => Promise<unknown> }>;
  WorkspaceFileActions?: Component<WorkspaceFileActionsProps>;
  FileLocationActions?: Component<{
    workspaceId: string;
    path: string;
    menu?: boolean;
    copyPath?: boolean;
    onAction: (action: () => Promise<unknown>) => void;
  }>;
  resolveFileLocation?: (workspaceId: string, path: string) => Promise<{ path: string }>;
  copyText?: (text: string) => Promise<{ success: boolean }>;
  onDownloadResult?: (callback: (result: DownloadResult) => void) => () => void;
  connectBrowserTabs?: ConnectBrowserTabs;
  BrowserPanel?: Component<BrowserLivePanelProps>;
  ModelSettings?: Component<{ onChanged: () => void }>;
  InternetContentSettings?: Component<{ workspaceId?: string }>;
  connectNotifications?: ConnectNotifications;
}

export const webPlatform: ClientPlatform = {
  surface: 'web',
  artifactConfigUrl: '/api/artifact-services/config',
  serviceStatusUrl: () => '/api/artifact-services',
};

export const ClientPlatformContext = createContext<ClientPlatform>(webPlatform);
export const useClientPlatform = () => useContext(ClientPlatformContext);
