import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, checkHealth, sendChat, type ChatResponse } from "../services/api";
import { useConversation } from "./useConversation";
import { STORAGE_KEY } from "./conversationStorage";
import { emptySummaryContext, type LegacyCaseContext } from "../services/caseFacts";

vi.mock("../services/api", async () => ({ ...await vi.importActual<typeof import("../services/api")>("../services/api"), sendChat: vi.fn(), checkHealth: vi.fn() }));
vi.mock("../services/privacyReview", () => ({ requestPrivacyReview: vi.fn(async request => request) }));
vi.mock("../services/analytics", () => ({ hasAnalyticsConsent: () => true, trackAnalytics: vi.fn() }));
const health = { status: "ok", timestamp: "test", version: "test", environment: "test", capabilities: { chat_contract_versions: [1, 2, 3, 4] } };
const response = (revision: number, extra: Partial<ChatResponse> = {}): ChatResponse => ({ reply: "合成回覆", session_id: "test", anonymized: false, rag_used: { status: false, sources: [] }, suggested_replies: [], action_buttons: [], interaction_mode: "answer", clarifying_questions: [], contract_version: 4, context_revision: revision, summary_update: null, clarification: null, analysis: [], execution: { route: "direct_retrieval", model_calls: 1 }, ...extra });
beforeEach(() => { localStorage.clear(); vi.mocked(checkHealth).mockReset().mockResolvedValue(health); vi.mocked(sendChat).mockReset(); });
afterEach(() => vi.restoreAllMocks());

