import { useClientPlatform } from './clientPlatform';
import {hypothesisMapLink,type HypothesisFocus} from './api/hypothesisMap';
import { AppLoading, LazyPanelFallback, WorkspaceInitError } from './components/WorkspaceLoading';
import { displayWorkspacePath } from './utils/filePreview';
import { confirmAction, showFeedback } from './components/FeedbackDialog';
import { researchChangesLink, type ResearchChangeFocus } from './api/researchChanges';
import { listResearchProjects } from './api/research';
import {
  createSignal,
  onMount,
  Show,
  For,
  Suspense,
  createEffect,
  on,
  onCleanup,
  createMemo,
  createResource,
  lazy,
  untrack,
  type Component,
  type JSX,
} from 'solid-js';
import type { StudioShellProps } from './components/studioControls';
import PluginWorkspacePanel, {type PanelDescriptor} from './components/PluginWorkspacePanel';
import RunDirectory from './components/RunDirectory';
import { presentChatTurns } from './utils/chatPresentation';
import { isCollaborationPage, studioPageFromHash, studioPages, type StudioPage } from './utils/studioLayout';
import { isSideChatView, parseCompanionState, restoreSideChatTabs, settingsOnlyCompanionState, type CompanionView } from './utils/companionState';
import type { CollaborationTab } from './components/CollaborationPanel';
import { composerDrafts, composerKey } from './stores/composer';
import { readSideChatSelection, saveSideChatSelection, sideChatDraftKey } from './utils/sideChat';
import { activeRuntimeNotices, sessionWorkspaceId, type RuntimeNotice } from './utils/sessionRuntimeNotices';
import { mergeWithLocalSyntheticMessages } from './utils/messageSnapshots';
import type { StructureRequest } from './components/StructureWorkspace';
import { sessionStore, toVisibleAgentName } from './stores/session';
import type { SearchHit } from './api/globalSearch';
import { bootstrapSession, getHumanReviewQueue, getSession, getSessions, getSymphonyLocalIssues, getTeamCatalog, listScheduledNotifications, listWorkflowRuns, type AuthUser, type HumanReviewItem, type KanbanCard, type MCPServerStatus, type SymphonyLocalIssue, type WorkflowRun } from './api/client';
import { getTeamCatalogEntryByAgentName, hydrateTeamCatalog, isFixedTeamBootstrapAgent, usesFixedScheduler } from './utils/teamRegistry';
import { dismissDetailsOnOutsideClick } from './utils/dismissDetails';
import { installButtonFeedback } from './utils/buttonFeedback';
import TaskLifecycleStrip from './components/TaskLifecycleStrip';
import type { ResearchSessionTab } from './components/ResearchSessionPanel';
import { loadResearchNavigation, researchModuleVisible } from './utils/researchNavigation';
import { bindSessionToResearch } from './api/client';
import { availableSettingsSections, type SettingsSection } from './utils/settingsSections';
import { CITATION_OPEN_EVENT, type CitationOpenDetail, type CitationRef } from './utils/citations';
import { askSideQuestion, deleteSession, getSideChats, type SubagentItem } from './api/client';

// 应用入口只保留鉴权与页面骨架；会话、聊天在登录确认后加载，其他工作区
// 在用户打开时加载，避免 Wiki、研究、CodeMirror 等功能进入初始入口。
const ChatView = lazy(() => import('./components/ChatView'));
const MessageInput = lazy(() => import('./components/MessageInput'));
const SessionList = lazy(() => import('./components/SessionList'));
const AsyncCompletionNotifications = lazy(() => import('./components/AsyncCompletionNotifications'));
const CommandPalette = lazy(() => import('./components/CommandPalette'));
const FileBrowser = lazy(() => import('./components/FileBrowser'));
const CollaborationPanel = lazy(() => import('./components/CollaborationPanel'));
const SideChatWorkspace = lazy(() => import('./components/SideChatWorkspace'));
const SymphonyWorkspace = lazy(() => import('./components/SymphonyWorkspace'));
const PluginPanel = lazy(() => import('./components/PluginPanel'));
const ArchivedSessionsModal = lazy(() => import('./components/ArchivedSessionsModal'));
const KanbanWorkspace = lazy(() => import('./components/KanbanWorkspace'));
const SkillLibrary = lazy(() => import('./components/SkillLibrary'));
const ScheduledTasksWorkspace = lazy(() => import('./components/ScheduledTasksWorkspace'));
const PaperWikiWorkspace = lazy(() => import('./components/PaperWikiWorkspace'));
const ResearchWorkspace = lazy(() => import('./components/ResearchWorkspace'));
const ResourceLibraryWorkspace = lazy(() => import('./components/ResourceLibraryWorkspace'));
const PaperLibraryPreviewPanel = lazy(() => import('./components/PaperLibraryPreviewPanel'));
const ProteinRunsWorkspace = lazy(() => import('./components/ProteinRunsWorkspace'));
const HumanReviewPanel = lazy(() => import('./components/HumanReviewPanel'));
const WorkflowWorkspacePanel = lazy(() => import('./components/WorkflowWorkspacePanel'));
const StructureWorkspace = lazy(() => import('./components/StructureWorkspace'));
const FilePreviewPanel = lazy(() => import('./components/FilePreviewPanel'));
const OntologyWorkspace = lazy(() => import('./components/OntologyWorkspace'));
const EvidencePanel = lazy(() => import('./components/EvidencePanel'));

function WorkspaceSlot(props: { active: boolean; children: JSX.Element; scroll?: boolean }) {
  const [visited, setVisited] = createSignal(false);
  createEffect(() => { if (props.active) setVisited(true); });
  return <Show when={visited()}><div class={props.scroll ? 'studio-page studio-page-scroll' : 'studio-page'} hidden={!props.active}>{props.children}</div></Show>;
}

type SchedulerMode = 'supervisor' | 'chain' | 'swarm' | 'auto_swarm_builder';

type AppProps = {
  Shell: Component<StudioShellProps>;
  user: AuthUser;
  accountMenu: JSX.Element;
  openSettings?: (section?: SettingsSection) => void;
  SettingsTab?: Component<{workspaceId?: string; section: SettingsSection; onSectionChange: (section: SettingsSection) => void}>;
  SettingsWindow?: Component<{workspaceId?: string}>;
};

