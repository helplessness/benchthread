import { describe, expect, it } from 'vitest';
import { isCompanionView, parseCompanionState, restoreSideChatTabs, settingsOnlyCompanionState } from './companionState';

describe('companion tab state', () => {
  it('does not restore a browser tab without its live desktop session', () => {
    expect(parseCompanionState(JSON.stringify({tabs:['collaboration','browser:manual-browser-test'],active:'browser:manual-browser-test',closed:[]})))
      .toEqual({tabs:['collaboration'],active:'collaboration',closed:[]});
  });
  it('restores valid tabs and discards stale values', () => {
    expect(parseCompanionState(JSON.stringify({
      tabs: ['collaboration', 'preview:outputs%2Fone.pdb', 'page:review', 'page:missing'],
      active: 'preview:outputs%2Fone.pdb', closed: ['assets', 'page:missing'],
    }))).toEqual({
      tabs: ['collaboration', 'preview:outputs%2Fone.pdb', 'page:review'],
      active: 'preview:outputs%2Fone.pdb', closed: ['assets'],
    });
  });

  it('falls back safely for corrupt state', () => {
    expect(parseCompanionState('{')).toEqual({ tabs: ['collaboration'], active: 'collaboration', closed: [] });
    expect(isCompanionView('changes')).toBe(true);
    expect(isCompanionView('preview:%E0%A4%A')).toBe(false);
  });

  it('drops tabs that are unavailable on the current surface', () => {
    const allowed = (page: string) => !['mcp', 'settings'].includes(page);
    expect(parseCompanionState(JSON.stringify({
      tabs: ['collaboration', 'page:mcp', 'page:settings', 'page:plugins'],
      active: 'page:settings', closed: ['page:mcp', 'assets'],
    }), allowed)).toEqual({
      tabs: ['collaboration', 'page:plugins'], active: 'page:plugins', closed: ['assets'],
    });
  });

  it('merges legacy Web settings tabs and preserves the active tab', () => {
    const migrated = parseCompanionState(JSON.stringify({
      tabs: ['assets', 'page:mcp', 'page:settings', 'page:plugins'],
      active: 'page:mcp', closed: ['page:mcp', 'changes'],
    }));
    expect(migrated).toEqual({
      tabs: ['assets', 'page:settings', 'page:plugins'], active: 'page:settings', closed: ['changes'],
      settingsSection: 'mcp',
    });
    expect(parseCompanionState(JSON.stringify(restoreSideChatTabs(migrated, [])))).toEqual(migrated);
    expect(parseCompanionState(JSON.stringify({
      tabs: ['assets'], active: 'assets', closed: ['page:mcp'],
    }))).toEqual({tabs: ['assets'], active: 'assets', closed: ['page:settings'], settingsSection: 'mcp'});
  });

  it('keeps ordinary settings active when a separate legacy MCP tab was also present', () => {
    const saved = {tabs: ['page:settings', 'page:mcp'], active: 'page:settings', closed: []};
    expect(parseCompanionState(JSON.stringify(saved)).settingsSection).toBeUndefined();
    expect(parseCompanionState(JSON.stringify({...saved, settingsSection: 'models'})).settingsSection).toBe('models');
    expect(parseCompanionState(JSON.stringify({...saved, settingsSection: 'invalid'})).settingsSection).toBeUndefined();
    expect(parseCompanionState(JSON.stringify({...saved, settingsSection: 'mcp'}), page => page !== 'settings').settingsSection).toBeUndefined();
  });

  it('restores independent paper preview tabs', () => {
    const paper = `paper:${encodeURIComponent(JSON.stringify({id:'paper-123',title:'Example paper'}))}`;
    expect(isCompanionView(paper)).toBe(true);
    expect(parseCompanionState(JSON.stringify({tabs:['page:library',paper],active:paper,closed:[]}))).toEqual({
      tabs:['page:library',paper],active:paper,closed:[],
    });
  });

  it('persists only open Web settings when there is no workspace', () => {
    const saved = settingsOnlyCompanionState({
      tabs: ['preview:private.txt', 'sidechat:old', 'page:settings'],
      active: 'page:settings', closed: ['page:jobs'], settingsSection: 'models',
    });
    expect(parseCompanionState(JSON.stringify(saved))).toEqual({
      tabs: ['collaboration', 'page:settings'], active: 'page:settings', closed: [], settingsSection: 'models',
    });
    expect(settingsOnlyCompanionState({...saved, active: 'preview:private.txt'}).active).toBe('collaboration');
    expect(settingsOnlyCompanionState({...saved, tabs: ['preview:private.txt'], closed: ['page:settings']})).toEqual({
      tabs: ['collaboration'], active: 'collaboration', closed: [],
    });
  });
});
