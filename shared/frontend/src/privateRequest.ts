import type { ChatRequest } from "./api";
import { approvePrivacyDraft, createPrivacyDraft, type PrivacyDraft } from "./clientPrivacy";

export type PrivacyReviewer = (draft: PrivacyDraft, signal?: AbortSignal) => Promise<PrivacyDraft | null>;

/** Rule-mask locally, require a person's review, then register the exact approved snapshot. */
export async function preparePrivateRequest(request: ChatRequest, review: PrivacyReviewer, signal?: AbortSignal): Promise<ChatRequest | null> {
  if (signal?.aborted) return null;
  const draft = createPrivacyDraft(request);
  const reviewed = await review(draft, signal);
  return !reviewed || signal?.aborted ? null : approvePrivacyDraft(reviewed);
}
