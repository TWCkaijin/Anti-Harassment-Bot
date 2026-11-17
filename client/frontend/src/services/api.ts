export * from "@rag/shared/api";

import { sendChat as sendSharedChat, type AnalysisEntry, type ChatGuidance, type ChatProgress, type ChatRequest, type ChatResponse, type ChatRetraction, type ReasoningEntry } from "@rag/shared/api";
import { getChatAppCheckToken } from "./appCheck";

/** Attach App Check only to the privacy-reviewed chat request. */
export async function sendChat(
  request: ChatRequest,
  signal?: AbortSignal,
  onDelta?: (text: string) => void,
  onGuidance?: (guidance: ChatGuidance) => void,
  onProgress?: (progress: ChatProgress) => void,
  onAnalysis?: (analysis: AnalysisEntry) => void,
  onReasoning?: (reasoning: ReasoningEntry) => void,
  onRetract?: (retraction: ChatRetraction) => void,
): Promise<ChatResponse> {
  const token = await getChatAppCheckToken(signal);
  return sendSharedChat(request, signal, onDelta, onGuidance, onProgress, onAnalysis, onReasoning, onRetract, token);
}
