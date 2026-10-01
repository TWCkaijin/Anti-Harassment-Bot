import { trackAnalytics } from "../services/analytics";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, sendChat, checkHealth, type ChatGuidance, type ChatProgress, type ChatResponse } from "../services/api";
import {
  MAX_USER_MESSAGE_CHARACTERS,
  USER_MESSAGE_TOO_LONG_ERROR,
} from "./conversationHistory";
import { useConversation } from "./useConversation";
import { toSummaryContext, type LegacyCaseContext } from "../services/caseFacts";

vi.mock("../services/api", async () => {
  const actual = await vi.importActual<typeof import("../services/api")>("../services/api");
  return { ...actual, sendChat: vi.fn(), checkHealth: vi.fn() };
});

vi.mock("../services/privacyReview", () => ({ requestPrivacyReview: vi.fn(async request => request) }));
vi.mock("../services/analytics", () => ({ hasAnalyticsConsent: () => true, trackAnalytics: vi.fn() }));

const successfulResponse: ChatResponse = {
  reply: "我會陪你整理下一步。",
  session_id: "server-session",
  anonymized: false,
  rag_used: { status: false, sources: [] },
  suggested_replies: ["繼續", "查看資源"],
  action_buttons: [],
  interaction_mode: "answer",
  clarifying_questions: [],
};

beforeEach(() => {
  window.localStorage.clear();
  vi.mocked(sendChat).mockReset();
  vi.mocked(checkHealth).mockReset().mockResolvedValue({ status: "ok", timestamp: "test", version: "test", environment: "test" });
  vi.mocked(trackAnalytics).mockClear();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const v2Response = (revision: number, extra: Partial<ChatResponse> = {}): ChatResponse => ({ ...successfulResponse, contract_version: 2, facts_revision: revision, fact_updates: [], clarification: null, answer_sections: [], execution: { route: "direct_retrieval", model_calls: 1 }, ...extra });
const v2Health = { status: "ok", timestamp: "test", version: "test", environment: "test", capabilities: { chat_contract_versions: [1, 2] } };

describe("v3 conversation contracts", () => {
  const v3Health = { ...v2Health, capabilities: { chat_contract_versions: [1, 2, 3] } };
  const v3Response = (revision: number, extra: Partial<ChatResponse> = {}): ChatResponse => ({ ...v2Response(revision), contract_version: 3, analysis: [], ...extra });
  it("selects v3 after health and sends multi-value facts without reducing them", async () => {
    vi.mocked(checkHealth).mockResolvedValue(v3Health);
    vi.mocked(sendChat).mockImplementation(async request => v3Response(request.case_context!.revision));
    const { result } = renderHook(() => useConversation("v3-multi"));
    await act(async () => { await Promise.resolve(); });
    const clarificationAnswer = { question_id: "case.subject_role.0", fact_key: "subject_role" as const, status: "provided" as const, value: ["學生", "兼職員工"], selection_mode: "multiple" as const, max_selections: 2, validation_token: "signed", context_scope: "personal" as const };
    await act(async () => { await result.current.sendMessage("學生、兼職員工", undefined, undefined, { answers: [], factsRevision: 0, clarificationAnswer }); });
    expect(vi.mocked(sendChat).mock.calls[0][0]).toMatchObject({ contract_version: 3, case_context: { schema_version: 2, revision: 1, facts: { subject_role: { status: "provided", value: ["學生", "兼職員工"] } } }, clarification_answer: clarificationAnswer });
    expect(result.current.caseFacts.facts.subject_role?.value).toEqual(["學生", "兼職員工"]);
  });
  it("keeps a third-person typed answer in history while preserving the personal summary", async () => {
    const caseFacts: LegacyCaseContext = { schema_version: 2, revision: 1, facts: { subject_role: { status: "provided", value: "學生" } } };
    vi.mocked(checkHealth).mockResolvedValue(v3Health);
    vi.mocked(sendChat).mockResolvedValue(v3Response(1));
    const { result } = renderHook(() => useConversation("v3-scenario"));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await result.current.saveCaseFacts(caseFacts); });
    const clarificationAnswer = { question_id: "scenario.subject_role.4", fact_key: "subject_role" as const, status: "provided" as const, value: ["員工", "實習生"], selection_mode: "multiple" as const, max_selections: 2, validation_token: "signed-scenario", context_scope: "scenario" as const };
    await act(async () => { await result.current.sendMessage("朋友是員工、實習生", undefined, undefined, { answers: [{ question: "朋友的身分？", answer: "員工、實習生" }], factsRevision: 1, clarificationAnswer }); });
    expect(vi.mocked(sendChat).mock.calls[0][0]).toMatchObject({ contract_version: 3, case_context: caseFacts, clarification_answer: clarificationAnswer });
    expect(result.current.caseFacts).toEqual(caseFacts);
    expect(result.current.messages.at(-2)).toMatchObject({ role: "user", content: "朋友是員工、實習生", replyContext: { clarificationAnswer } });
  });
  it("blocks v2 fallback for saved arrays without rewriting or silently joining their values", async () => {
    const caseFacts = { schema_version: 2, revision: 4, facts: { subject_role: { status: "provided", value: ["學生", "兼職員工"] } } };
    localStorage.setItem("harass_bot_conversations", JSON.stringify([{ id: "v3-fallback", createdAt: 1, schemaVersion: 3, caseFacts, messages: [{ id: "u", role: "user", content: "原問題", timestamp: 1 }] }]));
    vi.mocked(checkHealth).mockResolvedValue(v2Health);
    const { result } = renderHook(() => useConversation("v3-fallback"));
    await act(async () => { await Promise.resolve(); });
    expect(result.current.incompatibleSummary).toBe(true);
    await act(async () => { await result.current.sendMessage("繼續"); });
    expect(sendChat).not.toHaveBeenCalled();
    expect(result.current.caseFacts).toEqual(toSummaryContext(caseFacts as LegacyCaseContext));
    expect(JSON.parse(localStorage.getItem("harass_bot_conversations")!)[0]).toMatchObject({ caseFacts: toSummaryContext(caseFacts as LegacyCaseContext), legacyCaseFacts: caseFacts });
  });
  it("streams stage explanations but persists only final validated analysis", async () => {
    vi.mocked(checkHealth).mockResolvedValue(v3Health);
    const preview = { stage: "understanding" as const, summary: "正在整理", facts: [], source_labels: [], limitations: [] };
    const final = { ...preview, summary: "已依提供資訊整理", facts: ["學生"] };
    vi.mocked(sendChat).mockImplementation(async (_request, _signal, _delta, _guidance, _progress, analysis) => { analysis?.(preview); return v3Response(0, { analysis: [final] }); });
    const { result } = renderHook(() => useConversation("v3-analysis"));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await result.current.sendMessage("我是學生"); });
    expect(result.current.messages.at(-1)?.analysis).toEqual([final]);
    expect(localStorage.getItem("harass_bot_conversations")).toContain("已依提供資訊整理");
    expect(localStorage.getItem("harass_bot_conversations")).not.toContain("正在整理");
  });
  it("does not automatically replay a request after analysis has become visible", async () => {
    vi.mocked(checkHealth).mockResolvedValue(v3Health);
    const entry = { stage: "understanding" as const, summary: "正在整理已知條件", facts: [], source_labels: [], limitations: [] };
    vi.mocked(sendChat).mockImplementation(async (_request, _signal, _delta, _guidance, _progress, analysis) => { analysis?.(entry); throw new ApiError(502, "中斷", undefined, true); });
    const { result } = renderHook(() => useConversation("v3-analysis-error"));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await result.current.sendMessage("合成情境"); });
    expect(sendChat).toHaveBeenCalledOnce();
    expect(result.current.messages.at(-1)).toMatchObject({ isError: true, analysis: [entry] });
  });
});

