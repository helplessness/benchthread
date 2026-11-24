import { batch, createMemo, createSignal, onMount } from 'solid-js';
import { getCurrentUser, type AuthUser } from '../api/auth';
import { resetSessionRequestCache } from '../api/sessions';
import { sessionStore } from './session';

export function createAccountSession() {
  const [user, setUser] = createSignal<AuthUser | null>(null);
  const [checking, setChecking] = createSignal(true);
  const [failed, setFailed] = createSignal(false);
  const scope = createMemo(() => {
    const current = user();
    return current ? `${current.user_id}:${current.tenant_id}` : '';
  });

  function bindUser(next: AuthUser, freshLogin: boolean) {
    batch(() => {
      if (sessionStore.bindIdentityScope(`${next.user_id}:${next.tenant_id}`, freshLogin)) {
        resetSessionRequestCache();
      }
      setUser(next);
    });
  }

  function leave() {
    batch(() => {
      setUser(null);
      resetSessionRequestCache();
      sessionStore.setSessions([]);
      sessionStore.clearCurrentSession();
    });
  }

  onMount(async () => {
    try {
      const current = await getCurrentUser();
      if (current) bindUser(current, false);
    } catch (error) {
      setFailed(true);
      console.error('Failed to initialize auth state:', error);
    } finally {
      setChecking(false);
    }
  });

  return {
    user, scope, checking, failed, leave,
    enter: (next: AuthUser) => bindUser(next, true),
    passwordSaved: () => setUser(current => current && { ...current, password_mode: 'change' }),
  };
}
