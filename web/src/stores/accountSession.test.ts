import { afterEach, expect, it, vi } from 'vitest';
import { createAccountSession } from './accountSession';
import { sessionStore } from './session';

afterEach(() => vi.unstubAllGlobals());

it('keeps the current session on reload and clears it on explicit login even if another tab bound the same scope', () => {
  const scope = 'member:lab';
  vi.stubGlobal('localStorage', { getItem: () => scope, setItem: () => {} });
  sessionStore.setCurrentSessionId('prior-session');
  expect(sessionStore.bindIdentityScope(scope)).toBe(false);
  expect(sessionStore.currentSessionId()).toBe('prior-session');

  const account = createAccountSession();
  account.enter({user_id: 'member', tenant_id: 'lab', email: 'member@example.test', password_mode: 'setup'});
  expect(account.user()?.tenant_id).toBe('lab');
  expect(sessionStore.currentSessionId()).toBeNull();

  sessionStore.setCurrentSessionId('current-session');
  account.passwordSaved();
  expect(account.user()?.password_mode).toBe('change');
  expect(sessionStore.currentSessionId()).toBe('current-session');
  account.leave();
  expect(account.user()).toBeNull();
  expect(sessionStore.currentSessionId()).toBeNull();
});