describe("backend connection recovery", () => {
  it("checks the latest handshake when an older send callback survives a reconnect", async () => {
    vi.mocked(checkHealth).mockResolvedValueOnce(v2Health);
    const { result } = renderHook(() => useConversation("old-send-callback"));
    await act(async () => { await Promise.resolve(); });
    const oldSend = result.current.sendMessage;
    let resolveHealth!: (value: typeof v2Health) => void;
    vi.mocked(checkHealth).mockReturnValueOnce(new Promise(resolve => { resolveHealth = resolve; }));
    act(() => { void result.current.reconnectBackend(); });
    await act(async () => { await oldSend("圖片轉換後才送出"); });
    expect(sendChat).not.toHaveBeenCalled();
    await act(async () => { resolveHealth({ ...v2Health, capabilities: { chat_contract_versions: [1, 2, 3, 4] } }); });
    vi.mocked(sendChat).mockResolvedValueOnce({ ...successfulResponse, contract_version: 4, context_revision: 0, summary_update: null, clarification: null, analysis: [], execution: { route: "direct_retrieval", model_calls: 1 } });
    await act(async () => { await oldSend("圖片轉換後才送出"); });
    expect(sendChat).toHaveBeenCalledOnce();
    expect(vi.mocked(sendChat).mock.calls[0][0]).toMatchObject({ contract_version: 4, case_context: { schema_version: 3 } });
  });
  it("blocks empty sessions after failed health and during reconnect without replaying a queued send", async () => {
    vi.mocked(checkHealth).mockRejectedValueOnce(new Error("offline"));
    const { result } = renderHook(() => useConversation("empty-offline"));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await result.current.sendMessage("第一個問題"); });
    expect(sendChat).not.toHaveBeenCalled();
    let resolveHealth!: (value: typeof v2Health) => void;
    vi.mocked(checkHealth).mockReturnValueOnce(new Promise(resolve => { resolveHealth = resolve; }));
    act(() => { void result.current.reconnectBackend(); });
    await act(async () => { await result.current.sendMessage("第一個問題"); });
    expect(sendChat).not.toHaveBeenCalled();
    await act(async () => { resolveHealth(v2Health); });
    expect(result.current.isBackendConnected).toBe(true);
    expect(result.current.messages).toEqual([]);
    expect(sendChat).not.toHaveBeenCalled();
  });
  it.each([{ versions: [] }, { versions: [5] }])("does not silently select v1 when an explicit capabilities list has no supported versions: $versions", async ({ versions }) => {
    vi.mocked(checkHealth).mockResolvedValue({ ...v2Health, capabilities: { chat_contract_versions: versions } });
    const { result } = renderHook(() => useConversation("unsupported-health"));
    await act(async () => { await Promise.resolve(); });
    expect(result.current.isBackendConnected).toBe(false);
    await act(async () => { await result.current.sendMessage("第一個問題"); });
    expect(sendChat).not.toHaveBeenCalled();
  });
  it.each([undefined, 2, 3] as const)("rejects a v4 response mismatch with version %s without retry or summary updates", async version => {
    vi.mocked(checkHealth).mockResolvedValue({ ...v2Health, capabilities: { chat_contract_versions: [1, 2, 3, 4] } });
    vi.mocked(sendChat).mockResolvedValue({ ...v2Response(0), contract_version: version, reply: "不應成為已完成回覆", analysis: [], fact_updates: [{ fact_key: "other_role", status: "provided", value: "主管", kind: "confirmation", evidence: "合成資料" }] });
    const { result } = renderHook(() => useConversation("contract-mismatch"));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await result.current.sendMessage("第一個問題"); });
    expect(sendChat).toHaveBeenCalledOnce();
    expect(result.current.messages.at(-1)).toMatchObject({ isError: true });
    expect(result.current.messages.at(-1)?.content).not.toContain("不應成為已完成回覆");
    expect(result.current.caseFacts).toMatchObject({ revision: 0, summary: "", facts: {} });
  });
  it("preserves a saved summary through a failed health check and reconnects without replaying chat", async () => {
    const facts = { schema_version: 1, revision: 3, facts: { other_role: { status: "provided", value: "同事" } } };
    localStorage.setItem("harass_bot_conversations", JSON.stringify([{ id: "reconnect", createdAt: 1, schemaVersion: 2, caseFacts: facts, messages: [{ id: "u", role: "user", content: "原本的問題", timestamp: 1 }] }]));
    vi.mocked(checkHealth).mockRejectedValueOnce(new ApiError(404, "Not found"));
    const { result } = renderHook(() => useConversation("reconnect"));
    expect(result.current.isBackendConnected).toBeNull();
    expect(result.current.summaryRequiresConnection).toBe(true);
    expect(result.current.incompatibleSummary).toBe(false);
    await act(async () => { await result.current.sendMessage("繼續原本的問題"); });
    expect(result.current.isBackendConnected).toBe(false);
    expect(result.current.summaryRequiresConnection).toBe(true);
    expect(result.current.incompatibleSummary).toBe(false);
    expect(result.current.caseFacts).toEqual(toSummaryContext(facts as LegacyCaseContext));
    expect(sendChat).not.toHaveBeenCalled();

    vi.mocked(checkHealth).mockResolvedValueOnce({ ...v2Health, capabilities: { chat_contract_versions: [1, 2, 3, 4] } });
    await act(async () => { await result.current.reconnectBackend(); });
    expect(result.current.isBackendConnected).toBe(true);
    expect(result.current.contractV2).toBe(true);
    expect(result.current.summaryRequiresConnection).toBe(false);
    expect(result.current.incompatibleSummary).toBe(false);
    expect(sendChat).not.toHaveBeenCalled();
    vi.mocked(sendChat).mockResolvedValueOnce({ ...successfulResponse, contract_version: 4, context_revision: 3, summary_update: null, clarification: null, analysis: [], execution: { route: "direct_retrieval", model_calls: 1 } });
    await act(async () => { await result.current.sendMessage("繼續原本的問題"); });
    expect(sendChat).toHaveBeenCalledOnce();
    expect(vi.mocked(sendChat).mock.calls[0][0]).toMatchObject({ contract_version: 4, case_context: toSummaryContext(facts as LegacyCaseContext) });
  });

  it("aborts a health check when the conversation unmounts", async () => {
    vi.mocked(checkHealth).mockReturnValueOnce(new Promise(() => {}));
    const { unmount } = renderHook(() => useConversation("unmount-health"));
    const signal = vi.mocked(checkHealth).mock.calls[0][0];
    expect(signal?.aborted).toBe(false);
    unmount();
    expect(signal?.aborted).toBe(true);
  });

  it("aborts the previous check and ignores its late response after reconnection", async () => {
    let resolveOld!: (value: typeof v2Health) => void;
    vi.mocked(checkHealth).mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve; }));
    const { result } = renderHook(() => useConversation("stale-health"));
    const signal = vi.mocked(checkHealth).mock.calls[0][0];
    vi.mocked(checkHealth).mockResolvedValueOnce(v2Health);
    await act(async () => { await result.current.reconnectBackend(); });
    expect(signal?.aborted).toBe(true);
    await act(async () => { resolveOld({ ...v2Health, capabilities: { chat_contract_versions: [1] } }); });
    expect(result.current.isBackendConnected).toBe(true);
    expect(result.current.contractV2).toBe(true);
    expect(sendChat).not.toHaveBeenCalled();
  });
});

