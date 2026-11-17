import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useConversation } from "./useConversation";
import { ApiError, checkHealth, sendChat, type ChatResponse } from "../services/api";
import { reviewedRequestBody } from "../services/clientPrivacy";
import { emptySummaryContext } from "../services/caseFacts";
import { STORAGE_KEY } from "./conversationStorage";
import { trackAnalytics } from "../services/analytics";
import { DEFAULT_PIPELINE } from "../services/pipeline";

vi.mock("../services/api", async () => ({ ...await vi.importActual<typeof import("../services/api")>("../services/api"), checkHealth: vi.fn(), sendChat: vi.fn() }));
vi.mock("../services/analytics", () => ({ hasAnalyticsConsent: () => true, trackAnalytics: vi.fn() }));
const health = { status: "ok", timestamp: "test", version: "test", environment: "test", capabilities: { chat_contract_versions: [1, 2, 3, 4] } };
const response = (revision = 0, extra: Partial<ChatResponse> = {}): ChatResponse => ({ reply: "合成回覆", session_id: "test", anonymized: true, rag_used: { status: false, sources: [] }, suggested_replies: [], action_buttons: [], interaction_mode: "answer", clarifying_questions: [], contract_version: 4, context_revision: revision, summary_update: null, clarification: null, analysis: [], execution: { route: "direct_retrieval", model_calls: 1 }, ...extra });
beforeEach(() => { localStorage.clear(); vi.mocked(checkHealth).mockReset().mockResolvedValue(health); vi.mocked(sendChat).mockReset().mockResolvedValue(response()); vi.mocked(trackAnalytics).mockClear(); });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe("manual conversation privacy review", () => {
  it.each([undefined, true, false])("requires review for the first and subsequent messages regardless of legacy setting: %s", async enabled => {
    const settings = { contract_version: 4 as const, enable_image_upload: true, enable_client_privacy_review: enabled, pipeline: DEFAULT_PIPELINE };
    vi.mocked(checkHealth).mockResolvedValue({ ...health, client_settings: settings });
    const { result } = renderHook(() => useConversation("automatic"));
    await act(async () => { await Promise.resolve(); });
    for (const text of ["first@example.test", "second@example.test"]) {
      let pending!: ReturnType<typeof result.current.sendMessage>;
      act(() => { pending = result.current.sendMessage(text); });
      expect(sendChat).not.toHaveBeenCalledTimes(text === "first@example.test" ? 1 : 2);
      expect(result.current.privacyReview?.request.message).toBe("[電子郵件]");
      await act(async () => { result.current.confirmPrivacyReview(result.current.privacyReview!); expect(await pending).not.toBe(false); });
    }
    expect(sendChat).toHaveBeenCalledTimes(2);
    for (const [request] of vi.mocked(sendChat).mock.calls) expect(reviewedRequestBody(request)).not.toMatch(/first@|second@/);
  });

  it("refuses image data even if legacy settings claim uploads are enabled", async () => {
    const image = "data:image/png;base64,iVBORw0KGgo=";
    const settings = { contract_version: 4 as const, enable_image_upload: true, enable_client_privacy_review: true, pipeline: DEFAULT_PIPELINE };
    vi.mocked(checkHealth).mockResolvedValue({ ...health, client_settings: settings });
    const { result } = renderHook(() => useConversation("image-review"));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { expect(await result.current.sendMessage("請說明圖片", image, image)).toBe(false); });
    expect(sendChat).not.toHaveBeenCalled();
    expect(result.current.privacyReview).toBeNull();
    expect(result.current.error).toContain("圖片上傳目前已停用");
  });

  it("stores no new raw messages, titles or metrics before preparation, and cancellation leaves the conversation unchanged", async () => {
    const { result } = renderHook(() => useConversation("private"));
    await act(async () => { await Promise.resolve(); });
    const stored = localStorage.getItem(STORAGE_KEY);
    let pending!: ReturnType<typeof result.current.sendMessage>;
    act(() => { pending = result.current.sendMessage("合成信箱 alice@example.com"); });
    expect(result.current.messages).toEqual([]);
    expect(result.current.isLoading).toBe(true);
    expect(localStorage.getItem(STORAGE_KEY)).toBe(stored);
    expect(sendChat).not.toHaveBeenCalled();
    expect(trackAnalytics).not.toHaveBeenCalled();
    await act(async () => { result.current.stopCurrentResponse(); expect(await pending).toBe(false); });
    expect(result.current.isLoading).toBe(false);
    expect(result.current.messages).toEqual([]);
    expect(localStorage.getItem(STORAGE_KEY)).toBe(stored);
    expect(sendChat).not.toHaveBeenCalled();
  });

  it("sends the exact approved full snapshot and persists only approved new content and summary", async () => {
    const old = { id: "old-u", role: "user", content: "舊信箱 history@example.com", timestamp: 1 };
    localStorage.setItem(STORAGE_KEY, JSON.stringify([{ id: "history", createdAt: 1, schemaVersion: 4, caseFacts: { ...emptySummaryContext(), summary: "合成甲公司 summary@example.com" }, messages: [old] }]));
    const { result } = renderHook(() => useConversation("history"));
    await act(async () => { await Promise.resolve(); });
    let pending!: ReturnType<typeof result.current.sendMessage>;
    act(() => { pending = result.current.sendMessage("合成甲公司 new@example.com", undefined, undefined, { answers: [{ question: "原始題目", answer: "不应额外存入 extra@example.com" }] }); });
    expect(result.current.privacyReview).not.toBeNull();
    await act(async () => { result.current.confirmPrivacyReview(result.current.privacyReview!); await pending; });
    const sent = vi.mocked(sendChat).mock.calls[0][0];
    expect(Object.isFrozen(sent)).toBe(true);
    expect(reviewedRequestBody(sent)).not.toMatch(/new@example\.com|summary@example\.com|history@example\.com/);
    expect(sent.history[0].content).toBe("舊信箱 [電子郵件]");
    expect(result.current.messages[0].content).toBe(old.content);
    expect(result.current.messages[1]).toMatchObject({ role: "user", content: "合成甲公司 [電子郵件]" });
    expect(result.current.messages[1]).not.toHaveProperty("replyContext");
    expect(result.current.caseFacts).toMatchObject({ summary: "合成甲公司 [電子郵件]" });
    expect(localStorage.getItem(STORAGE_KEY)).toContain("history@example.com");
    expect(localStorage.getItem(STORAGE_KEY)).not.toMatch(/new@example\.com|summary@example\.com|extra@example\.com/);
  });

  it.each(["switch", "delete", "clear", "clear-all", "summary", "new", "unmount"] as const)("invalidates a pending preparation when its owning conversation changes: %s", async change => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([{ id: "owner", createdAt: 1, schemaVersion: 4, caseFacts: emptySummaryContext(), messages: [] }, { id: "other", createdAt: 2, messages: [{ id: "other-u", role: "user", content: "別的問題", timestamp: 2 }] }]));
    const { result, unmount } = renderHook(() => useConversation("owner"));
    await act(async () => { await Promise.resolve(); });
    let pending!: ReturnType<typeof result.current.sendMessage>;
    act(() => { pending = result.current.sendMessage("尚未確認的問題"); });
    await act(async () => {
      if (change === "switch") result.current.setCurrentSessionId("other");
      else if (change === "delete") result.current.deleteSession("owner");
      else if (change === "clear") result.current.clearCurrentSession();
      else if (change === "clear-all") result.current.clearAllSessions();
      else if (change === "summary") await result.current.saveCaseFacts({ ...emptySummaryContext(), summary: "手動更正" });
      else if (change === "new") result.current.createNewSession();
      else unmount();
      expect(await pending).toBe(false);
    });
    expect(sendChat).not.toHaveBeenCalled();
    expect(localStorage.getItem(STORAGE_KEY) ?? "").not.toContain("尚未確認的問題");
  });

  it("does not send a snapshot after negotiation changes during review", async () => {
    const { result } = renderHook(() => useConversation("connection"));
    await act(async () => { await Promise.resolve(); });
    let pending!: ReturnType<typeof result.current.sendMessage>;
    act(() => { pending = result.current.sendMessage("待確認問題"); });
    expect(result.current.privacyReview).not.toBeNull();
    vi.mocked(checkHealth).mockResolvedValue({ ...health, capabilities: { chat_contract_versions: [1, 2, 3] } });
    await act(async () => { await result.current.reconnectBackend(); });
    await act(async () => { result.current.confirmPrivacyReview(result.current.privacyReview!); expect(await pending).toBe(false); });
    expect(sendChat).not.toHaveBeenCalled();
    expect(result.current.messages).toEqual([]);
    expect(result.current.error).toContain("服務連線已變更");
  });

  it("reuses the exact manually approved object for automatic retries", async () => {
    vi.useFakeTimers();
    vi.mocked(sendChat).mockRejectedValueOnce(new ApiError(503, "合成暫時錯誤", undefined, true)).mockResolvedValueOnce(response());
    const { result } = renderHook(() => useConversation("retry"));
    await act(async () => { await Promise.resolve(); });
    let pending!: ReturnType<typeof result.current.sendMessage>;
    act(() => { pending = result.current.sendMessage("test@example.com"); });
    act(() => { result.current.confirmPrivacyReview(result.current.privacyReview!); });
    await act(async () => { await vi.runAllTimersAsync(); await pending; });
    expect(sendChat).toHaveBeenCalledTimes(2);
    const first = vi.mocked(sendChat).mock.calls[0][0], second = vi.mocked(sendChat).mock.calls[1][0];
    expect(second).toBe(first);
    expect(reviewedRequestBody(second)).not.toContain("test@example.com");
  });

  it("keeps new clarification draft text in memory without adding it to saved records", async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([{ id: "draft", createdAt: 1, schemaVersion: 4, caseFacts: emptySummaryContext(), messages: [{ id: "question", role: "assistant", content: "合成問題", timestamp: 1 }] }]));
    const { result } = renderHook(() => useConversation("draft"));
    await act(async () => { await Promise.resolve(); });
    act(() => { result.current.saveClarificationDraft("question", { selectedValues: [], other: "draft@example.com", includeOther: true, special: null }); });
    expect(result.current.messages[0].clarificationDraft?.other).toBe("draft@example.com");
    expect(localStorage.getItem(STORAGE_KEY)).not.toContain("draft@example.com");
  });

  it("drops provisional text and guidance on retraction and saves only the replacement refusal", async () => {
    let delta!: NonNullable<Parameters<typeof sendChat>[2]>;
    let guidance!: NonNullable<Parameters<typeof sendChat>[3]>;
    let analysis!: NonNullable<Parameters<typeof sendChat>[5]>;
    let retract!: NonNullable<Parameters<typeof sendChat>[7]>;
    let finish!: (value: ChatResponse) => void;
    vi.mocked(sendChat).mockImplementation((_request, _signal, onDelta, onGuidance, _progress, onAnalysis, _reasoning, onRetract) => {
      delta = onDelta!; guidance = onGuidance!; analysis = onAnalysis!; retract = onRetract!;
      return new Promise<ChatResponse>(resolve => { finish = resolve; });
    });
    const { result } = renderHook(() => useConversation("retract"));
    await act(async () => { await Promise.resolve(); });
    let pending!: ReturnType<typeof result.current.sendMessage>;
    act(() => { pending = result.current.sendMessage("合成問題"); });
    await act(async () => { result.current.confirmPrivacyReview(result.current.privacyReview!); await Promise.resolve(); });
    act(() => {
      delta("PROVISIONAL_CANARY");
      guidance({ interaction_mode: "clarify", clarifying_questions: ["PROVISIONAL_QUESTION"], suggested_replies: [] });
      analysis({ stage: "understanding", summary: "PROVISIONAL_ANALYSIS", facts: [], source_labels: [], limitations: [] });
    });
    expect(JSON.stringify(result.current.messages)).toContain("PROVISIONAL_CANARY");
    act(() => { retract({ turn_id: "12345678-1234-4234-8234-123456789abc", code: "gate_denied" }); });
    expect(JSON.stringify(result.current.messages)).not.toMatch(/PROVISIONAL_CANARY|PROVISIONAL_QUESTION|PROVISIONAL_ANALYSIS/);
    await act(async () => { finish(response(0, { reply: "簡短拒答", summary_update: { base_revision: 0, summary: "SHOULD_NOT_APPLY", evidence: [] } })); await pending; });
    expect(result.current.messages.at(-1)).toMatchObject({ role: "assistant", content: "簡短拒答" });
    expect(result.current.caseFacts).toMatchObject({ summary: "" });
    expect(localStorage.getItem(STORAGE_KEY)).not.toMatch(/PROVISIONAL_CANARY|PROVISIONAL_QUESTION|PROVISIONAL_ANALYSIS|SHOULD_NOT_APPLY/);
  });

  it.each(["stop", "review_error"] as const)("removes provisional content after %s", async outcome => {
    let delta!: NonNullable<Parameters<typeof sendChat>[2]>;
    let retract!: NonNullable<Parameters<typeof sendChat>[7]>;
    let fail!: (error: Error) => void;
    vi.mocked(sendChat).mockImplementation((_request, _signal, onDelta, _guidance, _progress, _analysis, _reasoning, onRetract) => {
      delta = onDelta!; retract = onRetract!;
      return new Promise<ChatResponse>((_resolve, reject) => { fail = reject; });
    });
    const { result } = renderHook(() => useConversation(`interrupted-${outcome}`));
    await act(async () => { await Promise.resolve(); });
    let pending!: ReturnType<typeof result.current.sendMessage>;
    act(() => { pending = result.current.sendMessage("合成問題"); });
    await act(async () => { result.current.confirmPrivacyReview(result.current.privacyReview!); await Promise.resolve(); });
    act(() => { delta("PROVISIONAL_CANARY"); });
    expect(result.current.messages.at(-1)?.content).toContain("PROVISIONAL_CANARY");
    if (outcome === "stop") act(() => { result.current.stopCurrentResponse(); });
    else act(() => { retract({ turn_id: "12345678-1234-4234-8234-123456789abc", code: "review_unavailable" }); });
    await act(async () => {
      fail(outcome === "stop" ? new DOMException("Aborted", "AbortError") : new ApiError(503, "審查失敗", undefined, true, undefined, "review_unavailable"));
      await pending;
    });
    expect(result.current.messages.at(-1)).toMatchObject(outcome === "stop" ? { isCancelled: true } : { isError: true });
    expect(JSON.stringify(result.current.messages)).not.toContain("PROVISIONAL_CANARY");
    expect(localStorage.getItem(STORAGE_KEY)).not.toContain("PROVISIONAL_CANARY");
    expect(sendChat).toHaveBeenCalledOnce();
  });

  it("cancels review when another tab changes storage and does not overwrite that tab", async () => {
    const { result } = renderHook(() => useConversation("cross-tab"));
    await act(async () => { await Promise.resolve(); });
    let pending!: ReturnType<typeof result.current.sendMessage>;
    act(() => { pending = result.current.sendMessage("待確認內容"); });
    const previous = localStorage.getItem(STORAGE_KEY);
    const newer = JSON.stringify([{ id: "other-tab", createdAt: 1, messages: [] }]);
    localStorage.setItem(STORAGE_KEY, newer);
    await act(async () => {
      const event = new StorageEvent("storage", { key: STORAGE_KEY, oldValue: previous, newValue: newer });
      Object.defineProperty(event, "storageArea", { value: localStorage });
      window.dispatchEvent(event);
      expect(await pending).toBe(false);
    });
    expect(result.current.privacyReview).toBeNull();
    expect(result.current.storageIssue).toBe("changed");
    expect(sendChat).not.toHaveBeenCalled();
    await act(async () => { expect(await result.current.sendMessage("新問題")).toBe(false); });
    act(() => { result.current.clearAllSessions(); });
    expect(localStorage.getItem(STORAGE_KEY)).toBe(newer);
  });

  it("checks the saved snapshot before writing even when a storage event was missed", async () => {
    const { result } = renderHook(() => useConversation("missed-event"));
    await act(async () => { await Promise.resolve(); });
    const newer = JSON.stringify([{ id: "other-tab", createdAt: 1, messages: [] }]);
    localStorage.setItem(STORAGE_KEY, newer);
    await act(async () => { result.current.renameSession("missed-event", "新標題"); await Promise.resolve(); });
    expect(result.current.storageIssue).toBe("changed");
    expect(localStorage.getItem(STORAGE_KEY)).toBe(newer);
  });
});
