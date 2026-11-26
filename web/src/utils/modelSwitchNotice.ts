import { confirmAction } from '../components/FeedbackDialog';

const changes = new Map<string, { from: string; to: string; fromLabel: string; toLabel: string }>();
const confirming = new Set<string>();

export function recordModelSwitch(sessionId: string, from: string, to: string, labels?: { from: string; to: string }) {
  const previous = changes.get(sessionId);
  const original = previous?.from || from;
  if (!original || original === to) changes.delete(sessionId);
  else changes.set(sessionId, { from: original, to, fromLabel: previous?.fromLabel || labels?.from || from, toLabel: labels?.to || to });
}

/** Acknowledge only this exact switch; cancellation retains the reminder. */
export async function confirmModelSwitch(sessionId: string, model?: string): Promise<boolean> {
  const change = changes.get(sessionId);
  if (!change || (model && model !== change.to)) return true;
  if (confirming.has(sessionId)) return false;
  confirming.add(sessionId);
  try {
    const accepted = await confirmAction(
      `模型已从 ${change.fromLabel} 切换为 ${change.toLabel}。\n不同模型的回答质量、速度和工具调用能力可能不同，部分任务可能出现效果下降或兼容性问题。已有会话记录会保留。\n是否使用新模型发送本次请求？`,
      { title: '使用已切换的模型？', tone: 'warning', confirmLabel: '使用新模型发送' },
    );
    if (!accepted || changes.get(sessionId) !== change) return false;
    changes.delete(sessionId);
    return true;
  } finally { confirming.delete(sessionId); }
}