describe("v4 summary ownership and ephemeral reasoning", () => {
  it("prefers v4, streams reasoning additively, and applies summary only on done", async () => {
    let finish!: (value: ChatResponse) => void;
    let reasoning!: NonNullable<Parameters<typeof sendChat>[6]>;
    vi.mocked(sendChat).mockImplementation((_request, _signal, _delta, _guidance, _progress, _analysis, onReasoning) => { reasoning = onReasoning!; return new Promise(resolve => { finish = resolve; }); });
    const { result, unmount } = renderHook(() => useConversation("v4-done"));
    await act(async () => { await Promise.resolve(); });
    let pending!: Promise<void | boolean>;
    await act(async () => { pending = result.current.sendMessage("我是學生"); await Promise.resolve(); });
    expect(vi.mocked(sendChat).mock.calls[0][0]).toMatchObject({ contract_version: 4, case_context: emptySummaryContext() });
    act(() => { reasoning({ text: "第一段", kind: "summary", stage: "understanding" }); reasoning({ text: "第二段", kind: "summary", stage: "understanding" }); });
    expect(result.current.caseFacts).toEqual(emptySummaryContext());
    expect(result.current.messages.at(-1)?.reasoning).toEqual([{ text: "第一段第二段", kind: "summary", stage: "understanding" }]);
    await act(async () => { finish(response(0, { summary_update: { base_revision: 0, summary: "使用者是學生。", evidence: ["我是學生"] } })); await pending; });
    expect(result.current.caseFacts).toEqual({ schema_version: 3, revision: 1, facts: {}, summary: "使用者是學生。", summary_origin: "model" });
    expect(result.current.messages.at(-1)?.reasoning?.[0].text).toBe("第一段第二段");
    expect(localStorage.getItem(STORAGE_KEY)).not.toContain("第一段");
    unmount();
    const reloaded = renderHook(() => useConversation("v4-done"));
    await act(async () => { await Promise.resolve(); });
    expect(reloaded.result.current.messages.at(-1)?.reasoning).toBeUndefined();
    expect(reloaded.result.current.caseFacts).toMatchObject({ summary: "使用者是學生。", revision: 1 });
  });

  it("ignores stale summary updates and rejects stale typed answer revision", async () => {
    vi.mocked(sendChat).mockResolvedValue(response(0, { summary_update: { base_revision: 9, summary: "過期摘要", evidence: [] } }));
    const { result } = renderHook(() => useConversation("v4-stale"));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await result.current.sendMessage("合成情境"); });
    expect(result.current.caseFacts).toEqual(emptySummaryContext());
    await act(async () => { await result.current.sendMessage("選項", undefined, undefined, { answers: [], factsRevision: 0, clarificationAnswer: { question_id: "question.0", context_revision: 1, status: "provided", value: "選項" } }); });
    expect(sendChat).toHaveBeenCalledOnce();
    expect(result.current.error).toContain("情境摘要已更新");
  });

  it("keeps user edits authoritative during in-flight work and regenerates original request without old history", async () => {
    vi.mocked(sendChat).mockResolvedValue(response(0));
    const { result } = renderHook(() => useConversation("v4-edit"));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await result.current.sendMessage("原始申訴問題"); });
    const originId = result.current.messages[0].id;
    let resolveOld!: (value: ChatResponse) => void;
    vi.mocked(sendChat).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    let oldRequest!: Promise<void | boolean>;
    await act(async () => { oldRequest = result.current.sendMessage("後續追問"); await Promise.resolve(); });
    const oldSignal = vi.mocked(sendChat).mock.calls[1][1];
    await act(async () => { await result.current.saveCaseFacts({ ...emptySummaryContext(), summary: "使用者更正：事件與工作無關。" }); });
    expect(oldSignal?.aborted).toBe(true);
    await act(async () => { resolveOld(response(0, { reply: "過期回答", summary_update: { base_revision: 0, summary: "過期模型摘要", evidence: [] } })); await oldRequest; });
    expect(result.current.caseFacts).toMatchObject({ revision: 1, summary_origin: "user", summary: "使用者更正：事件與工作無關。" });
    expect(result.current.messages.some(message => message.content === "過期回答")).toBe(false);
    // A typed clarification must keep its origin request when the summary is edited again.
    vi.mocked(sendChat).mockImplementation(async request => response(request.case_context!.revision, request.regenerate_from_summary ? { summary_update: { base_revision: request.case_context!.revision, summary: "意外回傳的舊事實", evidence: ["原始問題"] } } : {}));
    await act(async () => { await result.current.sendMessage("哪種場合？\n校外", undefined, undefined, { answers: [], factsRevision: 1, clarificationAnswer: { question_id: "question.1", context_revision: 1, status: "provided", value: "校外" } }); });
    await act(async () => { await result.current.saveCaseFacts({ ...emptySummaryContext(), summary: "使用者更正：校外聚會。" }, true); });
    const regeneration = vi.mocked(sendChat).mock.calls.at(-1)![0];
    expect(regeneration.message).toBe("後續追問");
    expect(regeneration.history).toEqual([]);
    expect(regeneration.regenerate_from_summary).toBe(true);
    expect(regeneration.case_context).toMatchObject({ revision: 2, summary: "使用者更正：校外聚會。", summary_origin: "user" });
    expect(result.current.caseFacts).toMatchObject({ revision: 2, summary: "使用者更正：校外聚會。", summary_origin: "user" });
    expect(result.current.messages[0].id).toBe(originId);
    expect(result.current.messages.filter(message => message.role === "assistant" && message.superseded).length).toBeGreaterThan(0);
    await act(async () => { await result.current.sendMessage("現在該如何處理？"); });
    const nextRequest = vi.mocked(sendChat).mock.calls.at(-1)![0];
    expect(nextRequest.history.some(message => message.content === "後續追問" || message.content === "原始申訴問題")).toBe(false);
    expect(nextRequest).not.toHaveProperty("regenerate_from_summary");
  });

  it("preserves the manual edit boundary across reload and sends signed freeform clarification without fact_key", async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([{ id: "boundary", createdAt: 1, schemaVersion: 4, summaryEditedAfterMessageId: "old-a", summaryEditedAt: 3, caseFacts: { ...emptySummaryContext(), summary: "目前以這份文字為準。", revision: 2, summary_origin: "user" }, messages: [{ id: "old-u", role: "user", content: "過時陳述", timestamp: 1 }, { id: "old-a", role: "assistant", content: "過時回答", timestamp: 2, superseded: true }] }]));
    vi.mocked(sendChat).mockResolvedValue(response(2));
    const { result } = renderHook(() => useConversation("boundary"));
    await act(async () => { await Promise.resolve(); });
    const answer = { question_id: "open.2", context_revision: 2, status: "provided" as const, value: ["選項甲", "補充乙"], validation_token: "signed-v4", selection_mode: "multiple" as const, max_selections: 2 };
    await act(async () => { await result.current.sendMessage("選項甲、補充乙", undefined, undefined, { answers: [], factsRevision: 2, clarificationAnswer: answer }); });
    expect(vi.mocked(sendChat).mock.calls[0][0]).toMatchObject({ contract_version: 4, history: [], clarification_answer: answer, case_context: { revision: 2, summary: "目前以這份文字為準。" } });
    expect(vi.mocked(sendChat).mock.calls[0][0].clarification_answer).not.toHaveProperty("fact_key");
  });

  it("does not retry a failed request once provider reasoning is visible", async () => {
    vi.mocked(sendChat).mockImplementation(async (_r, _s, _d, _g, _p, _a, reasoning) => { reasoning?.({ text: "公開摘要片段", kind: "summary", stage: "answer" }); throw new ApiError(502, "中斷", undefined, true); });
    const { result } = renderHook(() => useConversation("v4-no-replay"));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await result.current.sendMessage("合成情境"); });
    expect(sendChat).toHaveBeenCalledOnce();
    expect(result.current.messages.at(-1)).toMatchObject({ isError: true, reasoning: [{ text: "公開摘要片段" }] });
    expect(localStorage.getItem(STORAGE_KEY)).not.toContain("公開摘要片段");
  });
});

