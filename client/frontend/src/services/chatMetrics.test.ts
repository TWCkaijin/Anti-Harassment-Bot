import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createChatMetrics } from "./chatMetrics";
import { hasAnalyticsConsent, trackAnalytics } from "./analytics";
import type { ChatResponse } from "./api";
vi.mock("./analytics", () => ({ hasAnalyticsConsent: vi.fn(() => true), trackAnalytics: vi.fn() }));
let now = 0;
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(hasAnalyticsConsent).mockReturnValue(true);
  now = 100;
  vi.spyOn(performance, "now").mockImplementation(() => now);
});
afterEach(() => vi.restoreAllMocks());

describe("chat latency measurements", () => {
  it("counts first text once and total duration across retries without mixing server clocks", () => {
    const metrics = createChatMetrics(false, true);
    metrics.beginAttempt(0);
    metrics.progress({ phase: "waiting_model", elapsed_ms: 100 });
    metrics.beginAttempt(1);
    metrics.progress({ phase: "preparing", elapsed_ms: 20 });
    metrics.progress({ phase: "waiting_model", elapsed_ms: 120 });
    now = 3100;
    metrics.firstToken();
    now = 3300;
    metrics.firstToken();
    metrics.firstGuidance();
    now = 4100;
    metrics.finish("success", { rag_used: { status: true, sources: ["private source"] } } as ChatResponse);
    metrics.finish("error");
    expect(trackAnalytics).toHaveBeenCalledWith("chat_first_token", { duration_ms: 3000, retry_count: 1 });
    expect(trackAnalytics).toHaveBeenCalledWith("chat_stage_completed", { phase: "preparing", duration_ms: 100, attempt: 1 });
    expect(trackAnalytics).toHaveBeenCalledWith("chat_request_finished", {
      outcome: "success", duration_ms: 4000, first_token_ms: 3000, first_guidance_ms: 3200,
      retry_count: 1, rag_used: true, source_count: 1, streamed: true,
    });
    expect(vi.mocked(trackAnalytics).mock.calls.filter(([event]) => event === "chat_first_token")).toHaveLength(1);
    expect(vi.mocked(trackAnalytics).mock.calls.filter(([event]) => event === "chat_stage_completed")).toHaveLength(1);
    expect(vi.mocked(trackAnalytics).mock.calls.filter(([event]) => event === "chat_request_finished")).toHaveLength(1);
    expect(JSON.stringify(vi.mocked(trackAnalytics).mock.calls)).not.toContain("private source");
  });
  it.each(["error", "cancelled"] as const)("records %s without fabricating a first-token measurement", outcome => {
    const metrics = createChatMetrics(false, false);
    now = 800;
    metrics.finish(outcome);
    expect(trackAnalytics).toHaveBeenLastCalledWith("chat_request_finished", {
      outcome, duration_ms: 700, retry_count: 0,
    });
  });
  it("does not backfill a request started before consent", () => {
    vi.mocked(hasAnalyticsConsent).mockReturnValue(false);
    const metrics = createChatMetrics(false, false);
    vi.mocked(hasAnalyticsConsent).mockReturnValue(true);
    metrics.firstToken();
    metrics.finish("success");
    expect(trackAnalytics).not.toHaveBeenCalled();
  });
});
