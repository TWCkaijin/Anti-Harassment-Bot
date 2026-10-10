import { afterEach, describe, expect, it, vi } from "vitest";
import { runAdminChatTest, runAdminApplicableLawTest } from "./api";
import { approvePrivacyDraft, createPrivacyDraft } from "./clientPrivacy";
import { behaviorResult } from "../test/behaviorFixture";

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const prepared = () => approvePrivacyDraft(createPrivacyDraft({ message: "合成案例 synthetic@example.test", history: [], use_rag: true, contract_version: 4, case_context: { schema_version: 3, revision: 0, facts: {}, summary: "", summary_origin: "user" } }));
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("asynchronous one-turn diagnostics", () => {
  it("creates exactly one idempotent job, polls and returns the original validated response", async () => {
    vi.useFakeTimers();
    const request = prepared(); const result = behaviorResult(request);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json({ job_id: "job-1", status: "queued" }, 202))
      .mockResolvedValueOnce(json({ job_id: "job-1", status: "running" }))
      .mockResolvedValueOnce(json({ job_id: "job-1", status: "succeeded", result }));
    vi.stubGlobal("fetch", fetchMock);
    const pending = runAdminChatTest("csrf", request, { enable_anonymization: false }, undefined, "a".repeat(64));
    await vi.advanceTimersByTimeAsync(1100);
    await expect(pending).resolves.toEqual(result);
    expect(fetchMock.mock.calls.filter(call => call[1].method === "POST")).toHaveLength(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/admin/test-jobs");
    const sent = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(sent.request.message).not.toContain("example.test");
    expect(sent.overrides).toEqual({ enable_anonymization: false });
    expect(sent.expected_chat_test_contract_sha256).toBe("a".repeat(64));
    const headers = new Headers(fetchMock.mock.calls[0][1].headers);
    expect(headers.get("Idempotency-Key")).toBeTruthy(); expect(headers.get("X-CSRF-Token")).toBe("csrf");
    expect(headers.has("Authorization")).toBe(false);
    expect(fetchMock.mock.calls.slice(1).every(call => call[1].method === "GET")).toBe(true);
  });
  it.each(["failed", "needs_review"])("never automatically recreates a %s job", async status => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ job_id: "j", status: "queued" }, 202)).mockResolvedValueOnce(json({ job_id: "j", status, error: { code: "uncertain_provider_result", detail: "請手動檢查" } }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(runAdminChatTest("csrf", prepared(), {})).rejects.toMatchObject({ status: 502 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it("preserves contract mismatch errors so a batch stops instead of mixing settings", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({ job_id: "j", status: "queued" }, 202)).mockResolvedValueOnce(json({ job_id: "j", status: "failed", error: { code: "applicable_laws_contract_mismatch", http_status: 409 } })));
    await expect(runAdminApplicableLawTest("csrf", prepared(), {})).rejects.toMatchObject({ status: 409 });
  });
  it("backs off a rate-limited poll without enqueueing the turn again", async () => {
    vi.useFakeTimers();
    const request = prepared();
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ job_id: "j", status: "queued" }, 202))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Slow down" }), { status: 429, headers: { "Content-Type": "application/json", "Retry-After": "2" } }))
      .mockResolvedValueOnce(json({ job_id: "j", status: "succeeded", result: behaviorResult(request) }));
    vi.stubGlobal("fetch", fetchMock);
    const pending = runAdminChatTest("csrf", request, {});
    await vi.advanceTimersByTimeAsync(1000); expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1100); await pending;
    expect(fetchMock.mock.calls.filter(call => call[1].method === "POST")).toHaveLength(1);
  });
  it("rejects every unprepared request before enqueueing even when backend masking is disabled", async () => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    const request = { message: "raw@example.test", history: [], use_rag: true };
    await expect(runAdminChatTest("csrf", request, { enable_anonymization: false })).rejects.toMatchObject({ code: "review_required" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("sends a cancellation request for an already queued turn when the caller stops", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ job_id: "j", status: "queued" }, 202)).mockResolvedValueOnce(json({ job_id: "j", status: "running" })).mockResolvedValueOnce(json({ job_id: "j", status: "cancelled" }));
    vi.stubGlobal("fetch", fetchMock);
    const abort = new AbortController();
    const pending = runAdminChatTest("csrf", prepared(), {}, abort.signal);
    const rejected = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(0); abort.abort();
    await rejected;
    expect(fetchMock.mock.calls[2][0]).toBe("/api/v1/admin/test-jobs/j/cancel");
    expect(fetchMock.mock.calls.filter(call => call[0] === "/api/v1/admin/test-jobs")).toHaveLength(1);
  });
});
