import type { ChatRequest } from "./api";
import { approvePrivacyDraft, createPrivacyDraft, removePrivacyClarification, updatePrivacyField, type PrivacyDraft } from "./clientPrivacy";

export interface PrivacyReviewSession { id: number; draft: PrivacyDraft; error: string | null }
interface PendingReview { snapshot: PrivacyReviewSession; hiddenTerms: string[]; signal?: AbortSignal; onAbort: () => void; resolve: (request: ChatRequest | null) => void }
const pending: PendingReview[] = [];
const listeners = new Set<() => void>();
let sequence = 0;
const publish = () => { for (const listener of listeners) listener(); };

export const getPrivacyReview = (): PrivacyReviewSession | null => pending[0]?.snapshot ?? null;
export function subscribePrivacyReview(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function finish(entry: PendingReview, request: ChatRequest | null): void {
  const index = pending.indexOf(entry);
  if (index < 0) return;
  pending.splice(index, 1);
  entry.signal?.removeEventListener("abort", entry.onAbort);
  entry.resolve(request);
  publish();
}

/** Text masking always runs; the saved admin setting may skip manual confirmation. */
export function requestPrivacyReview(request: ChatRequest, signal?: AbortSignal, options: { enabled?: boolean } = {}): Promise<ChatRequest | null> {
  if (signal?.aborted) return Promise.resolve(null);
  return new Promise((resolve, reject) => {
    let draft: PrivacyDraft;
    try { draft = createPrivacyDraft(request); }
    catch (error) { reject(error); return; }
    if (options.enabled === false) {
      try { resolve(signal?.aborted ? null : approvePrivacyDraft(draft)); }
      catch (error) { reject(error); }
      return;
    }
    const entry: PendingReview = {
      snapshot: { id: ++sequence, draft, error: null }, hiddenTerms: [], signal, resolve,
      onAbort: () => finish(entry, null),
    };
    pending.push(entry);
    signal?.addEventListener("abort", entry.onAbort, { once: true });
    // Also cover signals aborted by synchronous draft preparation.
    if (signal?.aborted) finish(entry, null);
    else publish();
  });
}

function change(id: number, update: (draft: PrivacyDraft) => PrivacyDraft): void {
  const entry = pending[0];
  if (!entry || entry.snapshot.id !== id || entry.signal?.aborted) return;
  try { entry.snapshot = { id, draft: update(entry.snapshot.draft), error: null }; }
  catch (error) { entry.snapshot = { ...entry.snapshot, error: error instanceof Error ? error.message : "無法更新送出內容，請重新檢查。" }; }
  publish();
}

export function editPrivacyReviewField(id: number, path: string[], text: string): void {
  change(id, draft => updatePrivacyField(draft, path, text));
}
export function addPrivacyHiddenTerms(id: number, terms: string[]): void {
  const entry = pending[0];
  if (!entry || entry.snapshot.id !== id) return;
  change(id, draft => {
    const combined = [...new Set([...entry.hiddenTerms, ...terms].map(term => term.trim()).filter(Boolean))];
    const next = createPrivacyDraft(draft.request, combined);
    entry.hiddenTerms = combined;
    return next;
  });
}
export function removePrivacyReviewClarification(id: number): void {
  change(id, removePrivacyClarification);
}
export function cancelPrivacyReview(id: number): void {
  const entry = pending.find(item => item.snapshot.id === id);
  if (entry) finish(entry, null);
}
export function confirmPrivacyReview(id: number): boolean {
  const entry = pending[0];
  if (!entry || entry.snapshot.id !== id || entry.signal?.aborted) return false;
  try {
    // Keep this exact object: the transport verifies its approval registration.
    const approved = approvePrivacyDraft(entry.snapshot.draft);
    finish(entry, approved);
    return true;
  } catch (error) {
    entry.snapshot = { ...entry.snapshot, error: error instanceof Error ? error.message : "送出內容尚未通過本機檢查。" };
    publish();
    return false;
  }
}
