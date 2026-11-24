import FeedbackDialog, { confirmAction } from './FeedbackDialog';
import { verificationStageLabels, verificationStageStatus, verificationEffectiveLabel, type VerificationSettings } from '../utils/verificationSettings';
import { For, Index, Show, createEffect, createSignal, onMount } from 'solid-js';
import MCPAndSkillsPanel from './MCPAndSkillsPanel';
import PluginAsyncTargetsSettings from './PluginAsyncTargetsSettings';
import MCPFileTransferEditor from './MCPFileTransferEditor';
import DynamicHeadersEditor from './DynamicHeadersEditor';
import type { DynamicHeaders } from '../api/capabilities';
import type { MCPFileTransfer } from '../api/capabilities';
import { deleteMCPConfig, getMCPConfig, reloadMCPConfig, testMCPConnection, saveMCPConfig, type MCPConfigServer } from '../api/client';
import { ontologyRequest } from '../api/ontology';
import { useClientPlatform } from '../clientPlatform';
import { getAgents, getChatRuntimes, getModels, selectModel, type ModelOption } from '../api/catalog';
import { autoSwarmAvailability } from '../utils/schedulerCapabilities';
import SchedulerOptions from './composer/SchedulerOptions';
import { sessionStore } from '../stores/session';
import { announcePreferenceChange, preferenceKeys, readBooleanPreference, readPreference, savePreference } from '../utils/userPreferences';
import { agentDisplayLabel } from './composer/executionLabels';
import { canvasPreference, type CanvasStyle } from '../api/canvas';
import WebModelSettings from './WebModelSettings';
import FeishuSettings from './FeishuSettings';
import WebSearchSettings from './WebSearchSettings';
import AccountSecuritySettings from './AccountSecuritySettings';
import type { AuthUser } from '../api/auth';
import PersonalArtifactBinding from './PersonalArtifactBinding';

import { availableSettingsSections, settingsGroups, type SettingsSection } from '../utils/settingsSections';

type HeaderRow = { name: string; value: string };