describe("local case facts and request ownership", () => {
  it("regenerates the original user request after two typed answers and repeated summary edits", async () => {
    vi.mocked(checkHealth).mockResolvedValue(v2Health);
    vi.mocked(sendChat).mockImplementation(async request => v2Response(request.case_context!.revision));
    const { result } = renderHook(() => useConversation("original-intent"));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await result.current.sendMessage("我想知道如何申訴"); });
    const originId = result.current.messages[0].id;
    await act(async () => { await result.current.sendMessage("對方是誰？\n主管", undefined, undefined, { answers: [{ question: "對方是誰？", answer: "主管" }], factsRevision: 0, clarificationAnswer: { question_id: "case.other_role.0", fact_key: "other_role", status: "provided", value: "主管" } }); });
    await act(async () => { await result.current.sendMessage("與工作有關？\n是", undefined, undefined, { answers: [{ question: "與工作有關？", answer: "是" }], factsRevision: 1, clarificationAnswer: { question_id: "case.work_related.1", fact_key: "work_related", status: "provided", value: "是" } }); });
    await act(async () => { await result.current.saveCaseFacts({ schema_version: 1, revision: result.current.caseFacts.revision, facts: { ...result.current.caseFacts.facts, other_role: { status: "provided", value: "同事" } } }, true); });
    expect(vi.mocked(sendChat).mock.calls[3][0].message).toBe("我想知道如何申訴");
    expect(result.current.messages.filter(message => message.role === "user").at(-1)).toMatchObject({ requestKind: "regeneration", originRequestId: originId });
    await act(async () => { await result.current.saveCaseFacts(result.current.caseFacts, true); });
    expect(vi.mocked(sendChat).mock.calls[4][0].message).toBe("我想知道如何申訴");
    expect(result.current.messages.filter(message => message.role === "user").at(-1)?.originRequestId).toBe(originId);
  });

  it("keeps facts-only edits local when there is no original request to regenerate", async () => {
    vi.mocked(checkHealth).mockResolvedValue(v2Health);
    const { result } = renderHook(() => useConversation("facts-only"));
    await act(async () => { await Promise.resolve(); });
    expect(result.current.canRegenerate).toBe(false);
    await act(async () => { await result.current.saveCaseFacts({ schema_version: 1, revision: 0, facts: { city: { status: "declined" } } }, true); });
    expect(sendChat).not.toHaveBeenCalled();
    expect(result.current.caseFacts.facts.city).toEqual({ status: "declined" });
  });
  it("blocks an existing summary on legacy services without erasing it, and permits a new legacy chat", async () => {
    const facts = { schema_version: 1, revision: 3, facts: { other_role: { status: "provided", value: "同事" } } };
    localStorage.setItem("harass_bot_conversations", JSON.stringify([{ id: "v2-existing", createdAt: 1, schemaVersion: 2, caseFacts: facts, messages: [{ id: "u", role: "user", content: "原本的問題", timestamp: 1 }] }]));
    vi.mocked(sendChat).mockResolvedValue(successfulResponse);
    const { result } = renderHook(() => useConversation("v2-existing"));
    await act(async () => { await Promise.resolve(); });
    expect(result.current.incompatibleSummary).toBe(true);
    await act(async () => { await result.current.sendMessage("接續原本的問題"); });
    expect(sendChat).not.toHaveBeenCalled();
    expect(result.current.caseFacts).toEqual(toSummaryContext(facts as LegacyCaseContext));
    act(() => { result.current.createNewSession(); });
    expect(result.current.incompatibleSummary).toBe(false);
    await act(async () => { await result.current.sendMessage("新的問題"); });
    expect(sendChat).toHaveBeenCalledOnce();
    expect(vi.mocked(sendChat).mock.calls[0][0]).not.toHaveProperty("contract_version");
    expect(result.current.sessions.find(session => session.id === "v2-existing")?.caseFacts).toEqual(toSummaryContext(facts as LegacyCaseContext));
  });
  it("waits for a completed capability handshake even for a new empty session", async () => {
    let resolveHealth!: (value: typeof v2Health) => void;
    vi.mocked(checkHealth).mockReturnValueOnce(new Promise(resolve => { resolveHealth = resolve; }));
    vi.mocked(sendChat).mockResolvedValue(v2Response(0));
    const { result } = renderHook(() => useConversation("handshake"));
    await act(async () => { await result.current.sendMessage("第一個問題"); });
    expect(sendChat).not.toHaveBeenCalled();
    expect(result.current.messages).toEqual([]);
    await act(async () => { resolveHealth(v2Health); });
    expect(sendChat).not.toHaveBeenCalled();
    await act(async () => { await result.current.sendMessage("第一個問題"); });
    expect(sendChat).toHaveBeenCalledOnce();
    expect(vi.mocked(sendChat).mock.calls[0][0]).toMatchObject({ contract_version: 2, case_context: { schema_version: 1, revision: 0, facts: {} } });
  });

  it("applies explicit updates only on done and marks conflicting information pending", async () => {
    vi.mocked(checkHealth).mockResolvedValue(v2Health);
    const pending = deferredResponse();
    const { result } = renderHook(() => useConversation("v2-done"));
    await act(async () => { await Promise.resolve(); });
    let request!: Promise<void | boolean>;
    await act(async () => { request = result.current.sendMessage("我是學生"); await Promise.resolve(); });
    act(() => pending.delta("學生"));
    expect(result.current.caseFacts.facts).toEqual({});
    await act(async () => {
      pending.resolve(v2Response(0, { fact_updates: [{ fact_key: "subject_role", status: "provided", value: "學生", evidence: "我是學生", kind: "explicit" }] }));
      await request;
    });
    expect(result.current.caseFacts).toMatchObject({ revision: 1, facts: { subject_role: { status: "provided", value: "學生" } } });
    vi.mocked(sendChat).mockResolvedValueOnce(v2Response(1, { fact_updates: [{ fact_key: "subject_role", status: "provided", value: "員工", evidence: "我是員工", kind: "explicit" }] }));
    await act(async () => { await result.current.sendMessage("我是員工"); });
    expect(result.current.caseFacts.facts.subject_role?.value).toBe("學生");
    expect(result.current.pendingFacts).toHaveLength(1);
  });

  it("keeps a new request alive when a replaced request finishes after a correction", async () => {
    vi.mocked(checkHealth).mockResolvedValue(v2Health);
    let resolveOld!: (value: ChatResponse) => void;
    let oldDelta!: (text: string) => void;
    let oldProgress!: (value: ChatProgress) => void;
    vi.mocked(sendChat).mockImplementationOnce((_request, _signal, delta, _guidance, progress) => {
      oldDelta = delta!;
      oldProgress = progress!;
      return new Promise(resolve => { resolveOld = resolve; });
    });
    const { result } = renderHook(() => useConversation("replace"));
    await act(async () => { await Promise.resolve(); });
    let oldRequest!: Promise<void | boolean>;
    await act(async () => { oldRequest = result.current.sendMessage("原本的問題"); await Promise.resolve(); });
    const replacement = deferredResponse();
    let newRequest!: Promise<void | boolean>;
    await act(async () => { newRequest = result.current.saveCaseFacts({ schema_version: 1, revision: 0, facts: { other_role: { status: "provided", value: "同事" } } }, true); await Promise.resolve(); });
    expect(vi.mocked(sendChat).mock.calls[1][0].case_context).toMatchObject({ revision: 1, facts: { other_role: { value: "同事" } } });
    act(() => replacement.delta("新的回覆"));
    await act(async () => { resolveOld(v2Response(0)); await oldRequest; });
    act(() => oldDelta("過期的文字"));
    act(() => oldProgress({ phase: "retrieving", elapsed_ms: 9999 }));
    expect(result.current.isLoading).toBe(true);
    expect(result.current.messages.at(-1)?.content).toBe("新的回覆");
    expect(result.current.messages.at(-1)?.processingTrace?.steps.map(step => step.phase)).toEqual(["connecting", "generating"]);
    await act(async () => { replacement.resolve(v2Response(1, { reply: "依新摘要回答" })); await newRequest; });
    expect(result.current.messages.at(-1)?.content).toBe("依新摘要回答");
    expect(result.current.caseFacts.facts.other_role?.value).toBe("同事");
  });

  it("rejects answers from an obsolete summary and preserves unknown on a successful typed reply", async () => {
    vi.mocked(checkHealth).mockResolvedValue(v2Health);
    const { result } = renderHook(() => useConversation("stale-question"));
    await act(async () => { await Promise.resolve(); });
    const answer = { answers: [{ question: "對方是？", answer: "不確定" }], factsRevision: 8, clarificationAnswer: { question_id: "case.other_role.8", fact_key: "other_role" as const, status: "unknown" as const } };
    await act(async () => { await result.current.sendMessage("不確定", undefined, undefined, answer); });
    expect(sendChat).not.toHaveBeenCalled();
    vi.mocked(sendChat).mockResolvedValueOnce(v2Response(1));
    await act(async () => { await result.current.sendMessage("不確定", undefined, undefined, { ...answer, factsRevision: 0 }); });
    expect(result.current.caseFacts.facts.other_role).toEqual({ status: "unknown" });
    expect(vi.mocked(sendChat).mock.calls[0][0].clarification_answer).toEqual(answer.clarificationAnswer);
  });

  it("surfaces storage failure without discarding messages", async () => {
    vi.mocked(sendChat).mockResolvedValue(successfulResponse);
    const { result } = renderHook(() => useConversation("full"));
    await act(async () => { await Promise.resolve(); });
    vi.spyOn(localStorage, "setItem").mockImplementation(() => { throw new DOMException("Full", "QuotaExceededError"); });
    await act(async () => { await result.current.sendMessage("留在分頁的問題"); });
    expect(result.current.storageIssue).toBe("unavailable");
    expect(result.current.messages).toHaveLength(2);
  });
});

