export function resolveActiveModel(
  confirmedSelection: string,
  harnessProjection: string,
  sessionProjection: string,
): string {
  return [confirmedSelection, harnessProjection, sessionProjection]
    .map(value => String(value || '').trim())
    .find(Boolean) || '';
}

export function shouldAcceptResponseModel(
  currentModel: string,
  requestModel: string,
): boolean {
  const current = String(currentModel || '').trim();
  const requested = String(requestModel || '').trim();
  return !current || current === requested;
}

export function nativeModelTurnBlock(
  guidingExistingGoal: boolean,
  catalog: readonly { id: string }[] | undefined,
  model: string,
): '' | 'empty' | 'unverified' {
  if (guidingExistingGoal || catalog?.some(item => item.id === model)) return '';
  return catalog?.length === 0 ? 'empty' : 'unverified';
}
