import type { ChatRequest } from "./api";
import { approvePrivacyDraft, createPrivacyDraft } from "./clientPrivacy";

/** Mask all outbound fields locally, then register the immutable transport snapshot. */
export async function preparePrivateRequest(request: ChatRequest, signal?: AbortSignal): Promise<ChatRequest | null> {
  if (signal?.aborted) return null;
  const draft = createPrivacyDraft(request);
  // Let synchronous navigation, summary edits or cancellation invalidate preparation.
  await Promise.resolve();
  return signal?.aborted ? null : approvePrivacyDraft(draft);
}