describe("useConversation cancellation", () => {
  it("does not send the cancelled UI record in the next request", async () => {
    vi.mocked(sendChat).mockImplementationOnce((_request, signal) =>
      new Promise((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      })
    );
    const { result } = renderHook(() => useConversation("test-session"));
    await act(async () => { await Promise.resolve(); });

    let cancelledRequest!: Promise<void | boolean>;
    await act(async () => {
      cancelledRequest = result.current.sendMessage("第一個問題");
      await Promise.resolve();
    });
    act(() => {
      result.current.stopCurrentResponse();
    });
    await act(async () => {
      await cancelledRequest;
    });

    expect(result.current.messages.at(-1)?.isCancelled).toBe(true);

    vi.mocked(sendChat).mockResolvedValueOnce(successfulResponse);
    let nextRequest!: Promise<void | boolean>;
    await act(async () => {
      nextRequest = result.current.sendMessage("下一個問題");
      await Promise.resolve();
    });

    expect(vi.mocked(sendChat).mock.calls[1]?.[0].history).toEqual([
      { role: "user", content: "第一個問題" },
    ]);

    await act(async () => {
      await vi.runAllTimersAsync();
      await nextRequest;
    });
  });
});

describe("useConversation image requests", () => {
  it("rejects a preview URL without image data and leaves history unchanged", async () => {
    const { result } = renderHook(() => useConversation("image-session"));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { expect(await result.current.sendMessage("文字", undefined, "blob:missing")).toBe(false); });
    expect(sendChat).not.toHaveBeenCalled();
    expect(result.current.messages).toEqual([]);
    expect(result.current.error).toContain("圖片資料不完整");
    vi.mocked(sendChat).mockResolvedValueOnce(successfulResponse);
    await act(async () => { await result.current.sendMessage("之後的文字問題"); });
    expect(vi.mocked(sendChat).mock.calls[0][0].history).toEqual([]);
  });
});

