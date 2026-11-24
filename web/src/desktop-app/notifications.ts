import type { ConnectNotifications } from '../clientPlatform';
import { desktop } from './bridge';

/** Keep OS banners independent of unread state; reading remains an explicit action. */
export function desktopNotificationLedger(identity: string, storage: Pick<Storage, 'getItem' | 'setItem'>) {
  const key = `tcode-desktop-notifications-v1:${identity}`;
  let seen: Set<string>;
  try {
    const saved: unknown = JSON.parse(storage.getItem(key) || '[]');
    seen = new Set(Array.isArray(saved) ? saved.filter((id): id is string => typeof id === 'string').slice(-1000) : []);
  } catch { seen = new Set(); }
  return {
    has: (id: string) => seen.has(id),
    remember(id: string) {
      seen.add(id);
      if (seen.size > 1000) seen.delete(seen.values().next().value!);
      try { storage.setItem(key, JSON.stringify([...seen])); } catch { /* In-memory dedupe still works. */ }
    },
  };
}

export const connectDesktopNotifications: ConnectNotifications = (identity, isCurrent, format, open) => {
  const ledger = desktopNotificationLedger(identity, localStorage);
  const events = new Map<string, Parameters<typeof open>[0]>();
  return {
    subscribe() {
      const unsubscribe = desktop!.onNotification(id => {
        const event = events.get(id);
        if (event && isCurrent()) open(event);
      });
      return () => {
        unsubscribe();
        events.clear();
      };
    },
    async sync(unread) {
      if (localStorage.getItem('tcode.settings.notify') === '0') return;
      for (const event of unread) events.set(JSON.stringify([identity, event.id]), event);
      // Bound banners after reopening while keeping every unread card in the app.
      const fresh = unread.filter(event => !ledger.has(event.id));
      for (const event of fresh.slice(3)) ledger.remember(event.id);
      for (const event of fresh.slice(0, 3)) {
        if (!isCurrent()) return;
        const id = JSON.stringify([identity, event.id]);
        if (events.size > 1000) events.delete(events.keys().next().value!);
        try {
          await desktop!.notify({ id, ...format(event) });
          if (isCurrent()) ledger.remember(event.id);
        } catch { /* The event remains visible in the app. */ }
      }
    },
  };
};
