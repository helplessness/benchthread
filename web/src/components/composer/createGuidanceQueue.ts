import { createSignal, createMemo, createEffect, on, onMount, onCleanup } from 'solid-js';
import { getChatGuidanceQueue, deleteChatGuidanceItem, type ChatGuidanceQueueItem } from '../../api/client';
import { sessionStore } from '../../stores/session';

const DISMISSED_GUIDANCE_KEY = 'tcode-dismissed-unapplied-guidance-v1';

function loadDismissedGuidance(): string[] {
  try {
    const stored = JSON.parse(localStorage.getItem(DISMISSED_GUIDANCE_KEY) || '[]');
    return Array.isArray(stored) ? stored.filter(id => typeof id === 'string' && id) : [];
  } catch {
    return [];
  }
}

/** Own the active session's guidance queue and its polling lifetime. */
export function createGuidanceQueue() {
  const [guidanceQueue, setGuidanceQueue] = createSignal<ChatGuidanceQueueItem[]>([]);
  const [deletingGuidanceIds, setDeletingGuidanceIds] = createSignal<Set<string>>(new Set());
  const [dismissedUnappliedIds, setDismissedUnappliedIds] = createSignal(new Set(loadDismissedGuidance()));
  const latestUnappliedGuidance = createMemo(() => guidanceQueue().slice().reverse().find(item =>
    item.status === 'failed' && item.failure_code === 'expired_turn_unapplied'
    && !dismissedUnappliedIds().has(item.id)
  ));
  const activeGuidanceItems = createMemo(() =>
    guidanceQueue()
      .filter(item => (
        item.id
        && item.message
        && ['classifying', 'pending', 'claimed', 'deferred', 'applying'].includes(
          String(item.status || ''),
        )
      ))
  );
  const latestActiveGuidance = createMemo(() => {
    const items = activeGuidanceItems();
    return items.length > 0 ? items[items.length - 1] : null;
  });
  const guidanceStatusLabel = (status?: string) => {
    const labels: Record<string, string> = {
      classifying: '理解指导中',
      pending: '等待安全点',
      claimed: '正在接入',
      deferred: '等待当前交互',
      applying: '正在应用',
      applied: '已应用',
      superseded: '已被替代',
      cancelled: '已取消',
      failed: '应用失败',
    };
    return labels[String(status || '')] || (status ? String(status) : '已接收');
  };
  const guidanceCancellable = (status?: string) =>
    !status || ['classifying', 'pending', 'deferred'].includes(status);
  const dismissUnappliedGuidance = (id: string) => {
    const next = new Set([...dismissedUnappliedIds(), ...loadDismissedGuidance(), id]);
    setDismissedUnappliedIds(next);
    try {
      localStorage.setItem(DISMISSED_GUIDANCE_KEY, JSON.stringify([...next]));
    } catch {
      // Storage disabled: still dismiss for the lifetime of this composer.
    }
  };
  const refreshGuidanceQueue = async (showLoading = false) => {
    const sessionId = sessionStore.currentSessionId();
    if (!sessionId) {
      setGuidanceQueue([]);
      return null;
    }
    try {
      const queue = await getChatGuidanceQueue(sessionId);
      if (sessionStore.currentSessionId() === queue.session_id) {
        setGuidanceQueue(queue.items);
      }
      return queue;
    } catch (e) {
      if (showLoading) {
        console.error('Failed to load guidance queue:', e);
      }
      return null;
    }
  };
  const handleDeleteGuidance = async (guidanceId: string) => {
    const sessionId = sessionStore.currentSessionId();
    if (!sessionId || !guidanceId || deletingGuidanceIds().has(guidanceId)) return;

    setDeletingGuidanceIds(prev => new Set([...prev, guidanceId]));
    try {
      const queue = await deleteChatGuidanceItem(sessionId, guidanceId);
      if (sessionStore.currentSessionId() === queue.session_id) {
        setGuidanceQueue(queue.items);
        sessionStore.setIsRunning(
          Boolean(queue.running || queue.pending_guidance_count > 0 || queue.guidance_worker_active),
        );
        sessionStore.setStatus(
          queue.pending_guidance_count > 0
            ? `已删除排队问题，剩余 ${queue.pending_guidance_count} 条`
            : '已删除排队问题',
        );
      }
    } catch (e) {
      console.error('Failed to delete guidance item:', e);
      sessionStore.setStatus(e instanceof Error ? `删除排队问题失败: ${e.message}` : '删除排队问题失败');
      void refreshGuidanceQueue(false);
    } finally {
      setDeletingGuidanceIds(prev => {
        const next = new Set(prev);
        next.delete(guidanceId);
        return next;
      });
    }
  };
  createEffect(on(
    [sessionStore.currentSessionId, sessionStore.isRunning],
    () => void refreshGuidanceQueue(true),
  ));
  onMount(() => {
    const timer = window.setInterval(() => {
      if (!sessionStore.currentSessionId()) return;
      if (!sessionStore.isRunning() && guidanceQueue().length === 0) return;
      void refreshGuidanceQueue(false);
    }, 2500);
    onCleanup(() => window.clearInterval(timer));
  });

  return {
    activeGuidanceItems,
    latestActiveGuidance,
    latestUnappliedGuidance,
    dismissUnappliedGuidance,
    guidanceStatusLabel,
    guidanceCancellable,
    refreshGuidanceQueue,
    handleDeleteGuidance,
    deletingGuidanceIds,
  };
}