describe("useConversation quoted replies", () => {
  it("persists only approved plain-text replies, not the original unreviewed presentation metadata", async () => {
    vi.mocked(sendChat).mockResolvedValue(successfulResponse);
    const { result, unmount } = renderHook(() => useConversation("reply-session"));
    await act(async () => { await Promise.resolve(); });
    const content = "事情發生在哪裡？\n在學校";
    const replyContext = { answers: [{ question: "事情發生在哪裡？", answer: "在學校" }] };
    let request!: Promise<void | boolean>;
    await act(async () => {
      request = result.current.sendMessage(content, undefined, undefined, replyContext);
      await Promise.resolve();
    });
    expect(result.current.messages[0]).toMatchObject({ content });
    expect(result.current.messages[0]).not.toHaveProperty("replyContext");
    expect(vi.mocked(sendChat).mock.calls[0][0].message).toBe(content);
    expect(vi.mocked(sendChat).mock.calls[0][0]).not.toHaveProperty("replyContext");
    await act(async () => {
      await vi.runAllTimersAsync();
      await request;
    });
    unmount();

    const restored = renderHook(() => useConversation("reply-session"));
    await act(async () => { await Promise.resolve(); });
    expect(restored.result.current.messages[0]).toMatchObject({ content });
    expect(restored.result.current.messages[0]).not.toHaveProperty("replyContext");
    await act(async () => {
      request = restored.result.current.sendMessage("下一步呢？");
      await Promise.resolve();
    });
    expect(vi.mocked(sendChat).mock.calls[1][0].history[0]).toEqual({ role: "user", content });
    expect(restored.result.current.messages.at(-1)).not.toHaveProperty("replyContext");
    await act(async () => {
      await vi.runAllTimersAsync();
      await request;
    });
  });
});

describe("useConversation retries", () => {
  it("retries a retryable 502 at most twice before succeeding", async () => {
    vi.mocked(sendChat)
      .mockRejectedValueOnce(new ApiError(502, "Bad gateway", "暫時錯誤", true))
      .mockRejectedValueOnce(new ApiError(502, "Bad gateway", "暫時錯誤", true))
      .mockResolvedValueOnce(successfulResponse);
    const { result } = renderHook(() => useConversation("retry-session"));
    await act(async () => { await Promise.resolve(); });

    let request!: Promise<void | boolean>;
    await act(async () => {
      request = result.current.sendMessage("請重試");
      await Promise.resolve();
    });

    await act(async () => {
      await vi.runAllTimersAsync();
      await request;
    });

    expect(sendChat).toHaveBeenCalledTimes(3);
    expect(result.current.messages.at(-1)).toMatchObject({
      role: "assistant",
      content: successfulResponse.reply,
    });
  });

  it("does not retry a permanent API error", async () => {
    vi.mocked(sendChat).mockRejectedValueOnce(
      new ApiError(400, "Bad request", "請求無效", false),
    );
    const { result } = renderHook(() => useConversation("permanent-error-session"));
    await act(async () => { await Promise.resolve(); });

    await act(async () => {
      await result.current.sendMessage("無效請求");
    });

    expect(sendChat).toHaveBeenCalledTimes(1);
    expect(result.current.messages.at(-1)).toMatchObject({
      role: "assistant",
      isError: true,
      content: "服務暫時無法使用：請求無效",
    });
  });

  it("does not automatically retry a rate-limit response", async () => {
    vi.mocked(sendChat).mockRejectedValueOnce(
      new ApiError(429, "Too many requests", "請稍後再試", true),
    );
    const { result } = renderHook(() => useConversation("rate-limit-session"));
    await act(async () => { await Promise.resolve(); });

    let request!: Promise<void | boolean>;
    await act(async () => {
      request = result.current.sendMessage("新的問題");
      await Promise.resolve();
    });

    await act(async () => {
      await request;
    });

    expect(sendChat).toHaveBeenCalledTimes(1);
    expect(result.current.messages.at(-1)).toMatchObject({
      role: "assistant",
      isError: true,
      content: "服務暫時無法使用：請稍後再試",
    });
  });
});

