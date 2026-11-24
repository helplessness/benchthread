import { isStudioPage, type StudioPage } from './studioLayout';
import { isSettingsSection, type SettingsSection } from './settingsSections';

export type CompanionView = `plugin:${string}:${string}` | `browser:${string}` | `sidechat:${string}` | 'collaboration' | 'assets' | 'changes' | 'structure' | 'evidence' | `page:${StudioPage}` | `preview:${string}` | `paper:${string}`;
export type CompanionState = { tabs: CompanionView[]; active: CompanionView; closed: CompanionView[]; settingsSection?: SettingsSection };

export function settingsOnlyCompanionState(state: CompanionState): CompanionState {
  if (!state.tabs.includes('page:settings')) {
    return { tabs: ['collaboration'], active: 'collaboration', closed: [] };
  }
  return {
    tabs: ['collaboration', 'page:settings'],
    active: state.active === 'page:settings' ? 'page:settings' : 'collaboration',
    closed: [],
    settingsSection: state.settingsSection,
  };
}

const fixed = new Set<CompanionView>(['collaboration', 'assets', 'changes', 'structure', 'evidence']);

export const isSideChatView = (view: string): view is `sidechat:${string}` => /^sidechat:[a-zA-Z0-9_-]+$/.test(view);

/** Replace session-bound tabs, preserving unrelated browser/file tabs. */
export function restoreSideChatTabs(state: CompanionState, ids: string[], preferredId = ''): CompanionState {
  const sideTabs = [...new Set(ids)].map(id => `sidechat:${id}` as const).filter(isSideChatView);
  const tabs = [...state.tabs.filter(tab => !isSideChatView(tab)), ...sideTabs];
  const preferred = `sidechat:${preferredId}` as const;
  const active = tabs.includes(state.active) ? state.active : sideTabs.includes(preferred) ? preferred : tabs[tabs.length - 1] || 'collaboration';
  return {...state, tabs, active, closed: state.closed.filter(tab => !isSideChatView(tab))};
}

export function isCompanionView(value: unknown): value is CompanionView {
  if (typeof value !== 'string') return false;
  if (/^plugin:[a-z0-9._-]+:[a-z0-9_-]+$/.test(value)) return true;
  if (fixed.has(value as CompanionView)) return true;
  if (isSideChatView(value)) return true;
  if (value.startsWith('page:')) return isStudioPage(value.slice(5));
  if (value.startsWith('paper:')) {
    try {
      const paper = JSON.parse(decodeURIComponent(value.slice(6)));
      return Boolean(paper && typeof paper.id === 'string' && paper.id.trim() && typeof paper.title === 'string');
    } catch { return false; }
  }
  if (!value.startsWith('preview:')) return false;
  try { return Boolean(decodeURIComponent(value.slice(8)).trim()); } catch { return false; }
}

export function parseCompanionState(raw: string | null, pageAllowed: (page: StudioPage) => boolean = () => true): CompanionState {
  try {
    const value = JSON.parse(raw || '{}');
    // Legacy Web layouts stored MCP as a separate settings tab.
    const normalize = (view: unknown) => view === 'page:mcp' ? 'page:settings' : view;
    const allowed = (view: unknown): view is CompanionView => isCompanionView(view) && (!view.startsWith('page:') || pageAllowed(view.slice(5) as StudioPage));
    const savedTabs: unknown[] = Array.isArray(value.tabs) ? value.tabs : [];
    const savedClosed: unknown[] = Array.isArray(value.closed) ? value.closed : [];
    const tabs = [...new Set(savedTabs.map(normalize).filter(allowed))];
    const legacyActive = normalize(value.active);
    const requestedActive: CompanionView | undefined = allowed(legacyActive) ? legacyActive : undefined;
    const active = requestedActive && tabs.includes(requestedActive) ? requestedActive : tabs[tabs.length - 1];
    const validClosed = [...new Set(savedClosed.map(normalize).filter(allowed))];
    const closed = validClosed.filter(item => !tabs.includes(item)).slice(-8);
    if (tabs.length && active) {
      const state: CompanionState = { tabs, active, closed };
      if (tabs.includes('page:settings') || closed.includes('page:settings')) {
        const legacySettings = [...savedTabs, ...savedClosed];
        if (isSettingsSection(value.settingsSection)) state.settingsSection = value.settingsSection;
        else if (value.active === 'page:mcp' || (legacySettings.includes('page:mcp') && !legacySettings.includes('page:settings'))) {
          state.settingsSection = 'mcp';
        }
      }
      return state;
    }
  } catch { /* Ignore stale local preferences. */ }
  return { tabs: ['collaboration'], active: 'collaboration', closed: [] };
}