export default function App(props: AppProps) {
  const { surface, connectBrowserTabs } = useClientPlatform();
  const isDesktop = surface === 'desktop';
  onMount(() => onCleanup(installButtonFeedback()));
  const StudioShell = props.Shell;
  const { SettingsTab, SettingsWindow } = props;
  const [settingsSection, setSettingsSection] = createSignal<SettingsSection>('general', {equals: false});
  let sessionInfoDetails!: HTMLDetailsElement;
  onMount(() => onCleanup(dismissDetailsOnOutsideClick(() => sessionInfoDetails)));
  const initialPage = studioPageFromHash(window.location.hash);
  let startupSettingsPage = SettingsTab && (initialPage === 'mcp' || initialPage === 'settings') ? initialPage : undefined;
  const [mainSection, setMainSection] = createSignal<StudioPage>(startupSettingsPage ? 'console' : initialPage);
  const [researchSessionTab, setResearchSessionTab] = createSignal<'task' | ResearchSessionTab>('task');
  const [showHistoryModal, setShowHistoryModal] = createSignal(false);
  const [showCommandPalette, setShowCommandPalette] = createSignal(false);
  const [showSkillLibrary, setShowSkillLibrary] = createSignal(false);
  const [sideChats, setSideChats] = createSignal<SubagentItem[]>([]);
  const [creatingSideChat, setCreatingSideChat] = createSignal(false);
  const [closingSideChats, setClosingSideChats] = createSignal<string[]>([]);
  const [sideChatError, setSideChatError] = createSignal('');
  let sideChatSyncGeneration = 0;
  const [activeDiscussionSessionId, setActiveDiscussionSessionId] = createSignal('');
  const [collaborationTab, setCollaborationTab] = createSignal<CollaborationTab>('tasks');
  const [discussionOpen, setDiscussionOpen] = createSignal(false);
  const [inspectorRequest, setInspectorRequest] = createSignal(0);
  const [inspectorCloseRequest, setInspectorCloseRequest] = createSignal(0);
  const [inspectorVisible, setInspectorVisible] = createSignal(true);
  const [citationRequest, setCitationRequest] = createSignal<{ ref: CitationRef; key: number }>();
  let citationTrigger: HTMLElement | null = null;
  const previewView = (path: string): CompanionView => `preview:${encodeURIComponent(path)}`;
  const paperPreviewView = (id: string, title: string): CompanionView => `paper:${encodeURIComponent(JSON.stringify({id,title}))}`;
  const isPreviewView = (view: CompanionView) => view.startsWith('preview:');
  const isPaperPreviewView = (view: CompanionView) => view.startsWith('paper:');
  const isPageView = (view: CompanionView) => view.startsWith('page:');
  const companionPage = (view: CompanionView) => view.slice('page:'.length) as StudioPage;
  const previewPath = (view: CompanionView) => isPreviewView(view) ? decodeURIComponent(view.slice('preview:'.length)) : '';
  const previewPaper = (view: CompanionView): {id:string;title:string} => {
    if (!isPaperPreviewView(view)) return {id:'',title:''};
    try { return JSON.parse(decodeURIComponent(view.slice('paper:'.length))); } catch { return {id:'',title:'文献'}; }
  };
  const companionLabel = (view: CompanionView) => {
    if (view.startsWith('plugin:')) return pluginPanels().find(item => panelView(item) === view)?.title || '插件面板';
    if (view.startsWith('browser:')) return browserTabs?.label(view.slice(8)) || '浏览器';
    if (view === 'collaboration') return '协作';
    if (isSideChatView(view)) {
      const item = sideChats().find(item=>item.session_id===view.slice(9));
      const title = item?.title?.replace(/^侧边聊天\s*·\s*/, '') || '';
      return title && title !== '侧边聊天' ? title : `侧聊 ${Math.max(1, sideChats().findIndex(item=>item.session_id===view.slice(9)) + 1)}`;
    }
    if (view === 'assets') return '文件';
    if (view === 'changes') return '任务改动';
    if (view === 'structure') return '三维结构';
    if (view === 'evidence') return '来源证据';
    if (isPageView(view)) return studioPages.find(item => item.id === companionPage(view))?.label || '功能';
    if (isPaperPreviewView(view)) return previewPaper(view).title || '文献';
    return previewPath(view).split('/').pop() || '文件';
  };
  const [companionView, setCompanionView] = createSignal<CompanionView>('collaboration');
  const [workspaceMerged,setWorkspaceMerged]=createSignal(false);
  const [mergedMainActive,setMergedMainActive]=createSignal(false);
  const [splitRequest,setSplitRequest]=createSignal(0);
  createEffect(on(()=>inspectorRequest(),()=>setMergedMainActive(false),{defer:true}));
  const [companionTabs, setCompanionTabs] = createSignal<CompanionView[]>(['collaboration']);
  const [closedCompanionTabs, setClosedCompanionTabs] = createSignal<CompanionView[]>([]);
  let restoredCompanionScope = '';
  const openCompanion = (view: CompanionView) => {
    setCompanionTabs(previous => previous.includes(view) ? previous : [...previous.filter(item => !(isPreviewView(view) && isPreviewView(item)) && !(isPaperPreviewView(view) && isPaperPreviewView(item))), view]);
    setClosedCompanionTabs(previous => previous.filter(item => item !== view));
    setCompanionView(view);
    setInspectorRequest(value => value + 1);
  };
  const removeCompanion = (view: CompanionView) => {
    if (view.startsWith('browser:')) browserTabs?.close(view.slice(8));
    const remaining = companionTabs().filter(item => item !== view);
    setCompanionTabs(remaining);
    if (!isSideChatView(view)) setClosedCompanionTabs(previous => [...previous.filter(item => item !== view), view].slice(-8));
    if (companionView() === view && remaining.length) setCompanionView(remaining[remaining.length - 1]);
    if (!remaining.length) setInspectorCloseRequest(value => value + 1);
  };
  const closeCompanion = async (view: CompanionView) => {
    if (!isSideChatView(view)) { removeCompanion(view); return; }
    const id = view.slice(9);
    const parent = sessionStore.currentSessionId() || '';
    if (closingSideChats().includes(id)) return;
    setClosingSideChats(current=>[...current,id]); setSideChatError('');
    const item = sideChats().find(item=>item.session_id===id);
    setSideChats(current=>current.filter(item=>item.session_id!==id));
    removeCompanion(view);
    try {
      // Hide immediately; keep polling durable deletion without blocking UI.
      await deleteSession(id);
      composerDrafts.update(sideChatDraftKey(parent,id), {text:''});
      if (sessionStore.currentSessionId() === parent) {
        setSideChats(current=>current.filter(item=>item.session_id!==id));
        removeCompanion(view);
      }
      if (readSideChatSelection(parent) === id) saveSideChatSelection(parent, '');
      sessionStore.refreshSubagents();
    } catch (error) {
      if (sessionStore.currentSessionId() === parent) {
        if (item) setSideChats(current=>[...current.filter(value=>value.session_id!==id),item]);
        openCompanion(view);
        setSideChatError(error instanceof Error ? error.message : '关闭侧聊失败');
      }
    } finally { setClosingSideChats(current=>current.filter(item=>item!==id)); }
  };
  const syncSideChats = async (parent: string, reveal = false) => {
    const generation = ++sideChatSyncGeneration;
    const owner = identity();
    try {
      const items = await getSideChats(parent);
      if (generation !== sideChatSyncGeneration || parent !== sessionStore.currentSessionId() || owner !== identity()) return;
      const open = items.filter(item=>!closingSideChats().includes(item.session_id));
      setSideChats(open);
      // Remove states hydrated by an older client/backend from the main dock.
      const sideIds = new Set(items.map(item=>item.session_id));
      for (const state of sessionStore.subagentStates()) {
        if (sideIds.has(state.session_id)) sessionStore.removeSubagentSession(state.session_id);
      }
      const restored = restoreSideChatTabs({tabs:companionTabs(),active:companionView(),closed:closedCompanionTabs()}, open.map(item=>item.session_id), readSideChatSelection(parent) || '');
      setCompanionTabs(restored.tabs); setClosedCompanionTabs(restored.closed);
      if (reveal && open.length) openCompanion(`sidechat:${open.find(item=>item.session_id===readSideChatSelection(parent))?.session_id || open[open.length - 1].session_id}`);
      else setCompanionView(restored.active);
    } catch (error) {
      if (generation === sideChatSyncGeneration && parent === sessionStore.currentSessionId()) setSideChatError(error instanceof Error ? error.message : '加载侧聊失败');
    }
  };
  const createSideChat = async (sourceMessageId = '') => {
    const parent = sessionStore.currentSessionId() || '';
    const owner = identity();
    const agent = sessionStore.currentSession()?.agent_name || 'general';
    if (!parent || creatingSideChat()) return;
    setCreatingSideChat(true); setSideChatError('');
    try {
      const created = await askSideQuestion(parent, '', agent, {createOnly:true,sourceMessageId});
      if (!created) throw new Error('创建侧聊失败');
      if (owner !== identity()) return;
      saveSideChatSelection(parent,created.session_id);
      if (parent !== sessionStore.currentSessionId()) return;
      sideChatSyncGeneration++; // Ignore a list started before this tab was persisted.
      setSideChats(current=>[...current,{session_id:created.session_id,parent_session_id:parent,agent_name:created.agent_name,title:'侧边聊天',status:'idle',created_at:Date.now()/1000,message_count:0,last_message:'',side_question:true}]);
      openCompanion(`sidechat:${created.session_id}`);
    } catch (error) {
      if (parent === sessionStore.currentSessionId()) setSideChatError(error instanceof Error ? error.message : '创建侧聊失败');
    } finally { setCreatingSideChat(false); }
  };
  const browserTabs = connectBrowserTabs?.(
    () => companionView().startsWith('browser:') ? companionView().slice(8) : '',
    (session, open) => {
      const view: CompanionView = `browser:${session}`;
      if (open) openCompanion(view);
      else if (companionTabs().includes(view)) void closeCompanion(view);
    },
  );
  const closeCitation = () => {
    closeCompanion('evidence');
    setCitationRequest(undefined);
    queueMicrotask(() => citationTrigger?.isConnected && citationTrigger.focus());
  };
  onMount(() => {
    document.documentElement.dataset.compact = localStorage.getItem('tcode.settings.compact') === '1' ? 'true' : 'false';
    document.documentElement.dataset.reduceMotion = localStorage.getItem('tcode.settings.reduceMotion') === '1' ? 'true' : 'false';
    const open = (event: Event) => {
      const detail = (event as CustomEvent<CitationOpenDetail>).detail;
      if (!detail?.ref?.id) return;
      citationTrigger = detail.trigger || document.activeElement as HTMLElement | null;
      setCitationRequest({ ref: detail.ref, key: Date.now() });
      openCompanion('evidence');
      setMainSection('console');
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && companionView() === 'evidence' && !document.querySelector('[role="dialog"][aria-modal="true"]')) closeCitation();
    };
    window.addEventListener(CITATION_OPEN_EVENT, open);
    window.addEventListener('keydown', key);
    onCleanup(() => { window.removeEventListener(CITATION_OPEN_EVENT, open); window.removeEventListener('keydown', key); });
  });
  const lastClosedCompanion = () => {
    const closed = closedCompanionTabs();
    return closed[closed.length - 1];
  };
  const reopenCompanion = () => {
    const view = lastClosedCompanion();
    if (view?.startsWith('browser:')) browserTabs?.reopen();
    else if (view) openCompanion(view);
  };
  const companionStorageKey = () => {
    const owner = identity();
    const workspace = currentWorkspaceId();
    // Wait for session hydration before treating the Web session as workspace-less.
    if (!owner || (!workspace && (!SettingsTab || workspaceInitState() !== 'ready'))) return '';
    return `tcode-companion-tabs-v1:${owner}:${workspace}`;
  };
  createEffect(on(companionStorageKey, key => {
    if (!key || key === restoredCompanionScope) return;
    const noWorkspace = !currentWorkspaceId();
    const saved = parseCompanionState(localStorage.getItem(key), page => page !== 'settings' || Boolean(SettingsTab));
    const unrelated = noWorkspace ? settingsOnlyCompanionState(saved) : restoreSideChatTabs(saved, []);
    restoredCompanionScope = key;
    setSettingsSection(unrelated.settingsSection ?? 'general');
    setCompanionTabs(unrelated.tabs); setCompanionView(unrelated.active); setClosedCompanionTabs(unrelated.closed);
  }));
  createEffect(() => {
    const key = companionStorageKey();
    if (!key || key !== restoredCompanionScope) return;
    // Side tabs are restored from their parent-scoped database records, never
    // from this workspace-wide preference (which would leak tabs across chats).
    const state = {
      tabs: companionTabs(), active: companionView(), closed: closedCompanionTabs(),
      settingsSection: SettingsTab ? settingsSection() : undefined,
    };
    const stored = currentWorkspaceId() ? restoreSideChatTabs(state, []) : settingsOnlyCompanionState(state);
    localStorage.setItem(key, JSON.stringify(stored));
  });
  const [workflowAlertRuns, setWorkflowAlertRuns] = createSignal<WorkflowRun[] | null>(null);
  const [reviewAlerts, setReviewAlerts] = createSignal<HumanReviewItem[] | null>(null);
  const [asyncIssueAlerts, setAsyncIssueAlerts] = createSignal<SymphonyLocalIssue[] | null>(null);
  const [alertScope, setAlertScope] = createSignal('');
  let collaborationSession = '';
  createEffect(() => {
    const id = sessionStore.currentSessionId() || '';
    // The saved main ID can be available before authentication/workspace
    // initialization. Retry restoration when that owner scope becomes ready.
    const owner = identity();
    const scope = `${owner}:${currentWorkspaceId()}:${id}`;
    if (scope !== collaborationSession) {
      collaborationSession = scope;
      sideChatSyncGeneration++;
      setSideChats([]); setSideChatError(''); setDiscussionOpen(false);
      const unrelated = restoreSideChatTabs({tabs:companionTabs(),active:companionView(),closed:closedCompanionTabs()}, []);
      setCompanionTabs(unrelated.tabs); setCompanionView(unrelated.active); setClosedCompanionTabs(unrelated.closed);
      if (id && owner) void syncSideChats(id,true);
      setActiveDiscussionSessionId('');
      setCitationRequest(undefined);
    }
  });
  createEffect(on(() => sessionStore.subagentsVersion(), () => {
    const parent = sessionStore.currentSessionId();
    if (parent) void syncSideChats(parent);
  }, {defer:true}));
  createEffect(() => {
    const view = companionView();
    const parent = sessionStore.currentSessionId() || '';
    if (isSideChatView(view) && sideChats().some(item=>item.session_id===view.slice(9) && item.parent_session_id===parent)) saveSideChatSelection(parent,view.slice(9));
  });
  const [sessionLauncherRequest, setSessionLauncherRequest] = createSignal(0);
  const [mcpServers, setMcpServers] = createSignal<MCPServerStatus[]>([]);
  const [mcpStatusLoaded, setMcpStatusLoaded] = createSignal(false);
  const [mcpStatusError, setMcpStatusError] = createSignal('');
  const [skills, setSkills] = createSignal<{ name: string; description: string }[]>([]);
  const [workspaceInitState, setWorkspaceInitState] = createSignal<'loading' | 'failed' | 'ready'>('loading');
  const [workspaceInitStage, setWorkspaceInitStage] = createSignal('正在加载会话…');
  let workspaceInitGeneration = 0;
  let workspaceInitAbort: AbortController | null = null;
  const currentUser = () => props.user;
  const [reviewRequest,setReviewRequest] = createSignal<{reviewId?:string;reviewIdentifier?:string}>();
  const [workflowRequest,setWorkflowRequest] = createSignal<{sessionId?:string;runId?:string;templateKey?:string;draftId?:string}>();
  const [proteinRunRequest, setProteinRunRequest] = createSignal<{runId: string; key: number}>();
  const [searchMessageFocus, setSearchMessageFocus] = createSignal<{sessionId: string; messageId?: string; key: number}>();
  const chatTurns = createMemo(() => presentChatTurns(sessionStore.messages(), sessionStore.isRunning()));
  const [searchSkill, setSearchSkill] = createSignal('');
  const [searchRecordFocus, setSearchRecordFocus] = createSignal<{kind: string; id: string; key: number; project?: SearchHit['project']; comparison?:ResearchChangeFocus;hypothesis?:HypothesisFocus}>();
  const [structureRequest,setStructureRequest] = createSignal<StructureRequest>();
  const identity = () => props.user ? `${props.user.user_id}:${props.user.tenant_id}` : '';
  const seenScheduledNotificationIds = new Set<string>();

  const getEffectiveSchedulerMode = (
    agentName?: string | null,
    schedulerMode?: SchedulerMode | null,
  ): SchedulerMode => (
    usesFixedScheduler(agentName || '')
      ? 'supervisor'
      : (schedulerMode || 'supervisor')
  );

  const normalizeSessionTimestamp = (value: unknown) => {
    const raw = Number(value || 0);
    if (!Number.isFinite(raw) || raw <= 0) return Date.now();
    return raw < 1_000_000_000_000 ? raw * 1000 : raw;
  };
  const mcpConnectedCount = () => mcpServers().filter((server) => server.connected).length;
  const asyncMCPServers = () => mcpServers().filter((server) => server.route_mode === 'job_protocol');
  const asyncMCPConnectedCount = () => asyncMCPServers().filter((server) => server.connected).length;
  const mcpTooltip = () => {
    if (mcpStatusError()) return mcpStatusError();
    if (!mcpStatusLoaded()) return '正在读取 MCP 状态';
    if (mcpServers().length === 0) return '尚未配置 MCP 服务，点击打开配置';
    return mcpServers()
      .map((server) => `${server.name}: ${server.connected ? 'MCP 已连接' : '未连接'} · ${server.route_label || (server.user_callable ? '直接调用' : '异步路由')}${server.error ? ` · ${server.error}` : ''}`)
      .join('\n');
  };
  let mcpStatusInFlight: Promise<void> | null = null;

  const refreshMcpStatus = () => {
    if (mcpStatusInFlight) return mcpStatusInFlight;
    mcpStatusInFlight = fetch('/api/mcp/status?refresh_failed=true')
      .then(async (response) => {
        if (!response.ok) throw new Error(`MCP status failed: ${response.status}`);
        const data = await response.json();
        setMcpServers(data.servers || []);
        setMcpStatusError('');
      })
      .catch((e) => {
        console.error('Failed to load MCP status:', e);
        setMcpStatusError('MCP 状态加载失败，点击打开详情后可重试');
      })
      .finally(() => {
        setMcpStatusLoaded(true);
        mcpStatusInFlight = null;
      });
    return mcpStatusInFlight;
  };

  const loadStatus = async (signal?: AbortSignal) => {
    void refreshMcpStatus();
    const activeSession = sessionStore.currentSession();

    const [skillsResult, teamsResult] = await Promise.allSettled([
      fetch('/api/skills', { signal }).then((response) => {
        if (!response.ok) throw new Error(`Skills status failed: ${response.status}`);
        return response.json();
      }),
      getTeamCatalog(activeSession?.session_id, activeSession?.workdir_name),
    ]);
    if (signal?.aborted || disposed) return;
    if (skillsResult.status === 'fulfilled') {
      setSkills(skillsResult.value.skills || []);
    } else {
      console.error('Failed to load skills:', skillsResult.reason);
    }
    if (teamsResult.status === 'fulfilled') {
      hydrateTeamCatalog(teamsResult.value);
    } else {
      console.error('Failed to load team catalog:', teamsResult.reason);
    }
  };

  let disposed = false;
  const initializeWorkspace = async () => {
    const generation = ++workspaceInitGeneration;
    workspaceInitAbort?.abort();
    const controller = new AbortController(); workspaceInitAbort = controller;
    const runStage = async <T,>(stage: string, timeoutMs: number, work: (signal: AbortSignal) => Promise<T>): Promise<T> => {
      const startedAt = performance.now();
      if (generation === workspaceInitGeneration) setWorkspaceInitStage(stage);
      console.info('[workspace-init]', { event: 'start', stage, generation });
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        const value = await Promise.race([
          work(controller.signal),
          new Promise<never>((_, reject) => {
            timer = setTimeout(() => {
              controller.abort();
              reject(new Error(`workspace_init_timeout:${stage}`));
            }, timeoutMs);
          }),
        ]);
        console.info('[workspace-init]', { event: 'end', stage, generation, duration_ms: Math.round(performance.now() - startedAt) });
        return value;
      } catch (error) {
        console.error('[workspace-init]', { event: 'error', stage, generation, duration_ms: Math.round(performance.now() - startedAt), error });
        throw error;
      } finally {
        if (timer) clearTimeout(timer);
      }
    };
    // Reconcile the browser cache before any workspace panel can use a session
    // id.  This must live at the app level because the sidebar can be hidden and
    // therefore never mount its own session refresh.
    const backendSessions = await runStage('正在加载会话…', 15_000, signal => getSessions({ signal }));
    if (generation !== workspaceInitGeneration || controller.signal.aborted) return;
    sessionStore.mergeSessions(
      backendSessions.map((session) => ({
        session_id: session.session_id,
        title: session.title,
        status: session.status,
        is_archived: Boolean(session.is_archived),
        archived_at: session.archived_at
          ? normalizeSessionTimestamp(session.archived_at)
          : undefined,
        messages: [],
        created_at: normalizeSessionTimestamp(session.created_at),
        updated_at: normalizeSessionTimestamp(session.updated_at),
        agent_name: session.agent_name || '',
        workspace_id: String(session.workspace_id || ''),
        workdir: session.workdir,
        workdir_name: session.workdir_name,
        task_runtime: session.task_runtime,
        async_completion_unread_count: Number(session.async_completion_unread_count || 0),
        mode: session.mode === 'research' ? 'research' as const : 'general' as const,
        research_id: session.research_id || undefined,
        mode_source: session.mode_source || undefined,
        archive_cause: session.archive_cause || undefined,
        archive_locked: Boolean(session.archive_locked),
        scheduler_mode: 'supervisor' as const,
      })),
      'active',
    );

    const sessions = sessionStore.sessions();
    const currentSessionId = sessionStore.currentSessionId();
    
    if (currentSessionId) {
      // Restore the current session's messages
      const currentSession = sessions.find(s => s.session_id === currentSessionId);
      if (currentSession) {
        if (getTeamCatalogEntryByAgentName(currentSession.agent_name || '')?.key === 'protein') {
          // 启动页默认回到 build，避免每次打开页面都自动恢复 protein 团队与子 agent。
          sessionStore.setSelectedAgentName('build');
          sessionStore.setSelectedSchedulerMode(getEffectiveSchedulerMode('build', 'supervisor'));
          await runStage('正在加载工作区能力…', 15_000, loadStatus);
          return;
        }
        sessionStore.setMessages(currentSession.messages);
        const restoredAgentName = currentSession.agent_name || '';
        const visibleRestoredAgentName = toVisibleAgentName(restoredAgentName);
        if (restoredAgentName) {
          sessionStore.setSelectedAgentName(visibleRestoredAgentName || restoredAgentName);
        }
        sessionStore.setSelectedSchedulerMode(
          getEffectiveSchedulerMode(visibleRestoredAgentName || restoredAgentName, currentSession.scheduler_mode)
        );
        if (restoredAgentName && isFixedTeamBootstrapAgent(restoredAgentName)) {
          void (async () => {
            try {
              const resp = await bootstrapSession(restoredAgentName, currentSession.session_id);
              if (disposed) return;
              sessionStore.updateSessionAgent(
                currentSession.session_id,
                resp.agent_name || restoredAgentName,
              );
              sessionStore.refreshSubagents();
            } catch (e) {
              if (disposed) return;
              console.error('Failed to bootstrap fixed team on app restore:', e);
              sessionStore.setStatus('固定团队初始化失败');
            }
          })();
        }
      } else {
        // Current session not found, switch to first available
        if (sessions.length > 0) {
          sessionStore.switchSession(sessions[0].session_id);
        } else {
          sessionStore.clearCurrentSession();
        }
      }
    } else {
      // No current session ID, use the first session
      if (sessions.length > 0) {
        sessionStore.switchSession(sessions[0].session_id);
      }
    }
    
    await runStage('正在加载工作区能力…', 15_000, loadStatus);
  };

  const startWorkspace = async () => {
    setWorkspaceInitState('loading');
    try {
      await initializeWorkspace();
      if (disposed) return;
      setWorkspaceInitState('ready');
      // Restore the scoped tabs first, but do not replay a link the user has left.
      if (startupSettingsPage) {
        const page = startupSettingsPage;
        startupSettingsPage = undefined;
        if (studioPageFromHash(window.location.hash) === page) navigate(page);
      }
    } catch (error) {
      if (disposed) return;
      console.error('Failed to initialize workspace:', error);
      setWorkspaceInitState('failed');
    }
  };

  onMount(() => {
    if (props.user.tenant_status === 'inactive') {
      setWorkspaceInitState('ready');
      return;
    }
    void startWorkspace();

    const syncScheduledResults = async () => {
      if (!currentUser() || currentUser()?.tenant_status === 'inactive') return;
      try {
        const data = await listScheduledNotifications();
        if (disposed) return;
        const unseen = (data.notifications || []).filter((item) => {
          const id = String(item.id || '').trim();
          return id && !seenScheduledNotificationIds.has(id);
        });
        for (const item of unseen) {
          const id = String(item.id || '').trim();
          const sourceSessionId = String(item.source_session_id || '').trim();
          if (id && !sourceSessionId) seenScheduledNotificationIds.add(id);
        }
        const sourceSessionIds = Array.from(new Set(
          unseen
            .map((item) => String(item.source_session_id || '').trim())
            .filter(Boolean),
        ));
        for (const sourceSessionId of sourceSessionIds) {
          if (
            sessionStore.currentSessionId() === sourceSessionId
            && sessionStore.isRunning()
          ) {
            continue;
          }
          const detail = await getSession(sourceSessionId, { bustCache: true });
          if (disposed) return;
          if (!detail) continue;
          sessionStore.setSessions((prev) => prev.map((session) => (
            session.session_id === sourceSessionId
              ? {
                  ...session,
                  messages: detail.messages || [],
                  updated_at: Date.now(),
                }
              : session
          )));
          if (sessionStore.currentSessionId() === sourceSessionId) {
            sessionStore.setMessages(detail.messages || []);
            const latest = unseen.find((item) => item.source_session_id === sourceSessionId);
            if (latest) sessionStore.setStatus(latest.title || '定时任务已完成');
          }
          for (const item of unseen) {
            if (String(item.source_session_id || '').trim() !== sourceSessionId) continue;
            const id = String(item.id || '').trim();
            if (id) seenScheduledNotificationIds.add(id);
          }
        }
      } catch (error) {
        console.error('Failed to sync scheduled task results:', error);
      }
    };

    void syncScheduledResults();
    const scheduledResultTimer = window.setInterval(() => {
      void syncScheduledResults();
    }, 3000);
    onCleanup(() => window.clearInterval(scheduledResultTimer));
  });
  onCleanup(() => { disposed = true; workspaceInitGeneration += 1; workspaceInitAbort?.abort(); });

  const sessionInfoContent = (popover = false) => (
    <div class={popover ? 'studio-chat-info-popover' : 'studio-session-info'}>
      <p>{currentSessionCode()}</p>
      <p>{currentSessionSummary()}</p>
      <p>模式：{currentAgentLabel()}</p>
      <Show when={isResearchSession()}><p>已关联课题；研究记录按内容归属，不自动收录全部消息。</p></Show>
    </div>
  );

  const navigate = (page: StudioPage, section?: SettingsSection) => {
    if (page === 'mcp' || page === 'settings') {
      const next = section ?? (page === 'mcp' ? 'mcp' : undefined);
      if (SettingsTab) {
        setSettingsSection(next ?? 'general');
        openCompanion('page:settings');
        navigate('console');
      } else props.openSettings?.(next);
      return;
    }
    if (page === 'assets' || page === 'changes') {
      openCompanion(page);
      page = 'console';
    }
    if (page === 'structure') {
      openCompanion('structure');
      page = 'console';
    }
    if (page === 'sidechat') {
      const existing = companionTabs().find(tab=>isSideChatView(tab) && tab.slice(9)===readSideChatSelection(sessionStore.currentSessionId() || '')) || companionTabs().find(isSideChatView);
      if (existing) openCompanion(existing);
      else void createSideChat();
      page = 'console';
    }
    if (isCollaborationPage(page)) {
      openCompanion('collaboration');
      if (page === 'team') setCollaborationTab('tasks');
      else if (page === 'subagents') setCollaborationTab('tasks');
      page = 'console';
    }
    if (page !== 'console') {
      openCompanion(`page:${page}`);
      page = 'console';
    }
    setMainSection(page);
    if (window.location.hash !== `#/${page}`) window.location.hash = `/${page}`;
  };
  const openServiceSettings = (section: 'mcp' | 'desktop') => {
    if (section === 'mcp') navigate('mcp');
    else navigate('settings', 'desktop');
  };
  createEffect(on(
    () => sessionStore.filePreviewRequest()?.key,
    () => {
      const req = sessionStore.filePreviewRequest();
      if (req?.path) { openCompanion(previewView(req.path)); setMainSection('console'); }
    },
  ));
  onMount(() => {
    if (mainSection() !== 'console') navigate(mainSection());
    let openingResearch = false;
    const listeners: Record<string, EventListener> = {
      'tcode-open-research-center': () => navigate('research'),
      'tcode-continue-research': event => {
        const detail = (event as CustomEvent).detail || {};
        void (async () => {
          const scope = identity(), workspace = String(detail.workspaceId || '');
          const researchId = String(detail.researchId || '');
          if (openingResearch || !researchId || !workspace || workspace !== currentWorkspaceId()) return;
          openingResearch = true;
          try {
            let session = sessionStore.sessions().find(item =>
              sessionWorkspaceId(item) === workspace && !item.metadata?.parent_session_id && !item.is_archived
              && String(item.research_id || item.metadata?.research_id || '') === researchId
              && (!detail.sessionId || item.session_id === detail.sessionId));
            if (!session) {
              if (detail.sessionId) throw new Error('该会话不在当前可用列表中，请从会话历史打开。');
              if (!await confirmAction(`为“${detail.title || '当前课题'}”创建并关联一个新会话？不会自动启动持续研究。`)) return;
              const result = await bootstrapSession(sessionStore.selectedAgentName(), undefined, workspace);
              if (scope !== identity() || workspace !== currentWorkspaceId()) return;
              const updated = await bindSessionToResearch(result.session_id, researchId);
              if (scope !== identity() || workspace !== currentWorkspaceId()) return;
              session = {session_id:updated.session_id, title:updated.title, agent_name:updated.agent_name,
                workspace_id:workspace, workdir:updated.workdir, workdir_name:updated.workdir_name,
                mode:'research', research_id:researchId, mode_source:updated.mode_source,
                metadata:{mode:'research',research_id:researchId,mode_source:updated.mode_source}, messages:[], created_at:normalizeSessionTimestamp(updated.created_at), updated_at:normalizeSessionTimestamp(updated.updated_at)};
              sessionStore.upsertSession(session);
            }
            if (!session || scope !== identity() || workspace !== currentWorkspaceId()) return;
            const data = await getSession(session.session_id, {bustCache:true});
            if (scope !== identity() || workspace !== currentWorkspaceId()) return;
            sessionStore.switchSession(session.session_id);
            sessionStore.setMessages(data?.messages || []);
            closeCompanion(`page:research`); navigate('console'); setResearchSessionTab('task');
            const key = composerKey(session.session_id);
            const draft = composerDrafts.get(key);
            if (draft.roundRef && detail.roundRef && draft.roundRef.run_id !== detail.roundRef.run_id) { await showFeedback('草稿已关联另一轮运行。请先发送草稿或移除原引用。'); return; }
            composerDrafts.update(key, {roundRef:detail.roundRef || draft.roundRef, text:[draft.text, detail.prompt || `请接续课题“${detail.title || researchId}”：先读取研究摘要、未解决问题和冲突，再说明下一步建议。不要自动开始持续运行。`].filter(Boolean).join('\n\n')});
            window.dispatchEvent(new CustomEvent('tcode-focus-composer'));
          } catch (error) {await showFeedback(error instanceof Error ? error.message : '打开接续会话失败');}
          finally {openingResearch = false;}
        })();
      },
      hashchange: () => { if (window.location.hash.startsWith('#/')) navigate(studioPageFromHash(window.location.hash)); },
      'tcode-open-team-workspace': () => {navigate('subagents');setCollaborationTab('office');},
      'tcode-preview-library-file': event => {
        const path = String((event as CustomEvent).detail?.path || '');
        if (path) sessionStore.requestFilePreview(path);
      },
      'tcode-preview-library-paper': event => {
        const detail = (event as CustomEvent).detail || {};
        const id = String(detail.docId || '');
        if (id) openCompanion(paperPreviewView(id, String(detail.title || id)));
      },
      'tcode-use-library-item-in-chat': event => {
        const detail = (event as CustomEvent).detail || {};
        const text = String(detail.text || '');
        if (!text) return;
        const key = composerKey(sessionStore.currentSessionId());
        const previous = composerDrafts.get(key);
        const path = String(detail.path || '');
        const files = path && !previous.files.some(file=>file.path===path)
          ? [...previous.files, {id:crypto.randomUUID(),name:String(detail.name||path.split('/').pop()||path),path,displayPath:detail.displayPath,mode:'path' as const,source:'mention' as const,sessionId:sessionStore.currentSessionId()||undefined}]
          : previous.files;
        composerDrafts.update(key, {text: [previous.text, text].filter(Boolean).join('\n\n'), files});
        navigate('console');
        window.dispatchEvent(new CustomEvent('tcode-focus-composer'));
      },
      'tcode-reveal-human-review-panel': event => {
        setReviewRequest((event as CustomEvent).detail || {});
        navigate('review');
      },
      'tcode-open-side-chat': event => {
        const detail = (event as CustomEvent).detail || {};
        const parent = String(detail.parentSessionId || '');
        if (parent && sessionStore.sessions().some(s=>s.session_id===parent)) sessionStore.switchSession(parent);
        if (detail.discussion) {
          setActiveDiscussionSessionId(String(detail.sessionId || '')); setDiscussionOpen(true);
          navigate('subagents'); setCollaborationTab('discussion'); return;
        }
        // The main composer creates its own tab; do not create a second one
        // while its request is pending.
        if (detail.pending) { setInspectorRequest(value=>value+1); return; }
        if (detail.sessionId) {
          saveSideChatSelection(parent || sessionStore.currentSessionId() || '', String(detail.sessionId));
          openCompanion(`sidechat:${String(detail.sessionId)}`);
          void syncSideChats(parent || sessionStore.currentSessionId() || '');
        } else void createSideChat(String(detail.sourceMessageId || ''));
        navigate('console');
      },
      'tcode-workflow-run-created': event => {
        const detail = (event as CustomEvent).detail || {};
        if (detail.sessionId && sessionStore.sessions().some(s=>s.session_id===detail.sessionId)) sessionStore.switchSession(detail.sessionId);
        setWorkflowRequest({...detail});
        navigate(detail.draftId ? 'runtime-workflow' : 'workflow');
      },
      'tcode-use-research-pack-in-chat': event => {
        const detail = (event as CustomEvent).detail || {};
        navigate('console');
        window.dispatchEvent(new CustomEvent('tcode-set-research-context', {detail}));
        window.dispatchEvent(new CustomEvent('tcode-prefill-composer', {detail:{text:`请基于导入研究资料「${detail.title || '导入研究资料'}」继续研究：`}}));
      },
      'tcode-open-hypothesis-map': async event => {
        const detail=(event as CustomEvent).detail;
        const link=hypothesisMapLink(detail);if(!link)return;
        try {
          if(currentWorkspaceId()!==link.workspace_id){
            const session=sessionStore.sessions().find(s=>s.workspace_id===link.workspace_id);
            if(!session)throw new Error('请先打开此研究课题所属工作区的会话。');
            await sessionStore.switchSession(session.session_id);
          }
          const projects=await listResearchProjects(link.workspace_id);
          const project=projects.projects.find(p=>p.research_id===link.research_id);
          if(!project)throw new Error('课题不存在或无权访问。');
          const key=Date.now();setSearchRecordFocus({kind:'research',id:link.research_id,project,key,hypothesis:{hypothesisId:link.hypothesis_id,objectId:detail.objectId,objectType:detail.objectType,key}});
          navigate('research');
        } catch(e){await showFeedback(e instanceof Error?e.message:'打开假说地图失败');}
        finally {if(typeof detail.onSettled==='function')detail.onSettled();}
      },
      'tcode-open-research-changes': async event => {
        const detail=(event as CustomEvent).detail;
        const link=researchChangesLink(detail);if(!link)return;
        try {
          if(currentWorkspaceId()!==link.workspace_id){
            const session=sessionStore.sessions().find(s=>s.workspace_id===link.workspace_id);
            if(!session)throw new Error('请先打开此研究版本所属工作区的会话。');
            await sessionStore.switchSession(session.session_id);
          }
          const projects=await listResearchProjects(link.workspace_id);
          const project=projects.projects.find(p=>p.research_id===link.research_id);
          if(!project)throw new Error('课题不存在或无权访问。');
          const key=Date.now();setSearchRecordFocus({kind:'research',id:link.research_id,project,key,comparison:{baseId:link.base_snapshot_id,headId:link.head_snapshot_id,nodeId:typeof detail.nodeId==='string'?detail.nodeId:'project',side:detail.side==='before'?'before':'after',key}});
          navigate('research');
        } catch(e){await showFeedback(e instanceof Error?e.message:'打开研究变化失败');}
        finally {if(typeof detail.onSettled==='function')detail.onSettled();}
      },
      'tcode-use-wiki-in-chat': event => {
        const detail = (event as CustomEvent).detail || {};
        sessionStore.setSelectedAgentName('paper_auto');
        sessionStore.setSelectedSchedulerMode(getEffectiveSchedulerMode('paper_auto','supervisor'));
        navigate('console');
        window.dispatchEvent(new CustomEvent('tcode-set-wiki-context',{detail}));
        window.dispatchEvent(new CustomEvent('tcode-prefill-composer',{detail:{text:detail.text || ''}}));
      },
      'tcode-open-paper-wiki': event => {
        const detail = (event as CustomEvent).detail || {};
        navigate('wiki');
        void import('./components/PaperWikiWorkspace').then(()=>window.requestAnimationFrame(()=>window.dispatchEvent(new CustomEvent('tcode-focus-paper-wiki',{detail}))));
      },
      'tcode-open-structure': event => {
        const detail = (event as CustomEvent<StructureRequest>).detail;
        if (detail?.path) {setStructureRequest({...detail,sessionId:sessionStore.currentSessionId() || ''});navigate('structure');}
      },
    };
    const commandKey = (event: KeyboardEvent) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase()==='k') { event.preventDefault(); setShowCommandPalette(value=>!value); } };
    for (const [name,handler] of Object.entries(listeners)) window.addEventListener(name,handler);
    window.addEventListener('keydown',commandKey);
    if (isCollaborationPage(mainSection())) navigate(mainSection());
    onCleanup(()=>{for (const [name,handler] of Object.entries(listeners)) window.removeEventListener(name,handler);window.removeEventListener('keydown',commandKey);});
  });

  const currentSession = createMemo(() => sessionStore.currentSession());
  const [pluginPanels, setPluginPanels] = createSignal<PanelDescriptor[]>([]);
  const [panelRevision, setPanelRevision] = createSignal(0);
  const panelView = (item: PanelDescriptor): CompanionView => `plugin:${item.plugin}:${item.id}`;
  const panelQuery = createMemo(() => {
    const session = currentSession();
    if (!session?.workdir || !session.session_id || (session.metadata?.harness?.runtime_id || 'tcode-native') !== 'tcode-native') return '';
    return new URLSearchParams({session_id:session.session_id, workdir:session.workdir}).toString();
  });
  onMount(() => {
    const refresh = () => setPanelRevision(value => value + 1);
    window.addEventListener('tcode:plugins-changed', refresh);
    onCleanup(() => window.removeEventListener('tcode:plugins-changed', refresh));
  });
  const removeUnavailablePluginTabs = (items:PanelDescriptor[]) => {
    const available = new Set(items.map(panelView));
    const keep = (view:CompanionView) => !view.startsWith('plugin:') || available.has(view);
    const remaining = companionTabs().filter(keep);
    if (remaining.length !== companionTabs().length) {
      const tabs:CompanionView[] = remaining.length ? remaining : ['collaboration'];
      setCompanionTabs(tabs);
      if (!keep(companionView())) setCompanionView(tabs[tabs.length-1]);
    }
    setClosedCompanionTabs(previous=>previous.filter(keep));
  };
  createEffect(() => {
    panelRevision();
    const query = panelQuery();
    setPluginPanels([]);
    if (!query) {
      if (currentSession()?.metadata?.harness?.runtime_id && currentSession()?.metadata?.harness?.runtime_id !== 'tcode-native') untrack(() => removeUnavailablePluginTabs([]));
      return;
    }
    const controller = new AbortController();
    onCleanup(() => controller.abort());
    void fetch('/api/plugins/panels?' + query, {signal:controller.signal})
      .then(async response => { if (!response.ok) throw new Error('插件目录不可用'); return (await response.json()).panels; })
      .then((items:PanelDescriptor[]) => {
        if (controller.signal.aborted) return;
        setPluginPanels(items);
        removeUnavailablePluginTabs(items);
      })
      .catch(() => {});
  });

  const isResearchSession = createMemo(() => (
    currentSession()?.mode === 'research'
    || currentSession()?.metadata?.mode === 'research'
  ) && Boolean(currentSession()?.research_id || currentSession()?.metadata?.research_id));
  const researchNavigationKey = createMemo(() => isResearchSession() ? JSON.stringify([
    identity(), currentSession()?.research_id || currentSession()?.metadata?.research_id,
    sessionWorkspaceId(currentSession()), researchSessionTab(),
  ]) : false);
  const [researchNavigation] = createResource(researchNavigationKey, key => {
    const [,id,workspace] = JSON.parse(key);
    return loadResearchNavigation(id, workspace);
  });
  const researchContextTabs = createMemo(() => isResearchSession() ? [
    {id: 'overview', label: '研究概览'},
    ...(researchModuleVisible(researchNavigation()?.hypotheses?.length, researchSessionTab() === 'hypotheses') ? [{id:'hypotheses',label:'假说'}] : []),
    ...(researchModuleVisible(researchNavigation()?.experiments?.length, researchSessionTab() === 'experiments') ? [{id:'experiments',label:'实验与分支'}] : []),
    {id: 'evidence', label: '证据'},
    {id: 'findings', label: '结论'},
  ] : []);
  createEffect(on(() => sessionStore.currentSessionId(), () => setResearchSessionTab('task')));
  createEffect(() => {
    if (!isResearchSession() && researchSessionTab() !== 'task') {
      setResearchSessionTab('task');
    }
  });
  const currentWorkspaceId = createMemo(() => sessionWorkspaceId(currentSession()));
  createEffect(on(() => `${identity()}:${currentWorkspaceId()}`, () => {
    setShowCommandPalette(false); setSearchRecordFocus(); setProteinRunRequest(); setSearchMessageFocus(); setSearchSkill('');
  }));
  createEffect(() => {
    const workspace = currentWorkspaceId();
    setWorkflowAlertRuns(null); setReviewAlerts(null); setAsyncIssueAlerts(null);
    setAlertScope(`${identity()}:${workspace}`);
    if (!currentUser() || !workspace) return;
    let disposed = false;
    let loading = false;
    const refresh = async () => {
      if (loading) return;
      loading = true;
      try {
        const results = await Promise.allSettled([
          listWorkflowRuns({ workdir_name: workspace }),
          getHumanReviewQueue('pending_review', 500),
          getSymphonyLocalIssues(500, '', { workspaceId: workspace }),
        ]);
        if (disposed) return;
        if (results[0].status === 'fulfilled') setWorkflowAlertRuns(results[0].value.runs);
        if (results[1].status === 'fulfilled') setReviewAlerts(results[1].value.reviews);
        if (results[2].status === 'fulfilled') setAsyncIssueAlerts(results[2].value.issues);
      } finally { loading = false; }
    };
    void refresh();
    const timer = window.setInterval(() => { if (!document.hidden) void refresh(); }, 10000);
    onCleanup(() => { disposed = true; window.clearInterval(timer); });
  });
  const runtimeNotices = createMemo(() => {
    const currentScope = `${identity()}:${currentWorkspaceId()}`;
    const fresh = alertScope() === currentScope;
    return activeRuntimeNotices(
      sessionStore.sessions(), currentWorkspaceId(),
      fresh ? workflowAlertRuns() : null,
      fresh ? reviewAlerts() : null,
      fresh ? asyncIssueAlerts() : null,
    );
  });
  const noticeCounts = createMemo(() => {
    const notices = runtimeNotices();
    const unreadAsync = sessionStore.sessions()
      .filter(session => sessionWorkspaceId(session) === currentWorkspaceId())
      .reduce((count, session) => count + Number(session.async_completion_unread_count || 0), 0);
    return {
      jobs: notices.filter(notice => notice.page === 'jobs').length + unreadAsync,
      review: notices.filter(notice => notice.page === 'review').length,
      workflow: notices.filter(notice => notice.page === 'workflow' || notice.page === 'runtime-workflow').length,
    };
  });
  const openRuntimeNotice = (notice: RuntimeNotice) => {
    if (notice.sessionId && notice.sessionId !== sessionStore.currentSessionId()) {
      sessionStore.switchSession(notice.sessionId);
      void getSession(notice.sessionId, { bustCache: true }).then(detail => {
        if (disposed || !detail) return;
        const existing = sessionStore.sessions().find(session => session.session_id === notice.sessionId);
        if (!existing) return;
        const messages = mergeWithLocalSyntheticMessages(existing.messages, detail.messages);
        sessionStore.upsertSession({ ...existing, messages });
        if (sessionStore.currentSessionId() === notice.sessionId) sessionStore.setMessages(messages);
      });
    }
    if (notice.page === 'workflow' || notice.page === 'runtime-workflow') {
      setWorkflowRequest({ sessionId: notice.sessionId, runId: notice.externalId });
    } else if (notice.page === 'review') {
      setReviewRequest({ reviewId: notice.reviewId, reviewIdentifier: notice.externalId });
    }
    navigate(notice.page);
  };
  const currentRuntime = createMemo(() => {
    const sessionId = sessionStore.currentSessionId();
    if (!sessionId) return null;
    return sessionStore.getSessionRuntimeState(sessionId);
  });
  const mcpStatusLabel = createMemo(() => {
    if (!mcpStatusLoaded()) return '读取中';
    if (mcpStatusError()) return '状态异常';
    if (mcpServers().length === 0) return '未配置';
    const total = `${mcpConnectedCount()}/${mcpServers().length}`;
    const asyncTotal = `${asyncMCPConnectedCount()}/${asyncMCPServers().length}`;
    return `${total} 已连接 · 异步 ${asyncTotal}`;
  });
  const currentSessionTitle = createMemo(() => {
    const session = currentSession();
    if (session?.title) return session.title;
    const firstLine = String(session?.messages?.[0]?.content || '')
      .trim()
      .split('\n')[0]
      .trim();
    if (firstLine) {
      return firstLine.slice(0, 78);
    }
    if (session?.workdir_name) return session.workdir_name;
    return '欢迎来到 Tcode 编排工作台';
  });
  const currentSessionSummary = createMemo(() => {
    const runtime = currentRuntime();
    const runtimeStatus = String(runtime?.status || '');
    if (runtime?.questionRequest) return '当前会话正在等待你的回答。';
    if (runtime?.permissionRequest) return '当前会话正在等待权限确认。';
    if (runtimeStatus.includes('待接入指导（单步）') || runtimeStatus.includes('待继续（单步）')) {
      return '当前会话正在切换到你刚刚追加的人工指导。';
    }
    if (runtimeStatus.includes('待执行追加指导（单步）')) {
      return '当前会话已记下你的追加指导，会在当前步骤结束后执行。';
    }
    if (runtimeStatus.includes('异步运行')) {
      return '流程详情中存在进行中的异步任务。';
    }
    if (runtime?.runningTool?.tool_name) {
      return `当前工具调用：${runtime.runningTool.tool_name}`;
    }
    if (sessionStore.isRunning()) {
      return sessionStore.status() || '编排任务正在执行中。';
    }
    if (currentSession()?.workdir_name) {
      return `工作目录：${currentSession()?.workdir_name}`;
    }
    if (sessionStore.currentSessionId() && sessionStore.messages().length > 0) {
      return '会话历史已恢复，正在同步工作目录与流程信息。';
    }
    return '尚未检测到活动会话。可以直接开始新对话，或先配置一个工作目录。';
  });
  const currentSessionCode = createMemo(() => {
    const sessionId = String(sessionStore.currentSessionId() || '').trim();
    if (!sessionId) return '会话: --';
    return `会话: ${sessionId.slice(0, 8).toUpperCase()}`;
  });
  const currentStatusLabel = createMemo(() => {
    const runtime = currentRuntime();
    const runtimeStatus = String(runtime?.status || '');
    if (runtime?.questionRequest) return '等待回答';
    if (runtime?.permissionRequest) return '待确认';
    if (runtimeStatus.includes('待接入指导（单步）') || runtimeStatus.includes('待继续（单步）')) return '待接入';
    if (runtimeStatus.includes('待执行追加指导（单步）')) return '待执行';
    if (runtimeStatus.includes('异步运行')) return '异步运行';
    if (sessionStore.isRunning()) return '运行中';
    if (sessionStore.messages().length > 0) return '空闲';
    return '未开始';
  });
  const currentStatusClass = createMemo(() => {
    const runtime = currentRuntime();
    const runtimeStatus = String(runtime?.status || '');
    if (runtime?.questionRequest) return 'border-[#1f6feb]/30 bg-[#1f6feb]/12 text-[#8ed5ff]';
    if (runtime?.permissionRequest) return 'border-[#f1a02b]/30 bg-[#f1a02b]/12 text-[#ffc176]';
    if (runtimeStatus.includes('待接入指导（单步）') || runtimeStatus.includes('待继续（单步）')) {
      return 'border-[#4ecdc4]/30 bg-[#4ecdc4]/12 text-[#9df3ed]';
    }
    if (runtimeStatus.includes('待执行追加指导（单步）')) {
      return 'border-[#f5c451]/30 bg-[#f5c451]/12 text-[#ffe49a]';
    }
    if (runtimeStatus.includes('异步运行')) {
      return 'border-[#f59e0b]/30 bg-[#f59e0b]/12 text-[#ffd17a]';
    }
    if (sessionStore.isRunning()) return 'border-[#38b46a]/30 bg-[#38b46a]/10 text-[#9ee6bb]';
    return 'border-[#3e484f] bg-[#1b2024] text-[#bdc8d1]';
  });
  const currentAgentLabel = createMemo(() => {
    const agentName = currentSession()?.agent_name || sessionStore.selectedAgentName();
    const visibleName = toVisibleAgentName(agentName) || agentName || 'build';
    const labels: Record<string, string> = {
      build: '构建模式',
      manager: '团队编排',
      paper_auto: '文献自动协作',
      protein_orchestrator: '蛋白设计团队',
    };
    return labels[visibleName] || visibleName;
  });
  const currentSessionIsArchived = createMemo(() => Boolean(currentSession()?.is_archived));
  const openConsole = () => navigate('console');
  const openHistoryModal = () => setShowHistoryModal(true);
  const openSkillCenter = () => {setSearchSkill('');setShowSkillLibrary(true);};
  const openSessionLauncher = () => setSessionLauncherRequest(previous=>previous+1);
  const commandItems = createMemo(() => [
    ...studioPages.map(item => ({id:`page:${item.id}`,label:`打开${item.label}`,detail:item.group,run:()=>navigate(item.id)})),
    ...availableSettingsSections(isDesktop || Boolean(currentUser()?.password_mode), Boolean(currentWorkspaceId()))
      .map(({id, label, detail, group}) => ({
        id: `settings:${id}`, label: `设置：${label}`, detail, keywords: `settings ${id} ${group}`, run: () => navigate('settings', id),
      })),
    ...mcpServers().map(server=>({id:`mcp:${server.name}`,label:server.name,detail:'MCP 服务',run:()=>navigate('mcp')})),
    {id:'new',label:'新建任务',detail:'选择研究空间并开始会话',keywords:'workspace session',run:openSessionLauncher},
    {id:'assets',label:'打开文件与改动',detail:'查看本地文件、产物和 Review',keywords:'artifact diff',run:()=>navigate('assets')},
    {id:'library',label:'打开资料库',detail:'查看本地文献和其他工作区资料',keywords:'paper document library pdf',run:()=>navigate('library')},
    {id:'structure',label:'打开三维结构',detail:'查看并选择结构位点',keywords:'pdb sdf molecule',run:()=>navigate('structure')},
    {id:'review',label:'打开结果审查',detail:'处理待确认的结果和评论',keywords:'review',run:()=>navigate('review')},
    {id:'jobs',label:'显示远程计算',detail:'查看异步 MCP 进度、日志和取消',keywords:'mcp job log',run:()=>navigate('jobs')},
    {id:'collaboration',label:'打开协作',detail:'查看 Subagent 与旁支讨论',keywords:'subagent team',run:()=>navigate('subagents')},
    {id:'plan',label:'制定任务计划',detail:'补充目标、阶段和完成条件',keywords:'goal plan',run:()=>{navigate('console');window.dispatchEvent(new CustomEvent('tcode-prefill-composer',{detail:{text:'请先为当前任务制定计划：列出目标、非目标、所需数据与服务、执行阶段、验证方式和完成条件。'}}));}},
    {id:'branch',label:'创建实验分支',detail:'基于当前输入和参数派生独立实验',keywords:'branch compare',run:()=>{navigate('console');window.dispatchEvent(new CustomEvent('tcode-prefill-composer',{detail:{text:'请从当前任务创建一个实验分支，保留原始输入，使用独立输出目录和运行记录，并先询问需要修改的参数。'}}));}},
  ]);
  let searchNavigationVersion = 0;
  const openSearchHit = async (hit: SearchHit) => {
    const version = ++searchNavigationVersion;
    const scope = `${identity()}:${currentWorkspaceId()}`;
    try {
      if (hit.kind === 'session' || hit.kind === 'message' || (hit.kind === 'workflow' && hit.sessionId)) {
        const detail = await getSession(hit.sessionId || hit.id, {bustCache: true});
        if (!detail || version !== searchNavigationVersion || scope !== `${identity()}:${currentWorkspaceId()}`) return;
        const existing = sessionStore.sessions().find(item => item.session_id === detail.session_id);
        const messages = mergeWithLocalSyntheticMessages(existing?.messages || [], detail.messages);
        sessionStore.upsertSession({...existing, session_id: detail.session_id, title: detail.title,
          messages, created_at: Number(detail.created_at) || Date.now(), updated_at: Number(detail.updated_at) || Date.now(),
          workspace_id: detail.workspace_id, workdir: detail.workdir, workdir_name: detail.workdir_name,
          agent_name: detail.agent_name, metadata: detail.metadata, is_archived: detail.is_archived});
        sessionStore.switchSession(detail.session_id);
        if (hit.kind !== 'workflow') navigate('console');
        setResearchSessionTab('task');
        if (hit.kind === 'message') setSearchMessageFocus({sessionId: detail.session_id, messageId: hit.id, key: Date.now()});
      }
      if (hit.kind === 'session' || hit.kind === 'message') return;
      if (hit.kind === 'file') sessionStore.requestFilePreview(hit.id);
      else if (hit.kind === 'paper') openCompanion(paperPreviewView(hit.id, hit.title));
      else if (hit.kind === 'wiki') window.dispatchEvent(new CustomEvent('tcode-open-paper-wiki', {detail: {pageId: hit.id}}));
      else if (hit.kind === 'protein-run') { setProteinRunRequest({runId: hit.id, key: Date.now()}); navigate('protein-runs'); }
      else if (hit.kind === 'workflow') { setWorkflowRequest({runId: hit.templateKey || hit.draftId ? undefined : hit.id, templateKey: hit.templateKey, draftId: hit.draftId, sessionId: hit.sessionId}); navigate(hit.temporary ? 'runtime-workflow' : 'workflow'); }
      else if (hit.kind === 'skill') {setSearchSkill(hit.id);setShowSkillLibrary(true);}
      else if (['research','scheduled','job'].includes(hit.kind)) {
        setSearchRecordFocus({kind: hit.kind, id: hit.id, key: Date.now(), project: hit.project});
        navigate(hit.kind === 'job' ? 'jobs' : hit.kind as 'research' | 'scheduled');
      }
    } catch (error) { await showFeedback(error instanceof Error ? error.message : '无法打开搜索结果，请重试'); }
  };
  const handleStartConversation = () => {
    if (sessionStore.currentSessionId()) window.dispatchEvent(new CustomEvent('tcode-focus-composer'));
    else openSessionLauncher();
  };
  const openKanbanHumanReview = (card: KanbanCard) => {
    const target = String(card.root_session_id || card.session_id || '').trim();
    if (target && sessionStore.sessions().some(session=>session.session_id===target)) sessionStore.switchSession(target);
    setReviewRequest({reviewId:card.human_review?.id || '',reviewIdentifier:card.human_review?.identifier || ''});
    navigate('review');
  };

  return (
        <Show when={workspaceInitState() === 'ready'} fallback={
          <Show when={workspaceInitState() === 'failed'} fallback={<AppLoading label={workspaceInitStage()} />}>
            <WorkspaceInitError onRetry={startWorkspace}/>
          </Show>
        }>
        <StudioShell pluginNavigation={<For each={pluginPanels()}>{panel => <button aria-label={panel.title} onClick={() => openCompanion(panelView(panel))}><span>插</span>{panel.title}</button>}</For>} page={mainSection()} onNavigate={navigate}
          mergedMainActive={mergedMainActive()} onMergedChange={setWorkspaceMerged} splitRequest={splitRequest()}
          identity={`${currentUser()?.user_id}:${currentUser()?.tenant_id}`}
          workspace={currentWorkspaceId()} sessionTitle={currentSessionTitle()} status={currentStatusLabel()}
          notices={runtimeNotices()} onNoticeSelect={openRuntimeNotice}
          noticeCounts={noticeCounts()}
          contextTabs={researchContextTabs()}
          activeContextTab={researchSessionTab()}
          onContextTabSelect={id=>setResearchSessionTab(id as ResearchSessionTab)}
          sidebarRequest={sessionLauncherRequest()}
          onNewSession={openSessionLauncher}
          onCommandPalette={()=>setShowCommandPalette(true)}
          onOpenSkillCenter={openSkillCenter}
          onOpenServiceSettings={openServiceSettings}
          skillCount={skills().length} subagentCount={sessionStore.subagentStates().length}
          browserActive={isDesktop && companionView().startsWith('browser:') && !(workspaceMerged()&&mergedMainActive())}
          inspectorTabs={companionTabs().map(id=>({ id, label: companionLabel(id) }))}
          activeInspectorTab={workspaceMerged() && mergedMainActive() ? undefined : companionView()}
          onInspectorTabSelect={id=>{
            const tab = companionTabs().find(view=>view === id);
            if (!tab) return;
            openCompanion(tab);
          }}
          inspectorTitle="伴随查看" inspectorRequest={inspectorRequest()} inspectorCloseRequest={inspectorCloseRequest()} onInspectorVisibility={setInspectorVisible}
          actions={props.accountMenu}
          sidebar={<Suspense fallback={<LazyPanelFallback label="正在加载会话列表..." />}>
            <SessionList embedded width={280} openCreateRequest={sessionLauncherRequest()}
              onOpenHome={openConsole} onOpenHistory={openHistoryModal} />
          </Suspense>}
          inspectorScope={`${identity()}:${currentWorkspaceId()}:${sessionStore.currentSessionId() || ''}`}
          companionBar={{
            get tabs() { return companionTabs(); }, get active() { return companionView(); },
            label: companionLabel, title: view => isPreviewView(view) ? displayWorkspacePath(previewPath(view), isDesktop) : companionLabel(view),
            get merged() { return workspaceMerged(); }, get mainActive() { return mergedMainActive(); },
            get lastClosed() { return lastClosedCompanion(); },
            get plugins() { return pluginPanels().map(panel => ({title: panel.title, open: () => openCompanion(panelView(panel))})); },
            get canCreateChat() { return !!sessionStore.currentSessionId(); }, get creatingChat() { return creatingSideChat(); },
            closePending: tab => closingSideChats().includes(tab.slice(9)),
            onMain: () => setMergedMainActive(true),
            onSelect: tab => { setMergedMainActive(false); setCompanionView(tab); },
            onClose: tab => tab === 'evidence' ? closeCitation() : void closeCompanion(tab),
            onCreateChat: () => void createSideChat(), onReopen: reopenCompanion, onSplit: () => setSplitRequest(value => value + 1),
          }}
          inspector={<>
            <Show when={sideChatError()}><div class="side-chat-error" role="alert">{sideChatError()}<button onClick={()=>{setSideChatError('');const id=sessionStore.currentSessionId();if(id)void syncSideChats(id);}}>重试</button></div></Show>
            <For each={pluginPanels().filter(panel => companionView() === panelView(panel))}>{panel =>
              <PluginWorkspacePanel panel={panel} query={panelQuery()} onShare={(text,file) => {
                const key = composerKey(sessionStore.currentSessionId());
                const previous = composerDrafts.get(key);
                composerDrafts.update(key,{text:[previous.text,text].filter(Boolean).join('\n\n'),
                  files:file ? [...previous.files,{...file,id:crypto.randomUUID(),source:'upload',mode:'path',sessionId:sessionStore.currentSessionId() || undefined}] : previous.files});
                navigate('console'); window.dispatchEvent(new CustomEvent('tcode-focus-composer'));
              }}/>
            }</For>
            <div class="studio-page" hidden={companionView()!=='structure'}><Suspense fallback={<LazyPanelFallback/>}><StructureWorkspace
              workspaceId={currentWorkspaceId()} workdir={currentSession()?.workdir} request={structureRequest()}
              active={inspectorVisible() && companionView()==='structure'}
              onReveal={()=>navigate('structure')}
              onPin={ref=>{
                const key=composerKey(sessionStore.currentSessionId());const previous=composerDrafts.get(key);
                const refs=previous.structureRefs || [];
                if (refs.length >= 8) { sessionStore.setStatus('每轮最多引用 8 个结构位点'); return; }
                const next=Math.max(0,...refs.map(item=>Number(item.label.replace('@位点',''))||0))+1;
                composerDrafts.update(key,{structureRefs:[...refs,{...ref,label:`@位点${next}`}]});
                navigate('console');window.dispatchEvent(new CustomEvent('tcode-focus-composer'));
              }}
              onAsk={text=>{
                const key=composerKey(sessionStore.currentSessionId());const previous=composerDrafts.get(key);
                composerDrafts.update(key,{text:[previous.text,text].filter(Boolean).join('\n\n')});
                navigate('console');window.dispatchEvent(new CustomEvent('tcode-focus-composer'));
              }}/></Suspense></div>
            <WorkspaceSlot active={companionView()==='assets'}><Suspense fallback={<LazyPanelFallback/>}><FileBrowser identityScope={identity()} mode="files" workdir={currentSession()?.workdir} workspaceId={currentWorkspaceId()} onOpenDetail={path=>sessionStore.requestFilePreview(path)} onClose={()=>closeCompanion('assets')}/></Suspense></WorkspaceSlot>
            <WorkspaceSlot active={companionView()==='changes'}><Suspense fallback={<LazyPanelFallback/>}><FileBrowser identityScope={identity()} mode="changes" workdir={currentSession()?.workdir} workspaceId={currentWorkspaceId()} onPreview={path=>sessionStore.requestFilePreview(path)} onQuote={text=>{const key=composerKey(sessionStore.currentSessionId());composerDrafts.update(key,{text:[composerDrafts.get(key).text,text].filter(Boolean).join('\n\n')});navigate('console');window.dispatchEvent(new CustomEvent('tcode-focus-composer'));}} onClose={()=>closeCompanion('changes')}/></Suspense></WorkspaceSlot>
            <WorkspaceSlot active={companionView()==='evidence'}><Suspense fallback={<LazyPanelFallback label="正在加载来源证据…"/>}><EvidencePanel request={citationRequest()} workspaceId={currentWorkspaceId()} sessionId={sessionStore.currentSessionId() || ''} onClose={closeCitation}/></Suspense></WorkspaceSlot>
            <For each={companionTabs().filter(isPreviewView)}>{tab =>
              <WorkspaceSlot active={companionView()===tab}><Suspense fallback={<LazyPanelFallback label="正在加载文件预览…"/>}><FilePreviewPanel path={previewPath(tab)} workdir={currentSession()?.workdir} workspaceId={currentWorkspaceId()}/></Suspense></WorkspaceSlot>
            }</For>
            <For each={companionTabs().filter(isPaperPreviewView)}>{tab => {
              const paper=()=>previewPaper(tab);
              return <WorkspaceSlot active={companionView()===tab}><Suspense fallback={<LazyPanelFallback label="正在加载文献预览…"/>}><PaperLibraryPreviewPanel workspaceId={currentWorkspaceId()} docId={paper().id} title={paper().title}/></Suspense></WorkspaceSlot>;
            }}</For>
            <For each={companionTabs().filter(isPageView)}>{tab => {
              const page=()=>companionPage(tab);
              return <>
                <WorkspaceSlot active={companionView()===tab && page()==='jobs'}><Suspense fallback={<LazyPanelFallback/>}><SymphonyWorkspace jobsOnly active={companionView()===tab} focusRequest={searchRecordFocus()?.kind==='job'?searchRecordFocus():undefined}/></Suspense></WorkspaceSlot>
                <WorkspaceSlot active={companionView()===tab && page()==='symphony'}><Suspense fallback={<LazyPanelFallback/>}><SymphonyWorkspace active={companionView()===tab}/></Suspense></WorkspaceSlot>
                <WorkspaceSlot active={companionView()===tab && page()==='review'} scroll><Suspense fallback={<LazyPanelFallback/>}><HumanReviewPanel active={companionView()===tab} focusRequest={reviewRequest()}/></Suspense></WorkspaceSlot>
                <WorkspaceSlot active={companionView()===tab && page()==='runtime-workflow'} scroll><Suspense fallback={<LazyPanelFallback/>}><WorkflowWorkspacePanel mode="temporary" active={companionView()===tab} focusRequest={workflowRequest()}/></Suspense></WorkspaceSlot>
                <WorkspaceSlot active={companionView()===tab && page()==='workflow'} scroll><Suspense fallback={<LazyPanelFallback/>}><WorkflowWorkspacePanel mode="templates" active={companionView()===tab} focusRequest={workflowRequest()}/></Suspense></WorkspaceSlot>
                <WorkspaceSlot active={companionView()===tab && page()==='kanban'}><Suspense fallback={<LazyPanelFallback/>}><KanbanWorkspace onOpenHumanReview={openKanbanHumanReview}/></Suspense></WorkspaceSlot>
                <WorkspaceSlot active={companionView()===tab && page()==='scheduled'}><Suspense fallback={<LazyPanelFallback/>}><ScheduledTasksWorkspace currentUser={currentUser()!} sourceSessionId={sessionStore.currentSessionId() || undefined} workspaceId={currentWorkspaceId() || undefined} workdir={currentSession()?.workdir} focusRequest={searchRecordFocus()?.kind==='scheduled'?searchRecordFocus():undefined}/></Suspense></WorkspaceSlot>
                <WorkspaceSlot active={companionView()===tab && page()==='protein-runs'}><Suspense fallback={<LazyPanelFallback/>}><ProteinRunsWorkspace workspaceId={currentWorkspaceId() || undefined} active={companionView()===tab} focusRequest={proteinRunRequest()}/></Suspense></WorkspaceSlot>
                <WorkspaceSlot active={companionView()===tab && page()==='research'}><Suspense fallback={<LazyPanelFallback/>}><ResearchWorkspace workspaceId={currentWorkspaceId() || undefined} active={companionView()===tab} focusRequest={searchRecordFocus()?.kind==='research'?searchRecordFocus():undefined}/></Suspense></WorkspaceSlot>
                <WorkspaceSlot active={companionView()===tab && page()==='library'}><Suspense fallback={<LazyPanelFallback/>}><ResourceLibraryWorkspace workspaceId={currentWorkspaceId() || undefined} active={companionView()===tab}/></Suspense></WorkspaceSlot>
                <WorkspaceSlot active={companionView()===tab && page()==='wiki'}><Suspense fallback={<LazyPanelFallback/>}><PaperWikiWorkspace workspaceId={currentWorkspaceId() || undefined} active={companionView()===tab}/></Suspense></WorkspaceSlot>
                <WorkspaceSlot active={companionView()===tab && page()==='ontology'}><Suspense fallback={<LazyPanelFallback/>}><OntologyWorkspace workspaceId={currentWorkspaceId() || undefined} currentModelRef={sessionStore.currentSession()?.model || undefined}/></Suspense></WorkspaceSlot>
                <Show when={SettingsTab && page()==='settings'}>
                  <WorkspaceSlot active={companionView()===tab}><Suspense fallback={<LazyPanelFallback/>}>
                    {SettingsTab && <SettingsTab workspaceId={currentWorkspaceId() || undefined}
                      section={settingsSection()} onSectionChange={setSettingsSection}/>}
                  </Suspense></WorkspaceSlot>
                </Show>
                <WorkspaceSlot active={companionView()===tab && page()==='plugins'}><Suspense fallback={<LazyPanelFallback/>}><PluginPanel embedded width={360} workdir={currentSession()?.workdir} onHide={()=>closeCompanion(tab)}/></Suspense></WorkspaceSlot>
              </>;
            }}</For>
            <For each={companionTabs().filter(isSideChatView)}>{tab =>
              <WorkspaceSlot active={companionView()===tab}><Suspense fallback={<LazyPanelFallback/>}><SideChatWorkspace
                embedded sideOnly active={inspectorVisible() && companionView()===tab}
                selectedSessionId={tab.slice(9)}
                onClose={()=>void closeCompanion(tab)}
                onDeleted={()=>{setSideChats(current=>current.filter(item=>item.session_id!==tab.slice(9)));removeCompanion(tab);}}
              /></Suspense></WorkspaceSlot>
            }</For>
            <WorkspaceSlot active={companionView()==='collaboration'}><Suspense fallback={<LazyPanelFallback/>}><CollaborationPanel
            active={inspectorVisible() && companionView()==='collaboration'} tab={collaborationTab()} onTab={setCollaborationTab}
            discussion={discussionOpen()} onDiscussion={setDiscussionOpen} pending={false} selectedSessionId={activeDiscussionSessionId()}
            onSelected={setActiveDiscussionSessionId}
            onQuote={text => {
              const key = composerKey(sessionStore.currentSessionId());
              composerDrafts.update(key, {text: [composerDrafts.get(key).text, text].filter(Boolean).join('\n\n')});
              navigate('console'); window.dispatchEvent(new CustomEvent('tcode-focus-composer'));
            }} context={sessionInfoContent()}/></Suspense></WorkspaceSlot></>}
          dock={<div class="studio-runtime-summary">
            <div><b>{currentStatusLabel()}</b><span>{currentSessionSummary()}</span><button onClick={()=>navigate('symphony')}>打开完整运行总览 ↗</button></div>
            <For each={sessionStore.subagentStates().slice(0,6)}>{agent=><button class="studio-runtime-row" onClick={()=>navigate('subagents')}>{String(agent.agent_name || agent.session_id || 'Subagent')}<span>{String(agent.status || '')}</span></button>}</For>
            <p>这里显示当前会话运行信号；完整异步队列、日志、审批与阶段操作保留在各自工作标签中。</p>
          </div>}
        >
                  <section class="studio-chat flex h-full min-h-0 min-w-0 flex-1 flex-col bg-[#0f1418]/76" hidden={mainSection() !== 'console'}>
                    <div class="studio-chat-header">
                      <h1 title={currentSessionTitle()}>{currentSessionTitle()}</h1>
                      <RunDirectory turns={chatTurns()} disabled={!sessionStore.currentSessionId()} onNavigate={messageId => {
                        const sessionId = sessionStore.currentSessionId();
                        if (!sessionId) return;
                        setResearchSessionTab('task');
                        setSearchMessageFocus({sessionId, messageId, key: performance.now()});
                      }} />
                      <TaskLifecycleStrip compact />
                      <div class="task-context-strip" aria-label="当前任务上下文">
                        <button title={`研究空间：${currentSession()?.workdir || currentWorkspaceId()}`} onClick={()=>{openCompanion('assets');navigate('console');}}><span>研究空间</span><b>{currentSession()?.workdir_name || currentWorkspaceId() || '未选择'}</b></button>
                        <button title="当前 Agent"><span>执行</span><b>{currentAgentLabel()}</b></button>
                        <button title={mcpTooltip()} onClick={()=>navigate('mcp')}><span>MCP</span><b>{mcpStatusLabel()}</b></button>
                        <button title="打开协作" onClick={()=>openCompanion('collaboration')}><span>协作</span><b>{sessionStore.subagentStates().length ? `${sessionStore.subagentStates().length} 个任务` : '未启动'}</b></button>
                      </div>
                      <span class={`shrink-0 rounded-full border px-2 text-[10px] ${currentStatusClass()}`}>{currentStatusLabel()}</span>
                      <Show when={currentSessionIsArchived()}><span class="shrink-0 text-[10px]">已归档</span></Show>
                      <button class="side-chat-trigger" aria-label="打开侧边聊天" disabled={!sessionStore.currentSessionId()} onClick={()=>navigate('sidechat')}>侧边聊天</button>
                      <details ref={sessionInfoDetails} class="studio-chat-info">
                        <summary aria-label="会话信息与操作" title="会话信息与操作">⋯</summary>
                        {sessionInfoContent(true)}
                      </details>
                    </div>

                    <div class="min-h-0 flex flex-1 flex-col">
                      <Suspense fallback={<LazyPanelFallback label="正在加载对话..." />}>
                        <ChatView
                          turns={chatTurns()}
                          onStartConversation={handleStartConversation}
                          onInitializeWorkspace={openSessionLauncher}
                          activeTab={researchSessionTab()}
                          onResearchTabChange={setResearchSessionTab}
                          searchFocus={searchMessageFocus()}
                        />
                        <MessageInput onOpenSkills={openSkillCenter} onOpenModelSettings={() => navigate('settings', 'models')} />
                      </Suspense>
                    </div>
                  </section>
        </StudioShell>
        {SettingsWindow && <SettingsWindow workspaceId={currentWorkspaceId() || undefined}/>}
        <Show when={showHistoryModal()}><Suspense fallback={<LazyPanelFallback/>}><ArchivedSessionsModal onClose={()=>setShowHistoryModal(false)}/></Suspense></Show>
        <Show when={showCommandPalette()}><Suspense fallback={null}><CommandPalette items={commandItems()} workspaceId={currentWorkspaceId()} onHit={openSearchHit} onClose={()=>setShowCommandPalette(false)}/></Suspense></Show>
        <Show when={showSkillLibrary()}><Suspense fallback={<LazyPanelFallback/>}><SkillLibrary workspaceId={currentWorkspaceId()} initialSkill={searchSkill()} initialAgent={currentSession()?.agent_name || sessionStore.selectedAgentName()} onClose={()=>{setShowSkillLibrary(false);void loadStatus();}}/></Suspense></Show>
        <Suspense><AsyncCompletionNotifications/></Suspense>
        </Show>
  );
}