describe("explicit recovery of oversized legacy summaries", () => {
  const fact = { status: "provided" as const, value: ["a".repeat(300), "b".repeat(300), "c".repeat(300), "d".repeat(300)] };
  const legacy: LegacyCaseContext = { schema_version: 2, revision: 1, facts: { subject_role: fact, other_role: fact, relationship: fact, behavior: fact } };
  const record = (id: string) => ({ id, createdAt: 1, schemaVersion: 3, caseFacts: legacy, messages: [{ id: `${id}-user`, role: "user", content: "合成舊問題", timestamp: 1 }] });

  it("waits for every oversized summary, then writes all corrections and backups exactly once", async () => {
    const raw = JSON.stringify([record("first"), record("second")]);
    localStorage.setItem(STORAGE_KEY, raw);
    const write = vi.spyOn(localStorage, "setItem");
    const { result } = renderHook(() => useConversation("first"));
    await act(async () => { await Promise.resolve(); });
    expect(result.current.storageIssue).toBe("migration");
    expect(write).not.toHaveBeenCalled();
    await act(async () => { await result.current.saveCaseFacts({ ...emptySummaryContext(), summary: "第一份經使用者縮短的摘要。" }); });
    expect(result.current.caseFacts).toMatchObject({ schema_version: 3, revision: 2, summary: "第一份經使用者縮短的摘要。" });
    expect(result.current.storageIssue).toBe("migration");
    expect(localStorage.getItem(STORAGE_KEY)).toBe(raw);
    expect(write).not.toHaveBeenCalled();
    act(() => result.current.setCurrentSessionId("second"));
    await act(async () => { await result.current.saveCaseFacts({ ...emptySummaryContext(), summary: "第二份經使用者縮短的摘要。" }); });
    expect(result.current.storageIssue).toBeNull();
    expect(write).toHaveBeenCalledOnce();
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY)!);
    expect(saved).toMatchObject([{ id: "first", caseFacts: { summary: "第一份經使用者縮短的摘要。", revision: 2, summary_origin: "user" }, legacyCaseFacts: legacy }, { id: "second", caseFacts: { summary: "第二份經使用者縮短的摘要。", revision: 2, summary_origin: "user" }, legacyCaseFacts: legacy }]);
    expect(sendChat).not.toHaveBeenCalled();
    await act(async () => { await result.current.saveCaseFacts({ ...emptySummaryContext(), summary: "恢復後的正常摘要修改。" }); });
    expect(write).toHaveBeenCalledTimes(2);
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)[1].caseFacts.summary).toBe("恢復後的正常摘要修改。");
  });

  it.each([
    ["invalid", JSON.stringify([record("blocked"), { id: 123 }])],
    ["future", JSON.stringify([{ ...record("blocked"), schemaVersion: 99 }])],
    ["unavailable", "invalid json"],
  ])("cannot unlock %s storage through a valid explicit summary edit", async (issue, raw) => {
    localStorage.setItem(STORAGE_KEY, raw);
    const write = vi.spyOn(localStorage, "setItem");
    const { result } = renderHook(() => useConversation("blocked"));
    await act(async () => { await Promise.resolve(); });
    expect(result.current.storageIssue).toBe(issue);
    await act(async () => { await result.current.saveCaseFacts({ ...emptySummaryContext(), summary: "有效但只留在記憶體的修正。" }); });
    expect(result.current.storageIssue).toBe(issue);
    expect(write).not.toHaveBeenCalled();
    expect(localStorage.getItem(STORAGE_KEY)).toBe(raw);
  });

  it("keeps a failed recovery read-only even if storage later becomes writable", async () => {
    const raw = JSON.stringify([record("quota")]);
    localStorage.setItem(STORAGE_KEY, raw);
    const { result } = renderHook(() => useConversation("quota"));
    await act(async () => { await Promise.resolve(); });
    const write = vi.spyOn(localStorage, "setItem").mockImplementationOnce(() => { throw new DOMException("Full", "QuotaExceededError"); });
    await act(async () => { await result.current.saveCaseFacts({ ...emptySummaryContext(), summary: "第一次縮短。" }); });
    expect(result.current.storageIssue).toBe("unavailable");
    expect(write).toHaveBeenCalledOnce();
    await act(async () => { await result.current.saveCaseFacts({ ...emptySummaryContext(), summary: "第二次縮短。" }); });
    expect(write).toHaveBeenCalledOnce();
    expect(localStorage.getItem(STORAGE_KEY)).toBe(raw);
  });

  it("refuses to overwrite a different snapshot from another tab", async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([record("conflict")]));
    const { result } = renderHook(() => useConversation("conflict"));
    await act(async () => { await Promise.resolve(); });
    const newer = JSON.stringify([{ ...record("conflict"), schemaVersion: 99 }]);
    localStorage.setItem(STORAGE_KEY, newer);
    const write = vi.spyOn(localStorage, "setItem");
    await act(async () => { await result.current.saveCaseFacts({ ...emptySummaryContext(), summary: "縮短。" }); });
    expect(result.current.storageIssue).toBe("invalid");
    expect(write).not.toHaveBeenCalled();
    expect(localStorage.getItem(STORAGE_KEY)).toBe(newer);
  });
});