describe("useConversation message limits", () => {
  it("rejects overflow without truncating and accepts exactly 2,000 characters", async () => {
    const boundary = "x".repeat(MAX_USER_MESSAGE_CHARACTERS);
    const overflow = `${boundary}x`;
    const { result } = renderHook(() => useConversation("message-limit-session"));
    await act(async () => { await Promise.resolve(); });

    await act(async () => {
      await result.current.sendMessage(overflow);
    });

    expect(sendChat).not.toHaveBeenCalled();
    expect(result.current.error).toBe(USER_MESSAGE_TOO_LONG_ERROR);
    expect(result.current.messages).toEqual([]);

    vi.mocked(sendChat).mockResolvedValueOnce(successfulResponse);
    let request!: Promise<void | boolean>;
    await act(async () => {
      request = result.current.sendMessage(boundary);
      await Promise.resolve();
    });

    expect(vi.mocked(sendChat).mock.calls[0]?.[0].message).toBe(boundary);

    await act(async () => {
      await vi.runAllTimersAsync();
      await request;
    });
  });
});

function deferredResponse() {
  let resolve!: (response: ChatResponse) => void;
  let reject!: (error: unknown) => void;
  let delta!: (text: string) => void;
  let guidance!: (value: ChatGuidance) => void;
  let progress!: (value: ChatProgress) => void;
  const promise = new Promise<ChatResponse>((res, rej) => { resolve = res; reject = rej; });
  vi.mocked(sendChat).mockImplementationOnce((_request, signal, onDelta, onGuidance, onProgress) => {
    delta = onDelta!;
    guidance = onGuidance!;
    progress = onProgress!;
    signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    return promise;
  });
  return {
    resolve, reject,
    delta: (text: string) => delta(text),
    guidance: (value: ChatGuidance) => guidance(value),
    progress: (value: ChatProgress) => progress(value),
  };
}

describe("useConversation actual progress", () => {
  it("changes phases only on observed events, without manufacturing text or advancing with time", async () => {
    const pending = deferredResponse();
    const { result } = renderHook(() => useConversation("progress-session"));
    await act(async () => { await Promise.resolve(); });
    let request!: Promise<void | boolean>;
    await act(async () => { request = result.current.sendMessage("請幫我了解"); await Promise.resolve(); });
    expect(result.current.retryStatus).toBe("正在等待伺服器回應");
    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages.at(-1)).toMatchObject({ content: "", processingTrace: { outcome: "running", steps: [{ phase: "connecting", attempt: 0 }] } });
    expect(JSON.parse(localStorage.getItem("harass_bot_conversations")!)[0].messages).toHaveLength(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(result.current.retryStatus).toBe("正在等待伺服器回應");
    act(() => pending.progress({ phase: "waiting_model", elapsed_ms: 64 }));
    expect(result.current.retryStatus).toBe("正在等待 AI 回應");
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(result.current.retryStatus).toBe("正在等待 AI 回應");
    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages.at(-1)?.processingTrace?.steps.map(step => step.phase)).toEqual(["connecting", "waiting_model"]);
    act(() => pending.progress({ phase: "retrieving", elapsed_ms: 60_200 }));
    expect(result.current.retryStatus).toBe("正在檢索資料庫");
    act(() => pending.delta("我會陪您"));
    expect(result.current.retryStatus).toBe("正在生成回覆");
    act(() => pending.guidance({ interaction_mode: "answer", suggested_replies: ["下一步"] }));
    expect(result.current.retryStatus).toBe("正在產生後續引導");
    act(() => pending.progress({ phase: "validating", elapsed_ms: 62_000 }));
    expect(result.current.retryStatus).toBe("正在整理回覆");
    await act(async () => { pending.resolve(successfulResponse); await request; });
    expect(result.current.retryStatus).toBeNull();
    expect(result.current.isLoading).toBe(false);
    const trace = result.current.messages.at(-1)?.processingTrace;
    expect(trace?.outcome).toBe("complete");
    expect(trace?.steps.map(step => step.phase)).toEqual(["connecting", "waiting_model", "retrieving", "generating", "guidance", "validating"]);
    expect(JSON.parse(localStorage.getItem("harass_bot_conversations")!)[0].messages.at(-1).processingTrace).toEqual(trace);
    expect(trackAnalytics).not.toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ processingTrace: expect.anything() }));
  });

  it("still retries after progress-only failures and resets the status for the new request", async () => {
    vi.mocked(sendChat).mockImplementationOnce(async (_request, _signal, _delta, _guidance, onProgress) => {
      onProgress?.({ phase: "waiting_model", elapsed_ms: 40 });
      throw new ApiError(502, "retry", "", true);
    });
    const pending = deferredResponse();
    const { result } = renderHook(() => useConversation("progress-retry-session"));
    await act(async () => { await Promise.resolve(); });
    let request!: Promise<void | boolean>;
    await act(async () => { request = result.current.sendMessage("請重試"); await Promise.resolve(); });
    expect(result.current.retryStatus).toBe("伺服器回傳錯誤，正在重試中");
    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages.at(-1)?.processingTrace?.steps.at(-1)?.phase).toBe("retrying");
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(sendChat).toHaveBeenCalledTimes(2);
    expect(result.current.retryStatus).toBe("正在等待伺服器回應");
    act(() => pending.progress({ phase: "preparing", elapsed_ms: 10 }));
    expect(result.current.retryStatus).toBe("正在準備回覆");
    await act(async () => { pending.resolve(successfulResponse); await request; });
    expect(result.current.retryStatus).toBeNull();
    expect(result.current.messages.at(-1)?.processingTrace?.steps.map(step => [step.phase, step.attempt])).toEqual([
      ["connecting", 0], ["waiting_model", 0], ["retrying", 0], ["connecting", 1], ["preparing", 1],
    ]);
  });

  it("clears progress on stop and ignores late callbacks", async () => {
    const pending = deferredResponse();
    const { result } = renderHook(() => useConversation("progress-stop-session"));
    await act(async () => { await Promise.resolve(); });
    let request!: Promise<void | boolean>;
    await act(async () => { request = result.current.sendMessage("先停下"); await Promise.resolve(); });
    act(() => pending.progress({ phase: "retrieving", elapsed_ms: 70 }));
    await act(async () => { result.current.stopCurrentResponse(); await request; });
    act(() => pending.progress({ phase: "generating", elapsed_ms: 80 }));
    expect(result.current.retryStatus).toBeNull();
    expect(result.current.isLoading).toBe(false);
    expect(result.current.messages.at(-1)?.isCancelled).toBe(true);
    expect(result.current.messages.at(-1)?.processingTrace).toMatchObject({ outcome: "cancelled", steps: [{ phase: "connecting" }, { phase: "retrieving" }] });
  });
});

