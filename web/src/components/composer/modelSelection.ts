import type { ModelOption } from '../../api/catalog';

export function modelDisplayLabel(model: Pick<ModelOption, 'id' | 'name'> & Partial<Pick<ModelOption, 'provider_id' | 'provider_name'>>): string {
  const separator = model.id.indexOf('/');
  const providerId = model.provider_id || (separator >= 0 ? model.id.slice(0, separator) : '');
  const managed = /^(desktop-|web-model-)/.test(providerId || model.id);
  const fallback = managed ? (separator >= 0 ? model.id.slice(separator + 1) : '') : model.id;
  const name = (model.name !== model.id && model.name) || fallback || '模型不可用';
  const serviceName = model.provider_name || (!managed && model.provider_id ? model.provider_id : '');
  return serviceName && !(managed && serviceName === providerId) && serviceName !== name
    ? `${name} · ${serviceName}`
    : name;
}

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
