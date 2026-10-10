import type { ChatProgress, ChatResponse } from "./api";
import { hasAnalyticsConsent, trackAnalytics } from "./analytics";

/** One logical user request, including retries; no message content enters analytics. */
export function createChatMetrics(hasImage: boolean, usesRag: boolean) {
  const started = performance.now();
  const consentAtStart = hasAnalyticsConsent();
  const track: typeof trackAnalytics = (event, params) => {
    if (consentAtStart) trackAnalytics(event, params);
  };
  let firstToken: number | undefined;
  let firstGuidance: number | undefined;
  let attempt = 0;
  let finished = false;
  let previousProgress: ChatProgress | undefined;
  const elapsed = () => Math.max(0, Math.round(performance.now() - started));
  track("chat_request_started", { has_image: hasImage, uses_rag: usesRag });
  return {
    beginAttempt(index: number) { attempt = index; previousProgress = undefined; },
    firstToken() {
      if (finished || firstToken !== undefined) return;
      firstToken = elapsed();
      track("chat_first_token", { duration_ms: firstToken, retry_count: attempt });
    },
    firstGuidance() {
      if (finished || firstGuidance !== undefined) return;
      firstGuidance = elapsed();
      track("chat_first_guidance", { duration_ms: firstGuidance });
    },
    progress(progress: ChatProgress) {
      if (finished) return;
      if (previousProgress && progress.elapsed_ms >= previousProgress.elapsed_ms) {
        track("chat_stage_completed", {
          phase: previousProgress.phase,
          duration_ms: Math.round(progress.elapsed_ms - previousProgress.elapsed_ms),
          attempt,
        });
      }
      previousProgress = progress;
    },
    finish(outcome: "success" | "error" | "cancelled", response?: ChatResponse, httpStatus?: number) {
      if (finished) return;
      finished = true;
      track("chat_request_finished", {
        outcome, duration_ms: elapsed(), retry_count: attempt,
        ...(firstToken !== undefined ? { first_token_ms: firstToken } : {}),
        ...(firstGuidance !== undefined ? { first_guidance_ms: firstGuidance } : {}),
        ...(httpStatus !== undefined ? { http_status: httpStatus } : {}),
        ...(response ? { rag_used: response.rag_used.status, source_count: response.rag_used.sources.length, streamed: firstToken !== undefined } : {}),
      });
    },
  };
}
