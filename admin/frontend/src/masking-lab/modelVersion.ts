import { MODEL_REPOSITORY } from './config';
import { MaskingError } from './extraction';

export const MODEL_VERSION_ENDPOINT = `https://huggingface.co/api/models/${MODEL_REPOSITORY}/revision/main`;
/** Resolve once, then download immutable URLs. No user text or credentials are sent. */
export async function latestModelRevision(signal: AbortSignal): Promise<string> {
  const response = await fetch(MODEL_VERSION_ENDPOINT, { signal, cache: 'no-store', credentials: 'omit', referrerPolicy: 'no-referrer' });
  if (!response.ok) throw new MaskingError('model_version_check_failed');
  const data: unknown = await response.json();
  if (!data || typeof data !== 'object' || !('sha' in data) || typeof data.sha !== 'string' || !/^[a-f0-9]{40}$/.test(data.sha)) throw new MaskingError('invalid_model_revision');
  return data.sha;
}

const KEY = 'masking-lab:model-revision';
export function savedModelRevision(fallback: string): string {
  try { const value = localStorage.getItem(KEY); return value && /^[a-f0-9]{40}$/.test(value) ? value : fallback; } catch { return fallback; }
}
export function rememberModelRevision(revision: string): void {
  try { localStorage.setItem(KEY, revision); } catch { /* Loading still works when storage is disabled. */ }
}