describe("useConversation streamed replies", () => {
  it("updates transient guidance snapshots before completion and only persists final validated metadata", async () => {
    const pending = deferredResponse();
    const { result } = renderHook(() => useConversation("guidance-session"));
    await act(async () => { await Promise.resolve(); });
    let request!: Promise<void | boolean>;
    await act(async () => { request = result.current.sendMessage("我需要幫忙"); await Promise.resolve(); });
    act(() => pending.delta("我"));
    expect(result.current.messages.at(-1)?.content).toBe("我");
    const first: ChatGuidance = { interaction_mode: "clarify", clarifying_questions: ["事情"] };
    act(() => pending.guidance(first));
    expect(result.current.messages.at(-1)?.streamingGuidance).toEqual(first);
    const next: ChatGuidance = { ...first, clarifying_questions: ["事情發生在哪裡？"], suggested_replies: ["在學"] };
    act(() => pending.guidance(next));
    act(() => pending.delta("會陪您"));
    expect(result.current.messages.at(-1)).toMatchObject({ content: "我會陪您", streamingGuidance: next, isStreaming: true });
    expect(result.current.messages.at(-1)).not.toHaveProperty("suggestedReplies");
    expect(localStorage.getItem("harass_bot_conversations")).not.toContain("streamingGuidance");
    await act(async () => { pending.resolve(successfulResponse); await request; });
    expect(result.current.messages.at(-1)).not.toHaveProperty("streamingGuidance");
    expect(result.current.messages.at(-1)?.suggestedReplies).toEqual(successfulResponse.suggested_replies);
    expect(localStorage.getItem("harass_bot_conversations")).not.toContain("streamingGuidance");
  });

  it("discards guidance and never retries after visible guidance even without reply text", async () => {
    const pending = deferredResponse();
    const { result } = renderHook(() => useConversation("guidance-error-session"));
    await act(async () => { await Promise.resolve(); });
    let request!: Promise<void | boolean>;
    await act(async () => { request = result.current.sendMessage("我需要幫忙"); await Promise.resolve(); });
    act(() => pending.guidance({ interaction_mode: "answer", suggested_replies: ["了解"] }));
    expect(result.current.messages.at(-1)?.streamingGuidance?.suggested_replies).toEqual(["了解"]);
    await act(async () => {
      pending.reject(new ApiError(503, "upstream failed", "請稍後再試", true));
      await request;
    });
    expect(sendChat).toHaveBeenCalledOnce();
    expect(result.current.messages.at(-1)?.isError).toBe(true);
    expect(result.current.messages.at(-1)).not.toHaveProperty("streamingGuidance");
  });

  it("updates one transient bubble, persists only done, and applies metadata afterward", async () => {
    const pending = deferredResponse();
    const { result } = renderHook(() => useConversation("stream-session"));
    await act(async () => { await Promise.resolve(); });
    const storageWrite = vi.spyOn(localStorage, "setItem");
    let request!: Promise<void | boolean>;
    await act(async () => { request = result.current.sendMessage("我需要幫忙"); await Promise.resolve(); });
    storageWrite.mockClear();
    act(() => pending.delta("我會"));
    const bubbleId = result.current.messages.at(-1)?.id;
    expect(result.current.messages.at(-1)).toMatchObject({ content: "我會", isStreaming: true });
    expect(result.current.messages.at(-1)).not.toHaveProperty("suggestedReplies");
    expect(result.current.messages[0]).not.toHaveProperty("emotion");
    expect(result.current.isLoading).toBe(true);
    act(() => pending.delta("陪您"));
    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages.at(-1)).toMatchObject({ id: bubbleId, content: "我會陪您" });
    expect(storageWrite).not.toHaveBeenCalled();
    expect(JSON.parse(localStorage.getItem("harass_bot_conversations")!)[0].messages).toHaveLength(1);
    await act(async () => {
      pending.resolve({ ...successfulResponse, emotion: "擔心", emotion_color: "blue" });
      await request;
    });
    expect(result.current.isLoading).toBe(false);
    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages.at(-1)).toMatchObject({
      id: bubbleId, content: successfulResponse.reply, suggestedReplies: successfulResponse.suggested_replies,
    });
    expect(result.current.messages.at(-1)).not.toHaveProperty("isStreaming");
    expect(result.current.messages[0]).toMatchObject({ emotion: "擔心", emotionColor: "blue" });
    expect(storageWrite).toHaveBeenCalledOnce();
    storageWrite.mockRestore();
  });

  it("never retries after visible text and excludes incomplete content after reload", async () => {
    const pending = deferredResponse();
    const { result, unmount } = renderHook(() => useConversation("interrupted-session"));
    await act(async () => { await Promise.resolve(); });
    let request!: Promise<void | boolean>;
    await act(async () => { request = result.current.sendMessage("第一個問題"); await Promise.resolve(); });
    act(() => pending.delta("只收到一半"));
    await act(async () => {
      pending.reject(new ApiError(503, "upstream failed", "請稍後再試", true));
      await request;
    });
    expect(sendChat).toHaveBeenCalledOnce();
    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages.at(-1)).toMatchObject({
      content: "只收到一半", isError: true,
      interruptionReason: expect.stringContaining("回覆中斷"),
    });
    unmount();
    const restored = renderHook(() => useConversation("interrupted-session"));
    await act(async () => { await Promise.resolve(); });
    expect(restored.result.current.messages.at(-1)?.isError).toBe(true);
    vi.mocked(sendChat).mockResolvedValueOnce(successfulResponse);
    await act(async () => { await restored.result.current.sendMessage("下一個問題"); });
    expect(vi.mocked(sendChat).mock.calls[1][0].history).toEqual([{ role: "user", content: "第一個問題" }]);
  });

  it("keeps partial text when stopped and does not add a duplicate assistant bubble", async () => {
    const pending = deferredResponse();
    const { result } = renderHook(() => useConversation("cancel-stream-session"));
    await act(async () => { await Promise.resolve(); });
    let request!: Promise<void | boolean>;
    await act(async () => { request = result.current.sendMessage("請說明"); await Promise.resolve(); });
    act(() => pending.delta("這是已經收到的文字"));
    act(() => pending.guidance({ interaction_mode: "clarify", clarifying_questions: ["事情發生在"] }));
    const id = result.current.messages.at(-1)?.id;
    await act(async () => {
      result.current.stopCurrentResponse();
      await request;
    });
    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages.at(-1)).toMatchObject({ id, content: "這是已經收到的文字", isCancelled: true });
    expect(result.current.messages.at(-1)).not.toHaveProperty("streamingGuidance");
    expect(result.current.isLoading).toBe(false);
    expect(sendChat).toHaveBeenCalledOnce();
  });

  it("does not resume a retry after the user stops during its delay", async () => {
    vi.mocked(sendChat).mockRejectedValueOnce(new ApiError(502, "retry", "", true));
    const { result } = renderHook(() => useConversation("retry-stop-session"));
    await act(async () => { await Promise.resolve(); });
    let request!: Promise<void | boolean>;
    await act(async () => { request = result.current.sendMessage("第一個問題"); await Promise.resolve(); });
    expect(result.current.retryStatus).toBe("伺服器回傳錯誤，正在重試中");
    await act(async () => { result.current.stopCurrentResponse(); await request; });
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(sendChat).toHaveBeenCalledOnce();
    expect(result.current.messages.at(-1)?.isCancelled).toBe(true);
  });

  it("keeps in-flight text and metadata in their original session when switching", async () => {
    const pending = deferredResponse();
    const { result } = renderHook(() => useConversation("original-session"));
    await act(async () => { await Promise.resolve(); });
    let request!: Promise<void | boolean>;
    await act(async () => { request = result.current.sendMessage("原本的問題"); await Promise.resolve(); });
    act(() => pending.delta("原本的回覆"));
    act(() => pending.guidance({ interaction_mode: "answer", suggested_replies: ["原本"] }));
    let otherId!: string;
    act(() => { otherId = result.current.createNewSession(); });
    expect(result.current.messages).toEqual([]);
    expect(result.current.isLoading).toBe(false);
    expect(result.current.retryStatus).toBeNull();
    act(() => pending.progress({ phase: "validating", elapsed_ms: 800 }));
    expect(result.current.retryStatus).toBeNull();
    act(() => pending.delta("繼續"));
    act(() => pending.guidance({ interaction_mode: "answer", suggested_replies: ["原本的建議"] }));
    expect(result.current.messages).toEqual([]);
    await act(async () => { pending.resolve(successfulResponse); await request; });
    expect(result.current.currentSessionId).toBe(otherId);
    expect(result.current.messages).toEqual([]);
    act(() => result.current.setCurrentSessionId("original-session"));
    expect(result.current.messages.at(-1)?.content).toBe(successfulResponse.reply);
    expect(result.current.messages.at(-1)).not.toHaveProperty("streamingGuidance");
    expect(result.current.isLoading).toBe(false);
  });

  it("does not restore a cleared turn when its stream is interrupted", async () => {
    const pending = deferredResponse();
    const { result } = renderHook(() => useConversation("clear-stream-session"));
    await act(async () => { await Promise.resolve(); });
    let request!: Promise<void | boolean>;
    await act(async () => { request = result.current.sendMessage("請清除我"); await Promise.resolve(); });
    act(() => pending.delta("部分內容"));
    await act(async () => { result.current.clearCurrentSession(); await request; });
    expect(result.current.messages).toEqual([]);
    expect(result.current.isLoading).toBe(false);
  });
});


describe("chat analytics integration", () => {
  it("records streamed text and completion once without sending content or session IDs", async () => {
    const pending = deferredResponse();
    const { result } = renderHook(() => useConversation("private-session-id"));
    await act(async () => { await Promise.resolve(); });
    let request!: Promise<void | boolean>;
    await act(async () => { request = result.current.sendMessage("private-user-text"); await Promise.resolve(); });
    act(() => pending.delta("private-reply"));
    act(() => pending.delta("private-reply-part-two"));
    await act(async () => { pending.resolve(successfulResponse); await request; });
    const events = vi.mocked(trackAnalytics).mock.calls;
    expect(events.filter(([event]) => event === "chat_request_started")).toHaveLength(1);
    expect(events.filter(([event]) => event === "chat_first_token")).toHaveLength(1);
    expect(events.filter(([event]) => event === "chat_request_finished")).toHaveLength(1);
    expect(trackAnalytics).toHaveBeenCalledWith("chat_request_finished", expect.objectContaining({ outcome: "success", streamed: true }));
    expect(JSON.stringify(events)).not.toContain("private-");
    expect(JSON.stringify(events)).not.toContain(successfulResponse.reply);
  });
});
