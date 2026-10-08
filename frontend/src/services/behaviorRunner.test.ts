import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, runAdminChatTest } from "./api";
import { runBehaviorTests } from "./behaviorRunner";
import { behaviorResult, behaviorRun } from "../test/behaviorFixture";
import { reviewedRequestBody } from "./clientPrivacy";
import * as privacy from "./privacyReview";
import type { BehaviorRun } from "./behaviorTests";

vi.mock("./api", async () => ({ ...await vi.importActual("./api"), runAdminChatTest: vi.fn() }));
beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto);
  vi.mocked(runAdminChatTest).mockReset().mockImplementation(async (_token, request) => behaviorResult(request));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const options = (run = behaviorRun()) => ({ run, token: "never-store-admin-token", signal: new AbortController().signal, shouldPause: () => false, onUpdate: vi.fn(), delayMs: 0 });

describe("v4 behavior runner", () => {
  it("carries real replies and accepted summaries, masks each complete outbound request, and never sends the rubric", async () => {
    const run = behaviorRun(["C01"]);
    run.cases[0].expected = "gold-never-send";
    run.cases[0].criticalFailure = "rubric-never-send";
    run.cases[0].turns[0] = "請聯絡 test@example.com，對方是同事。";
    vi.mocked(runAdminChatTest).mockImplementation(async (_token, request) => behaviorResult(request, "上一輪真實回覆 contact@example.com", { summary_update: { base_revision: request.case_context!.revision, summary: request.case_context!.revision === 0 ? "對方是同事。contact@example.com" : "更正後，對方是直屬主管。", evidence: [request.message] } }));
    const result = await runBehaviorTests(options(run));
    const calls = vi.mocked(runAdminChatTest).mock.calls;
    expect(calls).toHaveLength(3);
    expect(calls[0][1].message).not.toContain("test@example.com");
    expect(calls[1][1].history[1].content).toBe("上一輪真實回覆 [電子郵件]");
    expect(calls[1][1].case_context).toMatchObject({ revision: 1, summary: "對方是同事。[電子郵件]" });
    expect(calls[2][1].case_context).toMatchObject({ revision: 2, summary: "更正後，對方是直屬主管。" });
    for (const call of calls) {
      expect(reviewedRequestBody(call[1])).not.toMatch(/example\.com|gold-never-send|rubric-never-send/);
      expect(call[4]).toBe(run.contractSha256);
    }
    expect(result.run.records[0].status).toBe("completed");
    expect(JSON.stringify(result.run)).not.toContain("never-store-admin-token");
  });
  it("does not accept stale response or summary revisions", async () => {
    vi.mocked(runAdminChatTest).mockImplementation(async (_token, request) => behaviorResult(request, "合成回覆", { context_revision: 9, summary_update: { base_revision: 9, summary: "過期摘要", evidence: ["合成"] } }));
    const result = await runBehaviorTests(options(behaviorRun(["C01"])));
    expect(vi.mocked(runAdminChatTest).mock.calls[1][1].case_context).toMatchObject({ revision: 0, summary: "" });
    expect(result.run.records[0].turns[0].issues.map(issue => issue.code)).toContain("context_revision");
  });
  it("pauses between turns and resumes without rerunning completed requests", async () => {
    const config = options(behaviorRun(["C01"])); let paused = false;
    config.shouldPause = () => paused;
    vi.mocked(runAdminChatTest).mockImplementationOnce(async (_token, request) => { paused = true; return behaviorResult(request); });
    const first = await runBehaviorTests(config);
    expect(first.run.records[0].status).toBe("paused");
    expect(first.run.records[0].turns).toHaveLength(1);
    paused = false;
    const done = await runBehaviorTests({ ...config, run: first.run });
    expect(done.run.records[0].status).toBe("completed"); expect(runAdminChatTest).toHaveBeenCalledTimes(3);
  });
  it("starts each case and repetition with empty history and context", async () => {
    await runBehaviorTests(options(behaviorRun(["B01", "S01"], 3)));
    expect(runAdminChatTest).toHaveBeenCalledTimes(6);
    for (const call of vi.mocked(runAdminChatTest).mock.calls) expect(call[1]).toMatchObject({ history: [], case_context: { revision: 0, summary: "" } });
  });
  it("stops later turns after an error and retries only that failed turn on explicit request", async () => {
    vi.mocked(runAdminChatTest).mockImplementationOnce(async (_token, request) => behaviorResult(request))
      .mockRejectedValueOnce(new ApiError(500, "private provider body"));
    const initial = behaviorRun(["B01", "C01"]); initial.records.reverse();
    const failed = await runBehaviorTests(options(initial));
    expect(failed.run.records.map(record => record.status)).toEqual(["error", "completed"]);
    expect(runAdminChatTest).toHaveBeenCalledTimes(3);
    expect(failed.run.records[0].turns[1].error).not.toContain("private");
    await runBehaviorTests(options(failed.run)); expect(runAdminChatTest).toHaveBeenCalledTimes(3);
    const retried = await runBehaviorTests({ ...options(failed.run), retryFailed: true });
    expect(runAdminChatTest).toHaveBeenCalledTimes(5);
    expect(retried.run.records[0].turns.map(turn => [turn.number, turn.attempt, turn.status])).toEqual([[1, 1, "completed"], [2, 1, "error"], [2, 2, "completed"], [3, 1, "completed"]]);
  });
  it.each([401, 403, 409, 429])("pauses the whole suite on HTTP %s", async status => {
    vi.mocked(runAdminChatTest).mockRejectedValueOnce(new ApiError(status, "private"));
    const result = await runBehaviorTests(options(behaviorRun(["B01", "S01"])));
    expect(result.stoppedReason).toBeTruthy(); expect(result.run.records[1].status).toBe("pending");
    expect(runAdminChatTest).toHaveBeenCalledTimes(1);
  });
  it("marks an interrupted request explicitly and never treats resume as retry authorization", async () => {
    const controller = new AbortController();
    vi.mocked(runAdminChatTest).mockImplementationOnce(async (_token, request) => { controller.abort(); return behaviorResult(request); });
    const result = await runBehaviorTests({ ...options(), signal: controller.signal });
    expect(result.run.records[0].status).toBe("interrupted");
    await runBehaviorTests(options(result.run)); expect(runAdminChatTest).toHaveBeenCalledTimes(1);
    await runBehaviorTests({ ...options(result.run), retryFailed: true }); expect(runAdminChatTest).toHaveBeenCalledTimes(2);
  });
  it("cancelling the privacy review pauses without sending a request", async () => {
    vi.spyOn(privacy, "requestPrivacyReview").mockResolvedValueOnce(null);
    const result = await runBehaviorTests(options());
    expect(result.run.records[0]).toMatchObject({ status: "paused", turns: [] });
    expect(runAdminChatTest).not.toHaveBeenCalled();
  });
  it("publishes immutable progress and marks timeout independently from a model judgment", async () => {
    vi.mocked(runAdminChatTest).mockRejectedValueOnce(new ApiError(504, "timeout"));
    const snapshots: BehaviorRun[] = [];
    const result = await runBehaviorTests({ ...options(), onUpdate: run => snapshots.push(run) });
    expect(snapshots[0].records[0]).toMatchObject({ status: "running", turns: [] });
    expect(snapshots[1].records[0].turns[0].status).toBe("running");
    expect(result.run.records[0].turns[0].error).toContain("逾時");
    expect(result.run.records[0].review).toBeUndefined();
  });
});