export default function SettingsWorkspace(props: {
  initialSection?: SettingsSection;
  section?: SettingsSection;
  onSectionChange?: (section: SettingsSection) => void;
  workspaceId?: string;
  account?: AuthUser;
  onPasswordSaved?: () => void;
} = {}) {
  const platform = useClientPlatform();
  const desktop = platform.surface === 'desktop';
  const ModelSettings = platform.ModelSettings;
  const InternetContentSettings = platform.InternetContentSettings;
  const [localSection, setLocalSection] = createSignal<SettingsSection>(props.initialSection || 'mcp');
  const section = () => props.section ?? localSection();
  const setSection = (next: SettingsSection) => {
    if (props.onSectionChange) props.onSectionChange(next);
    else setLocalSection(next);
  };
  const [query, setQuery] = createSignal('');
  createEffect(() => {
    if (props.section === undefined) setLocalSection(props.initialSection || 'mcp');
  });
  const [compact, setCompact] = createSignal(localStorage.getItem('tcode.settings.compact') === '1');
  const [notify, setNotify] = createSignal(localStorage.getItem('tcode.settings.notify') !== '0');
  const [inAppNotify, setInAppNotify] = createSignal(localStorage.getItem('tcode.settings.inAppNotify') !== '0');
  const [reduceMotion, setReduceMotion] = createSignal(localStorage.getItem('tcode.settings.reduceMotion') === '1');
  const [generateIntelligentUI, setGenerateIntelligentUI] = createSignal(true);
  const [canvasStyle, setCanvasStyle] = createSignal<CanvasStyle | 'default'>('default');
  const [canvasDefault, setCanvasDefault] = createSignal<CanvasStyle>('clay');
  const [canvasPreferenceReady, setCanvasPreferenceReady] = createSignal(false);
  const [defaultMcpMode, setDefaultMcpMode] = createSignal<'auto' | 'direct' | 'async'>((localStorage.getItem('tcode.settings.mcpMode') as 'auto' | 'direct' | 'async') || 'async');
  const [reasoning, setReasoning] = createSignal<'minimal'|'low'|'medium'|'high'>(readPreference(preferenceKeys.reasoning, 'medium'));
  const [scheduler, setScheduler] = createSignal<'supervisor'|'chain'|'swarm'|'auto_swarm_builder'>(readPreference(preferenceKeys.scheduler, 'supervisor'));
  const [schedulerCapabilities, setSchedulerCapabilities] = createSignal<Record<string, unknown>>();
  const autoSwarm = () => autoSwarmAvailability(schedulerCapabilities());
  const [defaultAgent, setDefaultAgent] = createSignal<string>(readPreference(preferenceKeys.agent, 'build'));
  const [agents, setAgents] = createSignal<{ name: string; description?: string }[]>([]);
  const [officeAnimations, setOfficeAnimations] = createSignal(readBooleanPreference(preferenceKeys.officeAnimations, true));
  const [officePauseInactive, setOfficePauseInactive] = createSignal(readBooleanPreference(preferenceKeys.officePauseInactive, true));
  const [officeRememberZoom, setOfficeRememberZoom] = createSignal(readBooleanPreference(preferenceKeys.officeRememberZoom, true));
  const [structureAgent, setStructureAgent] = createSignal(readBooleanPreference(preferenceKeys.structureAgent, false));
  const [structureAgentStatus, setStructureAgentStatus] = createSignal(readBooleanPreference(preferenceKeys.structureAgentStatus, true));
  const [structureRepresentation, setStructureRepresentation] = createSignal<'cartoon'|'ball-and-stick'|'molecular-surface'|'spacefill'|'line'>(readPreference(preferenceKeys.structureRepresentation, 'cartoon'));
  const [structureRememberScene, setStructureRememberScene] = createSignal(readBooleanPreference(preferenceKeys.structureRememberScene, true));
  const [structureMeasurementPrecision, setStructureMeasurementPrecision] = createSignal<'1'|'2'|'3'>(readPreference(preferenceKeys.structureMeasurementPrecision, '3'));
  const [models, setModels] = createSignal<ModelOption[]>([]);
  const [selectedModel, setSelectedModel] = createSignal('');
  const [ontologyPolicy, setOntologyPolicy] = createSignal({ public: true, private: true });
  const [learningEnabled, setLearningEnabled] = createSignal(true);
  const [resultVerification, setResultVerification] = createSignal(true);
  const [verificationMode, setVerificationMode] = createSignal<'light' | 'standard' | 'strict'>('standard');
  const [resultVerificationReady, setResultVerificationReady] = createSignal(false);
  const [savedVerification, setSavedVerification] = createSignal<VerificationSettings>();
  const [learningEditable, setLearningEditable] = createSignal(true);
  type ArtifactEntry = { service_id: string; name: string; url: string; token_env: string; targets: string; auth_mode?: 'personal-tokens' };
  const [personalAvailable, setPersonalAvailable] = createSignal(false);
  const [myBindings, setMyBindings] = createSignal<string[]>([]);
  const [savedBindings, setSavedBindings] = createSignal<string[]>([]);
  const [artifactServices, setArtifactServices] = createSignal<ArtifactEntry[]>([]);
  const [defaultArtifact, setDefaultArtifact] = createSignal('');
  const [artifactEditor, setArtifactEditor] = createSignal<number | null>(null);
  const [savedArtifacts, setSavedArtifacts] = createSignal<ArtifactEntry[]>([]);
  const [savedDefault, setSavedDefault] = createSignal('');
  const removedBindings = () => savedBindings().filter(id =>
    !savedArtifacts().some(entry => entry.service_id === id && entry.auth_mode === 'personal-tokens'));
  function updateBinding(id: string, bound: boolean) {
    const update = (ids: string[]) => [...ids.filter(key => key !== id), ...(bound ? [id] : [])];
    setMyBindings(update);
    setSavedBindings(update);
  }
  const artifactDirty = () => savedArtifacts() !== artifactServices() || savedDefault() !== defaultArtifact();
  const rememberArtifacts = () => {
    setSavedArtifacts(artifactServices());
    setSavedDefault(defaultArtifact());
  };
  const addArtifact = () => {
    setArtifactEditor(artifactServices().length);
    setArtifactServices(items => [...items, { service_id: '', name: '', url: '', token_env: '', targets: '' }]);
  };
  const [artifactSaving, setArtifactSaving] = createSignal(false);
  const [artifactEditable, setArtifactEditable] = createSignal(true);
  type ArtifactCheck = { state: string; reason: string; tested_at?: string };
  const [artifactChecks, setArtifactChecks] = createSignal(new Map<ArtifactEntry, ArtifactCheck>());
  async function testArtifact(entry: ArtifactEntry) {
    const setCheck = (check: ArtifactCheck) => setArtifactChecks(previous => new Map(previous).set(entry, check));
    setCheck({ state: 'checking', reason: '' });
    try {
      const response = await fetch('/api/artifact-services/test', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service_id: entry.service_id, url: entry.url, token_env: entry.token_env }),
      });
      if (!response.ok) throw new Error('test unavailable');
      const check = await response.json();
      if (artifactServices().includes(entry)) setCheck(check);
    } catch {
      if (artifactServices().includes(entry)) setCheck({ state: 'failed', reason: '无法测试，请检查登录权限或后端连接。' });
    }
  }
  const artifactConfigUrl = platform.artifactConfigUrl;
  const updateArtifact = (index: number, patch: Partial<ArtifactEntry>) => {
    const old = artifactServices()[index];
    if (patch.service_id !== undefined && old.service_id && defaultArtifact() === old.service_id) setDefaultArtifact(patch.service_id);
    setArtifactServices(items => items.map((item, i) => i === index ? { ...item, ...patch } : item));
  };
  const [desktopConfigAvailable, setDesktopConfigAvailable] = createSignal(false);
  const [servers, setServers] = createSignal<MCPConfigServer[]>([]);
  const [mcpListState, setMcpListState] = createSignal<'loading' | 'ready' | 'failed'>('loading');
  const [canEdit, setCanEdit] = createSignal(false);
  const [configFile, setConfigFile] = createSignal('');
  const [editing, setEditing] = createSignal<string | null>(null);
  const [name, setName] = createSignal('');
  const [url, setUrl] = createSignal('');
  const [transport, setTransport] = createSignal<'streamable-http' | 'sse'>('streamable-http');
  const [invocationMode, setInvocationMode] = createSignal<'auto' | 'direct' | 'async'>('direct');
  const [fileTransfer, setFileTransfer] = createSignal<MCPFileTransfer | null>(null);
  const [personalFiles, setPersonalFiles] = createSignal(false);
  const [personalToken, setPersonalToken] = createSignal('');
  const [enabled, setEnabled] = createSignal(true);
  const [connectTimeout, setConnectTimeout] = createSignal(10000);
  const [timeout, setTimeout] = createSignal(30000);
  const [headers, setHeaders] = createSignal<HeaderRow[]>([]);
  const [dynamicHeaders, setDynamicHeaders] = createSignal<DynamicHeaders>({});
  const [existingHeaders, setExistingHeaders] = createSignal<string[]>([]);
  const [status, updateStatus] = createSignal('');
  const [statusSuccess, setStatusSuccess] = createSignal(false);
  const setStatus = (message: string) => { setStatusSuccess(false); updateStatus(message); };
  const savedStatus = (message: string) => { updateStatus(message); setStatusSuccess(true); };
  const [busy, setBusy] = createSignal(false);

  const loadMcp = async () => {
    try {
      const data = await getMCPConfig(props.workspaceId);
      setServers(data.servers); setCanEdit(data.can_edit); setConfigFile(data.config_file); setMcpListState('ready');
    } catch (error) { setMcpListState('failed'); setStatus(error instanceof Error ? error.message : 'MCP 配置加载失败'); }
  };
  onMount(() => {
    void getChatRuntimes(sessionStore.currentSessionId() || undefined).then(data => {
      setSchedulerCapabilities(data.runtimes.find(item => item.runtime_id === 'tcode-native')?.capabilities);
      if (!autoSwarm().available && scheduler() === 'auto_swarm_builder') setScheduler('supervisor');
    }).catch(() => {
      setSchedulerCapabilities(undefined);
      if (scheduler() === 'auto_swarm_builder') setScheduler('supervisor');
    });
    void fetch('/api/config/result-verification').then(async response => {
      const data = await response.json();
      if (!response.ok) throw new Error('结果验证设置加载失败');
      setResultVerification(data.enabled); setVerificationMode(data.mode || 'standard'); setResultVerificationReady(true);
      setSavedVerification(data);
    }).catch(error => setStatus(error instanceof Error ? error.message : '结果验证设置加载失败'));
    void canvasPreference().then(value => {
      setGenerateIntelligentUI(value.enabled);
      setCanvasStyle(value.style || 'default'); setCanvasDefault(value.default_style); setCanvasPreferenceReady(true);
    }).catch(error => setStatus(error instanceof Error ? error.message : '智能 UI 设置加载失败'));
    void loadMcp();
    void getModels(sessionStore.currentSessionId() || undefined, props.workspaceId).then(data => { setModels(data.models); setSelectedModel(data.current_model); }).catch(() => setStatus('模型配置暂不可用'));
    void getAgents(sessionStore.currentSessionId() || undefined, props.workspaceId).then(data => {
      const available = Array.isArray(data) ? data.filter(item => item?.name) : [];
      setAgents(available);
      if (!available.some(item => item.name === defaultAgent())) setDefaultAgent(available.some(item => item.name === 'build') ? 'build' : (available[0]?.name || 'build'));
    }).catch(() => setStatus('Agent 列表暂不可用'));
    fetch(artifactConfigUrl).then(async response => {
      if (!response.ok) throw new Error('配置接口不可用');
      const data = await response.json();
      const services = data.artifact_services || (data.artifact_service_url ? { default: { url: data.artifact_service_url } } : {});
      setArtifactServices(Object.entries(services).map(([service_id, value]) => {
        const entry = value as Partial<ArtifactEntry>;
        return { service_id, name: entry.name || service_id, url: entry.url || '', token_env: entry.token_env || '', auth_mode: entry.auth_mode,
          targets: Object.entries(data.artifact_target_services || {}).filter(([, id]) => id === service_id).map(([target]) => target).join(', ') };
      }));
      setDefaultArtifact(data.artifact_default_service_id ?? (data.artifact_service_url ? 'default' : ''));
      setArtifactEditable(data.can_edit !== false);
      setPersonalAvailable(data.personal_available === true);
      setMyBindings(data.personal_bound || []);
      setSavedBindings(data.personal_saved || []);
      rememberArtifacts();
      setDesktopConfigAvailable(true);
    }).catch(() => setStatus('远端服务配置暂不可用'));
  });
  createEffect(() => {
    const workspace = props.workspaceId;
    const sessionId = sessionStore.currentSessionId();
    if (!workspace) return;
    void ontologyRequest<{ public: boolean; private: boolean }>(workspace, '/source-policy')
      .then(policy => setOntologyPolicy({ public: policy.public, private: policy.private }))
      .catch(() => setStatus('本体知识源设置暂不可用'));
    if (!sessionId) return;
    const params = new URLSearchParams({ session_id: sessionId, workspace_id: workspace });
    void fetch(`/api/config/learning?${params}`).then(async response => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(String(data.detail || '本地经验学习设置加载失败'));
      setLearningEnabled(Boolean(data.enabled));
      setLearningEditable(data.editable !== false);
    }).catch(error => setStatus(error instanceof Error ? error.message : '本地经验学习设置加载失败'));
  });

  const saveLocal = async () => {
    if (!canvasPreferenceReady() || busy()) return;
    setBusy(true);
    setStatus('正在保存…');
    try {
    const saved = await canvasPreference({ style: canvasStyle() === 'default' ? null : canvasStyle() as CanvasStyle, enabled: generateIntelligentUI() });
    setGenerateIntelligentUI(saved.enabled);
    setCanvasDefault(saved.default_style);
    localStorage.setItem('tcode.settings.compact', compact() ? '1' : '0');
    localStorage.setItem('tcode.settings.reduceMotion', reduceMotion() ? '1' : '0');
    document.documentElement.dataset.compact = compact() ? 'true' : 'false';
    document.documentElement.dataset.reduceMotion = reduceMotion() ? 'true' : 'false';
    savedStatus('常规偏好已保存');
    announcePreferenceChange();
    } catch (error) { setStatus(error instanceof Error ? error.message : '常规偏好保存失败'); }
    finally { setBusy(false); }
  };
  const saveNotifications = () => {
    localStorage.setItem('tcode.settings.notify', notify() ? '1' : '0');
    localStorage.setItem('tcode.settings.inAppNotify', inAppNotify() ? '1' : '0');
    window.dispatchEvent(new CustomEvent('tcode:settings-changed'));
    savedStatus('通知偏好已保存');
  };
  const saveArtifact = async () => {
    if (artifactSaving() || !artifactEditable()) return;
    const services: Record<string, { name: string; url: string; token_env: string; auth_mode?: string }> = Object.create(null);
    const routes: Record<string, string> = Object.create(null);
    for (const item of artifactServices()) {
      const id = item.service_id.trim();
      if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(id) || id in services) { setStatus('服务 ID 无效或重复，请使用 1–64 位字母、数字、点、下划线或连字符。'); return; }
      services[id] = { name: item.name.trim() || id, url: item.url.trim(), token_env: item.token_env.trim(), auth_mode: item.auth_mode };
      for (const target of item.targets.split(/[,，\n]/).map(value => value.trim()).filter(Boolean)) {
        if (target === '*') { setStatus('请填写具体的 MCP 名称，或留空。'); return; }
        if (target in routes) { setStatus(`MCP ${target} 只能绑定一个 Artifact 服务。`); return; }
        routes[target] = id;
      }
    }
    if (defaultArtifact() && !(defaultArtifact().trim() in services)) { setStatus('请选择默认 Artifact 服务。'); return; }
    setArtifactSaving(true); setStatus('正在保存…');
    try {
      const response = await fetch(artifactConfigUrl, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ artifact_services: services, artifact_default_service_id: defaultArtifact().trim(), artifact_target_services: routes }) });
      const data = await response.json().catch(() => ({}));
      if (response.ok) {
        rememberArtifacts(); setArtifactEditor(null);
        savedStatus(`Artifact 配置已保存，重启${desktop ? '本地服务' : 'TCode 服务'}后生效；请先等待运行中的任务结束。`);
      }
      else setStatus(String(data.detail || '保存失败'));
    } catch { setStatus('保存失败，请检查本地服务连接；当前编辑内容已保留。'); }
    finally { setArtifactSaving(false); }
  };
  const saveExecution = async () => {
    savePreference(preferenceKeys.mcpMode, defaultMcpMode());
    savePreference(preferenceKeys.agent, defaultAgent());
    savePreference(preferenceKeys.reasoning, reasoning());
    savePreference(preferenceKeys.scheduler, scheduler());
    const currentSessionId = sessionStore.currentSessionId();
    if (currentSessionId) {
      sessionStore.updateSessionReasoningEffort(currentSessionId, reasoning());
      sessionStore.updateSessionSchedulerMode(currentSessionId, scheduler());
    } else {
      sessionStore.setSelectedReasoningEffort(reasoning());
      sessionStore.setSelectedSchedulerMode(scheduler());
    }
    setBusy(true); setStatus('正在保存…');
    try {
      const response = await fetch('/api/config/result-verification', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: resultVerification(), mode: verificationMode() }),
      });
      if (!response.ok) throw new Error('结果验证设置保存失败');
      const saved: VerificationSettings = await response.json();
      setSavedVerification(saved);
      setResultVerification(saved.enabled); setVerificationMode(saved.mode);
      if (selectedModel()) await selectModel(selectedModel(), sessionStore.currentSessionId() || undefined, props.workspaceId);
      announcePreferenceChange();
      savedStatus('模型与执行偏好已保存，立即用于当前任务和后续新任务');
    } catch (error) { setStatus(error instanceof Error ? error.message : '保存失败'); }
    finally { setBusy(false); }
  };
  const saveCollaboration = () => {
    savePreference(preferenceKeys.officeAnimations, officeAnimations()); savePreference(preferenceKeys.officePauseInactive, officePauseInactive()); savePreference(preferenceKeys.officeRememberZoom, officeRememberZoom());
    announcePreferenceChange(); savedStatus('协作偏好已保存并生效');
  };
  const saveStructure = () => {
    savePreference(preferenceKeys.structureAgent, structureAgent()); savePreference(preferenceKeys.structureAgentStatus, structureAgentStatus()); savePreference(preferenceKeys.structureRepresentation, structureRepresentation()); savePreference(preferenceKeys.structureRememberScene, structureRememberScene()); savePreference(preferenceKeys.structureMeasurementPrecision, structureMeasurementPrecision());
    announcePreferenceChange(); savedStatus('三维结构偏好已保存并生效');
  };
  const saveKnowledge = async () => {
    const sessionId = sessionStore.currentSessionId();
    if (!props.workspaceId || !sessionId) { setStatus('请先选择工作区并新建任务'); return; }
    setBusy(true); setStatus('正在保存…');
    try {
      await ontologyRequest(props.workspaceId, '/source-policy', ontologyPolicy());
      if (learningEditable()) {
        const params = new URLSearchParams({ session_id: sessionId, workspace_id: props.workspaceId });
        const response = await fetch(`/api/config/learning?${params}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: learningEnabled() }) });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(String(data.detail || '本地经验学习设置保存失败'));
      }
      announcePreferenceChange();
      savedStatus('本体知识与本地经验学习设置已保存，无需重启');
    }
    catch (error) { setStatus(error instanceof Error ? error.message : '保存失败'); }
    finally { setBusy(false); }
  };
  const resetEditor = () => { setPersonalToken(''); setPersonalFiles(false); setEditing(null); setName(''); setUrl(''); setTransport('streamable-http'); setInvocationMode('direct'); setEnabled(true); setConnectTimeout(10000); setTimeout(30000); setHeaders([]); setDynamicHeaders({}); setFileTransfer(null); setExistingHeaders([]); };
  const editServer = (server?: MCPConfigServer) => {
    if (!server) { resetEditor(); setEditing(''); return; }
    setPersonalFiles(Boolean(server.personal_artifact)); setPersonalToken('');
    setEditing(server.name); setName(server.name); setUrl(server.url); setTransport(server.transport); setInvocationMode(server.invocation_mode); setEnabled(server.enabled);
    setConnectTimeout(server.connect_timeout); setTimeout(server.timeout); setExistingHeaders(server.header_names); setHeaders([]); setFileTransfer(server.file_transfer || null); setDynamicHeaders(server.dynamic_headers || {});
  };
  const saveServer = async () => {
    const serverName = name().trim();
    if (!serverName || !url().trim()) { setStatus('请填写服务名称和地址'); return; }
    setBusy(true); setStatus('正在保存并连接…');
    try {
      const secretHeaders = Object.fromEntries(headers().filter(row => row.name.trim() && row.value).map(row => [row.name.trim(), row.value]));
      if (desktop && personalFiles() && personalToken().trim()) secretHeaders.Authorization = 'Bearer ' + personalToken().trim();
      await saveMCPConfig(serverName, { url: url().trim(), transport: transport(), invocation_mode: invocationMode(), submit_via: 'job-protocol-mcp', enabled: enabled(), connect_timeout: connectTimeout(), timeout: timeout(), headers: secretHeaders, dynamic_headers: dynamicHeaders(), file_transfer: fileTransfer(), ...(desktop ? {personal_artifact: personalFiles()} : {}) }, props.workspaceId);
      const adapterRequired = fileTransfer()?.receiver === 'server_paths';
      resetEditor(); await loadMcp(); savedStatus(adapterRequired ? '配置已保存，但文件传输尚未接通：需要服务器适配器' : 'MCP 已保存，连接配置已重新加载（未提交计算）');
    } catch (error) { setStatus(error instanceof Error ? error.message : '保存失败'); }
    finally { setBusy(false); }
  };
  const removeServer = async (serverName: string) => {
    if (!await confirmAction(`删除 MCP“${serverName}”？`)) return;
    setBusy(true);
    try { await deleteMCPConfig(serverName, props.workspaceId); resetEditor(); await loadMcp(); setStatus('MCP 已删除'); }
    catch (error) { setStatus(error instanceof Error ? error.message : '删除失败'); }
    finally { setBusy(false); }
  };
  const addHeader = () => setHeaders(rows => [...rows, { name: '', value: '' }]);
  const updateHeader = (index: number, field: keyof HeaderRow, value: string) => setHeaders(rows => rows.map((row, i) => i === index ? { ...row, [field]: value } : row));
  const tip = (text: string) => <span class="settings-tip" tabindex="0" title={text} aria-label={`提示：${text}`} onClick={event=>event.preventDefault()}>i<span role="tooltip">{text}</span></span>;
  const toggle = (label: string, detail: string, value: boolean, setter: (next: boolean) => void, disabled = false, tipText = '') => <label class="settings-toggle"><span><b>{label}{tipText ? tip(tipText) : null}</b><small>{detail}</small></span><input type="checkbox" checked={value} disabled={disabled} onChange={event=>setter(event.currentTarget.checked)}/><i aria-hidden="true"/></label>;
  const selectRow = (label: string, detail: string, control: unknown, tipText = '') => <label class="settings-select-row"><span><b>{label}{tipText ? tip(tipText) : null}</b><small>{detail}</small></span>{control as any}</label>;
  const visibleSections = () => {
    const value = query().trim().toLowerCase();
    const available = availableSettingsSections(Boolean(props.account), Boolean(props.workspaceId));
    return value ? available.filter(item => `${item.label}${item.detail}${item.group}`.toLowerCase().includes(value)) : available;
  };
  const ontologyMode = () => ontologyPolicy().public ? ontologyPolicy().private ? 'both' : 'public' : ontologyPolicy().private ? 'private' : 'none';
  const setOntologyMode = (mode: string) => setOntologyPolicy({ public: mode === 'public' || mode === 'both', private: mode === 'private' || mode === 'both' });

  let content: HTMLElement | undefined;
  createEffect(() => {
    section();
    if (content) content.scrollTop = 0;
  });

  function McpServiceRow(props: { server: MCPConfigServer }) {
    const server = props.server;
    const [testing, setTesting] = createSignal(false);
    const [check, setCheck] = createSignal<{ ok: boolean; message: string }>();
    async function test() {
      setTesting(true); setCheck(undefined);
      try {
        const result = await testMCPConnection(server.name);
        setCheck({ ok: true, message: `连接正常 · 发现 ${result.tools} 个工具` });
      } catch (error) {
        setCheck({ ok: false, message: error instanceof Error ? error.message : '连接测试失败' });
      } finally { setTesting(false); }
    }
    return <article class="service-row" aria-label={server.name}>
      <div class="service-summary">
        <h4>{server.name}<small>{server.enabled ? '已启用' : '已停用'}</small></h4>
        <p>{server.managed_message || server.url}</p>
        <small>{server.editable === false ? '平台托管' : server.invocation_mode === 'direct' ? '直接调用' : server.invocation_mode === 'async' ? `异步任务 · ${server.submit_via}` : '自动判断'}</small>
        <Show when={server.file_transfer}><p>{server.file_transfer_status === 'adapter_required' ? '文件传输需要服务器适配器' : server.file_transfer_status === 'registered' ? '文件传输已注册，读取需授权' : server.file_transfer_status === 'disabled' ? '文件传输已停用' : '文件传输待启用，请编辑后保存'}</p></Show>
      </div>
      <div class="service-actions">
        <button type="button" class="service-test" disabled={!canEdit() || !server.enabled || testing()} title={!server.enabled ? '启用服务后可测试连接' : '验证 MCP 握手与工具发现'} onClick={()=>void test()}>{testing() ? '测试中…' : '测试连接'}</button>
        <Show when={server.editable !== false && canEdit()}><button type="button" onClick={()=>editServer(server)}>编辑</button></Show>
      </div>
      <Show when={check()}>{result=><p class="service-result" data-state={result().ok ? 'connected' : 'failed'} role="status">{result().message}</p>}</Show>
    </article>;
  }

  return <div class="settings-workspace">
    <nav class="settings-nav" aria-label="设置分类">
      <label class="settings-search">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></svg>
        <input type="search" value={query()} onInput={event=>setQuery(event.currentTarget.value)} placeholder="搜索设置" aria-label="搜索设置"/>
      </label>
      <div class="settings-category-list">
        <For each={settingsGroups}>{group=><Show when={visibleSections().some(item=>item.group===group)}>
          <div class="settings-nav-group">
            <h2>{group}</h2>
            <For each={visibleSections().filter(item=>item.group===group)}>{item=>
              <button type="button" aria-current={section()===item.id ? 'page' : undefined} onClick={()=>setSection(item.id)}>
                {item.label}
                <Show when={section()===item.id}><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12 4 4 10-10"/></svg></Show>
              </button>
            }</For>
          </div>
        </Show>}</For>
        <Show when={!visibleSections().length}><p class="settings-empty" role="status">没有匹配的设置</p></Show>
      </div>
      <div class="settings-compact-nav">
        <span>{visibleSections().find(item=>item.id===section())?.group ?? '搜索结果'}</span>
        <select class="settings-category-picker" aria-label="设置分类"
          value={visibleSections().some(item=>item.id===section()) ? section() : ''}
          disabled={!visibleSections().length}
          onChange={event=>{if (event.currentTarget.value) setSection(event.currentTarget.value as SettingsSection);}}>
          <Show when={!visibleSections().some(item=>item.id===section())}>
            <option value="" disabled>{visibleSections().length ? '选择匹配的设置' : '没有匹配的设置'}</option>
          </Show>
          <For each={settingsGroups}>{group=><Show when={visibleSections().some(item=>item.group===group)}>
            <optgroup label={group}><For each={visibleSections().filter(item=>item.group===group)}>{item=>
              <option value={item.id} selected={section()===item.id}>{item.label}</option>
            }</For></optgroup>
          </Show>}</For>
        </select>
      </div>
    </nav>
    <main class="settings-main" ref={content}>
      <Show when={section() === 'account' && props.account}>{user =>
        <AccountSecuritySettings user={user()} onPasswordSaved={() => props.onPasswordSaved?.()} />
      }</Show>
      <Show when={section()==='general'}><div class="settings-page"><h2>常规</h2><div class="settings-section"><h3>外观</h3><section class="settings-group">{toggle('生成智能 UI','关闭后，新请求以普通方式回答，已有图解和交互页面仍可查看。',generateIntelligentUI(),setGenerateIntelligentUI,!canvasPreferenceReady() || busy())}{selectRow('智能 UI 风格','应用于图解和交互页面；不会重写已有图的配色或几何。',<select aria-label="智能 UI 风格" value={canvasStyle()} disabled={!canvasPreferenceReady() || busy()} onChange={event => setCanvasStyle(event.currentTarget.value as CanvasStyle | 'default')}><option value="default">跟随默认（{canvasDefault() === 'clay' ? '粘土拟态' : '清爽说明书'}）</option><option value="clay">粘土拟态</option><option value="manual">清爽说明书</option></select>)}{toggle('紧凑布局','缩小导航、列表与面板的间距，在同一屏显示更多内容。',compact(),setCompact)}{toggle('减少界面动效','关闭非必要的过渡与动画，状态反馈仍会保留。',reduceMotion(),setReduceMotion,false,'同时影响办公室人物动画、侧边栏悬停放大和页面过渡，不会隐藏任务运行状态。')}</section></div><button class="settings-save" disabled={busy() || !canvasPreferenceReady()} onClick={saveLocal}>保存更改</button></div></Show>
      <Show when={section()==='notifications'}><div class="settings-page"><h2>通知</h2><div class="settings-section"><h3>任务提醒</h3><section class="settings-group"><Show when={desktop}>{toggle('系统通知','异步任务完成、失败或取消时发送桌面系统通知。',notify(),setNotify,false,'需要操作系统授予 TCode 通知权限，仅桌面端可用。')}</Show>{toggle('应用内提醒','在窗口右下角显示最多三条任务完成卡片。',inAppNotify(),setInAppNotify)}</section></div><button class="settings-save" onClick={saveNotifications}>保存更改</button></div></Show>
      <Show when={section()==='models'}><div class="settings-page"><h2>模型与执行</h2><p>设置当前任务和后续新任务的默认模型与运行方式。</p>
        <div class="settings-section"><h3>模型</h3><section class="settings-group">
          <Show when={models().length}>{selectRow('默认文本模型','保存后立即切换当前任务，并写入当前工作区配置。',<select value={selectedModel()} disabled={busy()} onChange={event=>setSelectedModel(event.currentTarget.value)}><For each={models()}>{model=><option value={model.id}>{model.name}（{model.id}）</option>}</For></select>,'切换模型不会中断正在生成的回复，下一次发送开始使用新模型。')}</Show>
          {selectRow('默认推理强度','可在聊天输入框旁为单次任务覆盖。',<select value={reasoning()} onChange={event=>setReasoning(event.currentTarget.value as any)}><option value="minimal">最低</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select>,'推理强度越高，复杂任务通常更稳，但响应时间和 token 消耗可能增加。')}
        </section></div>
        <div class="settings-section"><h3>任务调度</h3><section class="settings-group">
          <Show when={agents().length}>{selectRow('默认 Agent 模式','用于后续新建任务，不改变当前任务。',<select value={defaultAgent()} onChange={event=>setDefaultAgent(event.currentTarget.value)}><For each={agents()}>{agent=><option value={agent.name}>{agentDisplayLabel(agent.name)}</option>}</For></select>,'这是新建任务的起始 Agent。进入任务后，仍可在输入区切换 Agent，不会反向修改此默认值。')}</Show>
          {selectRow('默认调度方式','用于后续新任务，当前任务也会同步更新。',<select value={scheduler()} onChange={event=>setScheduler(event.currentTarget.value as any)}><SchedulerOptions autoSwarm={autoSwarm()} /></select>,'部分 Agent 会固定自己的调度方式，此时该默认值会被 Agent 配置覆盖。')}
          <Show when={!autoSwarm().available}><p class="text-xs leading-5 text-[#9faeb7]" role="status">自动群体构建不可用：{autoSwarm().reason}</p></Show>
          {selectRow('新 MCP 默认调用方式','添加 MCP 时预先选择，单个连接仍可覆盖。',<select value={defaultMcpMode()} onChange={event=>setDefaultMcpMode(event.currentTarget.value as any)}><option value="async">异步任务</option><option value="auto">自动判断</option><option value="direct">直接调用</option></select>,'长时间计算建议使用异步任务，关闭窗口后远程任务仍可继续。')}
        </section></div>
        <div class="settings-section"><h3>结果验证</h3><section class="settings-group">
          {toggle('结果验证（额外消耗 Token）','控制系统自动追加的验证；Agent 主动调用和 Workflow 明确指定的审核不受影响，仍可能消耗 Token。',resultVerification(),setResultVerification,!resultVerificationReady() || busy(),'关闭：跳过 Outcome Planner 的模型规划、Outcome Guard 验证、Worker 完成后的自动 Critic / Verifier 审查及工具重试的 Critic 模型判断；保留格式、权限、安全和重复调用等基础检查。Agent 主动委派的 Critic / Verifier，以及用户或 Workflow 明确指定的审核步骤仍会执行，可能消耗 Token。开启：恢复系统自动验证；最终结果验证按下方档位提供参考置信度和局限，未通过也会交付回答，不要求确认。')}
          {selectRow('验证档位','轻量：主要目标，0 次修复；标准：全部任务要求，最多 1 次；严格：逐项证据与冲突，最多 2 次。',<select aria-label="验证档位" value={verificationMode()} disabled={!resultVerification() || !resultVerificationReady() || busy()} onChange={event=>setVerificationMode(event.currentTarget.value as 'light' | 'standard' | 'strict')}><option value="light">轻量</option><option value="standard">标准</option><option value="strict">严格</option></select>,'轻量：只审查首个主要语义验收项，送审回答最多 1.2 万字符、6 条证据，模型输出预算上限 1024 Token、单次最长 30 秒，不自动修复。标准（默认）：审查全部任务要求，最多 4.8 万字符、20 条证据、2048 Token、60 秒，最多修复 1 次。严格：要求模型逐项给出结论，并检查已声明的文件大小或摘要，最多 6.4 万字符、40 条证据、4096 Token、90 秒，最多修复 2 次。部署配置和任务自身上限可能更低。所有档位均仅供参考，不自动重跑计算。')}
        </section>
        <section class="settings-group" aria-label="验证实际生效状态">
          <div class="settings-select-row"><span><strong>{verificationEffectiveLabel(savedVerification())}</strong><small>以下为服务端已保存配置，不代表每个任务都会调用验证模型。</small></span></div>
          <For each={Object.entries(verificationStageLabels)}>{([key, label]) => <div class="settings-select-row"><span>{label}</span><span>{verificationStageStatus(savedVerification()?.effective?.stages[key])}</span></div>}</For>
          <Show when={savedVerification() && (savedVerification()!.enabled !== resultVerification() || savedVerification()!.mode !== verificationMode())}><p class="settings-footnote">有未保存的更改，实际生效状态将在保存后更新。</p></Show>
          <Show when={savedVerification()?.effective?.policy}>{policy => <p class="settings-footnote">Guard、Worker 和重试审核单次判断输出上限 {policy().max_tokens} Token；Worker 审查阶段最长 {policy().timeout} 秒，最多修订 {policy().repairs} 次。Worker 只修订回答文字，不重跑工具或改派；部署或任务限制可能进一步降低预算。</p>}</Show>
          <p class="settings-footnote">Workflow 最终追加验证仅在流程声明完成验收时触发。主动委派和 Workflow 指定审核仍保留。状态未知时请刷新设置页面。</p>
        </section><p class="settings-footnote">三个档位均只提供参考，不阻塞继续使用。自动修复只利用已有结果，并受任务自身上限约束，不自动重跑计算。设置保存到当前账户，已发出的请求不会被中断。</p></div>
        <button class="settings-save" disabled={busy() || !resultVerificationReady()} onClick={()=>void saveExecution()}>保存更改</button>
        <details class="settings-service-details settings-provider-details">
          <summary>管理模型供应商</summary>
          {ModelSettings && <ModelSettings onChanged={()=>{void getModels(sessionStore.currentSessionId() || undefined, props.workspaceId).then(data=>{setModels(data.models);setSelectedModel(data.current_model);announcePreferenceChange();});}}/>}
          <Show when={!desktop}><WebModelSettings onChanged={()=>{
            void getModels(sessionStore.currentSessionId() || undefined, props.workspaceId)
              .then(data => { setModels(data.models); setSelectedModel(data.current_model); announcePreferenceChange(); })
              .catch(() => setStatus('模型列表刷新失败，请重新打开设置'));
          }}/></Show>
        </details>
      </div></Show>
      <Show when={section()==='feishu'}><FeishuSettings workspaceId={props.workspaceId}/></Show>
      <Show when={section()==='websearch'}><WebSearchSettings/></Show>
      <Show when={section()==='internet-content'}>{InternetContentSettings ? <InternetContentSettings workspaceId={props.workspaceId}/> : <div class="settings-page"><h2>互联网渠道</h2><p>请在 TCode 桌面端管理渠道。</p></div>}</Show>
      <Show when={section()==='collaboration'}><div class="settings-page"><h2>协作与 Agent</h2><p>控制协作办公室的默认表现，以及多 Agent 任务的界面行为。</p>
        <div class="settings-section"><h3>协作办公室</h3><section class="settings-group">
          {toggle('默认播放办公室动画','进入协作办公室时播放人物与状态动画。',officeAnimations(),setOfficeAnimations,false,'只控制视觉动画，不会暂停 Agent 任务。')}
          {toggle('非活动页面暂停动画','办公室不在当前页面时停止动画，降低 CPU 与 GPU 占用。',officePauseInactive(),setOfficePauseInactive,false,'页面回到前台后会继续动画，Agent 在后台照常运行。')}
          {toggle('记住办公室缩放','按当前任务保存最后一次缩放比例。',officeRememberZoom(),setOfficeRememberZoom)}
        </section></div>
        <div class="settings-note"><strong>即时生效</strong><span>保存后，已经打开的协作办公室会立即采用新的动画设置。</span></div>
        <button class="settings-save" onClick={saveCollaboration}>保存更改</button>
      </div></Show>
      <Show when={section()==='structure'}><div class="settings-page"><h2>三维结构</h2><p>设置 Mol* 查看器与 Agent 协作时的默认行为。</p>
        <div class="settings-section"><h3>Agent 操作</h3><section class="settings-group">
          {toggle('默认允许 Agent 操作视图','允许 Agent 读取结构状态并执行选择、聚焦、旋转和测距。',structureAgent(),setStructureAgent,false,'这是能力授权。关闭后，Agent 不能读取当前结构、选区或控制相机。')}
          {toggle('显示 Agent 操作视图','显示连接状态、接管按钮和当前授权开关。',structureAgentStatus(),setStructureAgentStatus,false,'只控制操作栏是否显示，不会单独改变授权状态。')}
        </section></div>
        <div class="settings-section"><h3>显示与视角</h3><section class="settings-group">
          {selectRow('默认结构表示','新打开且没有保存视角的结构使用此表示方式。',<select value={structureRepresentation()} onChange={event=>setStructureRepresentation(event.currentTarget.value as any)}><option value="cartoon">Cartoon</option><option value="ball-and-stick">球棍</option><option value="molecular-surface">分子表面</option><option value="spacefill">空间填充</option><option value="line">线框</option></select>,'如果该结构已保存过视角，会优先恢复保存的表示方式。')}
          {toggle('记住每个结构的视角','自动保存相机、选区和表示方式，下次打开同一结构时恢复。',structureRememberScene(),setStructureRememberScene,false,'视角按当前用户、工作区、会话和结构文件摘要保存在本机。')}
          {selectRow('测距小数位数','控制 Å 距离的显示精度。',<select value={structureMeasurementPrecision()} onChange={event=>setStructureMeasurementPrecision(event.currentTarget.value as any)}><option value="1">1 位</option><option value="2">2 位</option><option value="3">3 位</option></select>,'只改变数值的显示位数，不改变 Mol* 内部的测量精度。')}
        </section></div>
        <div class="settings-note settings-note-important"><strong>隐私提示</strong><span>允许 Agent 操作视图后，Agent 可以读取当前结构与选区，但结构坐标不会因此上传到外部网站。</span></div>
        <button class="settings-save" onClick={saveStructure}>保存更改</button>
      </div></Show>
      <Show when={section()==='knowledge'}><div class="settings-page"><h2>本体知识</h2><p>选择当前工作区允许 Agent 检索的已发布本体知识。</p>
        <div class="settings-section"><h3>本体知识源</h3><section class="settings-group">
          {selectRow('默认知识源','公共与私有范围可以组合使用，也可以完全关闭。',<select value={ontologyMode()} disabled={busy() || !props.workspaceId} onChange={event=>setOntologyMode(event.currentTarget.value)}><option value="public">仅公共本体</option><option value="private">仅当前工作区私有本体</option><option value="both">公共与私有本体</option><option value="none">不使用本体知识源</option></select>,'该设置只影响当前工作区。私有知识不会自动发布到公共本体。')}
        </section><Show when={!props.workspaceId}><p class="settings-footnote">选择工作区后可修改本体知识源。</p></Show></div>
        <Show when={learningEditable()}><div class="settings-section"><h3>本地经验</h3><section class="settings-group">
          {toggle('本地经验学习','从已完成任务中总结经验，帮助后续任务改进。',learningEnabled(),setLearningEnabled,!learningEditable() || busy(),'经验仅保存在当前工作区本地，不会训练或修改模型，也不会自动上传。关闭后停止生成和使用学习经验，但不删除已有记录。')}
        </section></div></Show>
        <button class="settings-save" disabled={busy() || !props.workspaceId || !sessionStore.currentSessionId()} onClick={()=>void saveKnowledge()}>保存更改</button>
      </div></Show>
      <Show when={section()==='mcp'}><div class="settings-page settings-page-wide service-page">
        <div class="settings-title-row"><div><h2>MCP 服务</h2></div><Show when={canEdit()}><button class="settings-save service-add" onClick={()=>editServer()}>添加常规 MCP</button></Show></div>
        <h3 class="service-list-title">常规服务 <Show when={mcpListState()==='ready'}><span>{servers().length}</span></Show></h3>
        <section class="service-list" aria-label="常规 MCP 服务列表">
          <Show when={mcpListState()==='ready'} fallback={<p class="service-empty">{mcpListState()==='failed' ? '无法读取服务列表。' : '正在读取服务…'}</p>}>
            <For each={servers()} fallback={<p class="service-empty">暂无常规 MCP 服务。{canEdit() && '点击「添加常规 MCP」连接第一个服务。'}</p>}>{server=><McpServiceRow server={server}/>}</For>
          </Show>
        </section>
        <Show when={editing() !== null}><FeedbackDialog title={editing() ? `编辑 ${editing()}` : '添加常规 MCP'} onClose={() => { if (!busy()) resetEditor(); }}><section class="settings-form mcp-editor"><div class="mcp-form-grid"><label>服务名称<input value={name()} disabled={Boolean(editing())} onInput={e=>setName(e.currentTarget.value)} placeholder="例如 literature-search"/></label><label>传输方式<select value={transport()} onChange={e=>setTransport(e.currentTarget.value as 'streamable-http'|'sse')}><option value="streamable-http">Streamable HTTP</option><option value="sse">SSE</option></select></label></div><label>MCP 地址<input value={url()} onInput={e=>setUrl(e.currentTarget.value)} placeholder="https://mcp.example.com/mcp"/></label><label>调用方式<select value={invocationMode()} onChange={e=>setInvocationMode(e.currentTarget.value as 'auto'|'direct'|'async')}><option value="auto">自动判断（遵循服务器策略）</option><option value="direct">直接调用</option><option value="async">异步执行（通过 Job Protocol）</option></select></label><Show when={invocationMode()==='async'}><p class="settings-callout">内置工具保持现有路由。自定义服务可在下方配置文件传输，无需制作插件；读取和计算仍遵循现有授权。</p><MCPFileTransferEditor value={fileTransfer()} onChange={setFileTransfer} disabled={busy()} connection={() => ({name: name(), url: url(), transport: transport(), headers: Object.fromEntries(headers().filter(r => r.name.trim() && r.value).map(r => [r.name.trim(), r.value]))})}/></Show><Show when={invocationMode() !== 'async' && fileTransfer()}><p role="alert">请先切回异步执行并关闭输入文件传输，再改为直接调用。</p></Show>
          <Show when={desktop}>
            <label><input type="checkbox" checked={personalFiles()} disabled={busy()} onChange={e=>{setPersonalFiles(e.currentTarget.checked);setPersonalToken('')}}/> 使用此 MCP 自带的个人文件服务</label>
            <Show when={personalFiles()}><label>个人访问令牌<input type="password" autocomplete="new-password" value={personalToken()} onInput={e=>setPersonalToken(e.currentTarget.value)} placeholder={existingHeaders().some(h=>h.toLowerCase()==='authorization')?'已保存，留空保留':'填写服务提供者签发的 Token'}/></label>
              <p class="settings-muted">保存时验证同一地址下的文件服务。聊天中可按服务名称列出和下载自己的文件。</p></Show>
          </Show>{toggle('启用此 MCP','关闭后保留配置，但不会连接或暴露工具。',enabled(),setEnabled)}<details><summary>高级设置</summary><div class="mcp-form-grid"><label>连接超时（毫秒）<input type="number" value={connectTimeout()} onInput={e=>setConnectTimeout(Number(e.currentTarget.value))}/></label><label>调用超时（毫秒）<input type="number" value={timeout()} onInput={e=>setTimeout(Number(e.currentTarget.value))}/></label></div><Show when={existingHeaders().length}><p class="settings-muted">已保存的请求头：{existingHeaders().join('、')}。留空会保留原值。</p></Show><For each={headers()}>{(row,index)=><div class="mcp-header-row"><input value={row.name} onInput={e=>updateHeader(index(),'name',e.currentTarget.value)} placeholder="Header 名称"/><input type="password" value={row.value} onInput={e=>updateHeader(index(),'value',e.currentTarget.value)} placeholder="值（保存后不回显）"/><button onClick={()=>setHeaders(rows=>rows.filter((_,i)=>i!==index()))}>移除</button></div>}</For><p class="settings-muted">固定值直接填写；环境变量使用 <code>{'${ENV_NAME}'}</code>，可与 Bearer 前缀组合，凭据不提供给 Agent。</p><button class="settings-link" onClick={addHeader}>添加固定值／环境变量请求头</button><DynamicHeadersEditor value={dynamicHeaders()} onChange={setDynamicHeaders} disabled={busy()}/></details><div class="mcp-editor-actions"><Show when={editing()}>{serverName=><button class="service-remove" disabled={busy()} onClick={()=>void removeServer(serverName())}>删除服务</button>}</Show><button onClick={resetEditor}>取消</button><button class="btn" disabled={busy()} onClick={()=>void saveServer()}>保存并连接</button></div><Show when={status()}><p role="status">{status()}</p></Show></section></FeedbackDialog></Show>
        <PluginAsyncTargetsSettings workspaceId={props.workspaceId}/><details class="settings-service-details"><summary>高级：配置文件与重新加载</summary><div class="settings-config-path" title={configFile()}>常规 MCP 配置文件：{configFile() || '读取中…'} <Show when={canEdit()}><button onClick={()=>void reloadMCPConfig().then(()=>setStatus('MCP 连接已重新加载')).catch(error=>setStatus(error.message))}>重新加载</button></Show></div></details><details class="settings-service-details"><summary>高级：连接诊断与代理路由</summary><div class="settings-embedded-panel"><MCPAndSkillsPanel embedded width={640}/></div></details></div></Show>
      <Show when={section()==='desktop'}><div class="settings-page settings-page-wide service-page">
        <div class="settings-title-row"><div><h2>Artifact 文件服务</h2></div>
          <Show when={desktopConfigAvailable() && artifactEditable()}><button class="settings-save service-add" disabled={artifactSaving() || artifactServices().length >= 64} onClick={addArtifact}>添加服务</button></Show>
        </div>
        <Show when={desktopConfigAvailable()} fallback={<p class="service-empty">{status() || '正在读取配置…'}</p>}>
        <h3 class="service-list-title">文件服务 <span>{artifactServices().length}</span></h3>
        <Show when={!artifactEditable()}><p class="settings-muted">服务地址由部署管理员管理；个人令牌由你自己绑定。</p></Show>
        <section class="service-list" aria-label="Artifact 服务配置列表">
          <Index each={artifactServices()} fallback={<p class="service-empty">暂无文件服务。{artifactEditable() && '点击「添加服务」配置文件存储。'}</p>}>{(entry, index) => <article class="service-row">
            <div class="service-summary"><h4>{entry().name || entry().service_id || '新文件服务'}
              <Show when={entry().service_id && entry().service_id === defaultArtifact()}><small>普通上传默认</small></Show>
              <Show when={!savedArtifacts().includes(entry())}><small>待保存</small></Show>
            </h4><p>{entry().url}</p><Show when={entry().targets}><p>绑定 MCP · {entry().targets}</p></Show></div>
            <div class="service-actions">
              <Show when={!entry().auth_mode}><button type="button" class="service-test" disabled={artifactSaving() || !artifactEditable() || !entry().service_id || !entry().url || artifactChecks().get(entry())?.state === 'checking'} onClick={() => void testArtifact(entry())}>{artifactChecks().get(entry())?.state === 'checking' ? '测试中…' : '测试连接'}</button></Show>
              <Show when={artifactEditable()}><button type="button" aria-expanded={artifactEditor()===index} onClick={()=>setArtifactEditor(artifactEditor()===index ? null : index)}>{artifactEditor()===index ? '收起' : '编辑'}</button></Show>
            </div>
            <Show when={artifactChecks().get(entry())}>{check=><p class="service-result" role="status" data-state={check().state}>
              {({ connected: '连接正常', reachable: '可达 · 认证未验证', failed: '测试失败', checking: '测试中…' } as Record<string,string>)[check().state] || '未验证'}{check().reason && ` · ${check().reason}`}
              <Show when={check().tested_at}>{time=><time dateTime={time()}>{new Date(time()).toLocaleTimeString()}</time>}</Show>
            </p>}</Show>
            <Show when={personalAvailable() && entry().auth_mode && savedArtifacts().includes(entry()) ? entry() : undefined} keyed>{item =>
              <PersonalArtifactBinding id={item.service_id} url={item.url} bound={myBindings().includes(item.service_id)} saved={savedBindings().includes(item.service_id)}
                changed={bound => updateBinding(item.service_id, bound)}/>
            }</Show>
            <Show when={artifactEditor()===index}><div class="artifact-settings-list settings-form"><fieldset disabled={artifactSaving() || !artifactEditable()}><legend>连接配置</legend>
            <label><span>服务 ID<span aria-hidden="true">（必填）</span></span><input ref={input => queueMicrotask(() => input.focus())} required value={entry().service_id} onInput={e => updateArtifact(index, { service_id: e.currentTarget.value })} placeholder="例如 store-eu" /></label>
            <label>显示名称<input value={entry().name} onInput={e => updateArtifact(index, { name: e.currentTarget.value })} /></label>
            <label><span>服务地址<span aria-hidden="true">（必填）</span></span><input type="url" required value={entry().url} onInput={e => updateArtifact(index, { url: e.currentTarget.value })} placeholder="https://artifacts.example.com" /></label>
            <Show when={personalAvailable()}><label><input type="checkbox" checked={!!entry().auth_mode}
              onChange={e => updateArtifact(index, {auth_mode: e.currentTarget.checked ? 'personal-tokens' : undefined})}/>按 Web 登录账号绑定个人令牌（仅同步文件）</label></Show>
            <label>凭据环境变量名（个人模式请留空）<input value={entry().token_env} onInput={e => updateArtifact(index, { token_env: e.currentTarget.value })} placeholder="ARTIFACT_STORE_TOKEN" /></label>
            <label>绑定的 MCP 名称（逗号分隔，可选）<input value={entry().targets} onInput={e => updateArtifact(index, { targets: e.currentTarget.value })} /></label>
            <label class="artifact-default"><input type="checkbox" disabled={!entry().service_id.trim()} checked={defaultArtifact() === entry().service_id && !!entry().service_id} onChange={e => setDefaultArtifact(e.currentTarget.checked ? entry().service_id : '')} />用作普通上传的默认服务（可选）</label>
            <p class="service-hint">{entry().auth_mode ? '保存并重启 Web 后，每位用户可在此绑定自己的令牌。' : '服务 ID 用于定位已有文件。共享凭据取自服务进程的环境变量。需要传文件的 MCP 请逐个填写名称，留空表示不绑定。'}</p>
            <button class="service-remove" onClick={() => { if (defaultArtifact() === entry().service_id) setDefaultArtifact(''); setArtifactServices(items => items.filter((_, i) => i !== index)); setArtifactEditor(null); }}>移除此配置（保留远端文件）</button>
          </fieldset></div></Show></article>}</Index>
        </section>
        <Show when={personalAvailable() && removedBindings().length > 0}>
          <h3 class="service-list-title">已失效的个人绑定</h3>
          <For each={removedBindings()}>{id => <PersonalArtifactBinding id={id} bound={false} saved
            changed={bound => updateBinding(id, bound)} />}</For>
        </Show>
        <Show when={artifactDirty()}><div class="service-save-bar"><span>有待保存的更改</span><button class="settings-save" disabled={!artifactEditable() || artifactSaving()} onClick={()=>void saveArtifact()}>{artifactSaving() ? '保存中…' : '保存 Artifact 配置'}</button></div></Show>
        </Show></div></Show>
      <Show when={status()}><div class="settings-status" data-success={statusSuccess()} role="status"><Show when={statusSuccess()}><svg class="tcode-success-check" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="m7 12 3 3 7-7"/></svg></Show>{status()}</div></Show>
    </main>
  </div>;
}
