import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useConversation } from "./useConversation";
import { ApiError, checkHealth, sendChat, type ChatResponse } from "../services/api";
import { addPrivacyHiddenTerms, cancelPrivacyReview, confirmPrivacyReview, editPrivacyReviewField, getPrivacyReview } from "../services/privacyReview";
import { reviewedRequestBody } from "../services/clientPrivacy";
import { emptySummaryContext } from "../services/caseFacts";
import { STORAGE_KEY } from "./conversationStorage";
import { trackAnalytics } from "../services/analytics";
import { DEFAULT_PIPELINE } from "../services/pipeline";

vi.mock("../services/api", async () => ({ ...await vi.importActual<typeof import("../services/api")>("../services/api"), checkHealth: vi.fn(), sendChat: vi.fn() }));
vi.mock("../services/analytics", () => ({ hasAnalyticsConsent: () => true, trackAnalytics: vi.fn() }));
const health = { status: "ok", timestamp: "test", version: "test", environment: "test", capabilities: { chat_contract_versions: [1, 2, 3, 4] } };
const response = (revision = 0): ChatResponse => ({ reply: "合成回覆", session_id: "test", anonymized: true, rag_used: { status: false, sources: [] }, suggested_replies: [], action_buttons: [], interaction_mode: "answer", clarifying_questions: [], contract_version: 4, context_revision: revision, summary_update: null, clarification: null, analysis: [], execution: { route: "direct_retrieval", model_calls: 1 } });
beforeEach(() => { localStorage.clear(); vi.mocked(checkHealth).mockReset().mockResolvedValue(health); vi.mocked(sendChat).mockReset().mockResolvedValue(response()); vi.mocked(trackAnalytics).mockClear(); });
afterEach(() => { while (getPrivacyReview()) cancelPrivacyReview(getPrivacyReview()!.id); vi.useRealTimers(); vi.restoreAllMocks(); });

describe("conversation privacy review ownership", () => {
  it("uses the current health setting to skip and later restore confirmation without skipping text masking", async () => {
    const settings = { contract_version: 4 as const, enable_image_upload: true, enable_client_privacy_review: false, pipeline: DEFAULT_PIPELINE };
    vi.mocked(checkHealth).mockResolvedValue({ ...health, client_settings: settings });
    const { result } = renderHook(() => useConversation("toggle"));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { expect(await result.current.sendMessage("synthetic@example.test")).not.toBe(false); });
    expect(getPrivacyReview()).toBeNull();
    expect(sendChat).toHaveBeenCalledTimes(1);
    expect(reviewedRequestBody(vi.mocked(sendChat).mock.calls[0][0])).not.toContain("synthetic@example.test");
    vi.mocked(checkHealth).mockResolvedValue({ ...health, client_settings: { ...settings, enable_client_privacy_review: true } });
    await act(async () => { await result.current.reconnectBackend(); });
    let pending!: ReturnType<typeof result.current.sendMessage>;
    act(() => { pending = result.current.sendMessage("新的問題"); });
    expect(getPrivacyReview()).not.toBeNull();
    expect(sendChat).toHaveBeenCalledTimes(1);
    await act(async () => { cancelPrivacyReview(getPrivacyReview()!.id); await pending; });
  });

  it("passes a validated original image through review and refuses new images when admin disables upload", async () => {
    const image = "data:image/png;base64,iVBORw0KGgo=";
    const settings = { contract_version: 4 as const, enable_image_upload: true, enable_client_privacy_review: true, pipeline: DEFAULT_PIPELINE };
    vi.mocked(checkHealth).mockResolvedValue({ ...health, client_settings: settings });
    const { result } = renderHook(() => useConversation("image-review"));
    await act(async () => { await Promise.resolve(); });
    let pending!: ReturnType<typeof result.current.sendMessage>;
    act(() => { pending = result.current.sendMessage("請說明圖片", image, image); });
    expect(getPrivacyReview()?.draft.request.image_base64).toBe(image);
    expect(sendChat).not.toHaveBeenCalled();
    await act(async () => { confirmPrivacyReview(getPrivacyReview()!.id); await pending; });
    expect(vi.mocked(sendChat).mock.calls[0][0].image_base64).toBe(image);
    expect(result.current.messages[0].imageUrl).toBe(image);
    vi.mocked(checkHealth).mockResolvedValue({ ...health, client_settings: { ...settings, enable_image_upload: false } });
    await act(async () => { await result.current.reconnectBackend(); });
    await act(async () => { expect(await result.current.sendMessage("另一張", image, image)).toBe(false); });
    expect(sendChat).toHaveBeenCalledTimes(1);
    expect(result.current.error).toContain("暫停圖片上傳");
  });

  it("stores no new raw messages, titles or metrics before review, and cancellation leaves the conversation unchanged", async () => {
    const { result } = renderHook(() => useConversation("private"));
    await act(async () => { await Promise.resolve(); });
    const stored = localStorage.getItem(STORAGE_KEY);
    let pending!: ReturnType<typeof result.current.sendMessage>;
    act(() => { pending = result.current.sendMessage("合成信箱 alice@example.com"); });
    expect(getPrivacyReview()?.draft.request.message).toBe("合成信箱 [電子郵件]");
    expect(result.current.messages).toEqual([]);
    expect(result.current.isLoading).toBe(true);
    expect(localStorage.getItem(STORAGE_KEY)).toBe(stored);
    expect(sendChat).not.toHaveBeenCalled();
    expect(trackAnalytics).not.toHaveBeenCalled();
    await act(async () => { cancelPrivacyReview(getPrivacyReview()!.id); expect(await pending).toBe(false); });
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
    const id = getPrivacyReview()!.id;
    act(() => { addPrivacyHiddenTerms(id, ["合成甲公司"]); editPrivacyReviewField(id, ["message"], "合成甲公司 new@example.com 已確認問題"); });
    const preview = structuredClone(getPrivacyReview()!.draft.request);
    await act(async () => { confirmPrivacyReview(id); await pending; });
    const sent = vi.mocked(sendChat).mock.calls[0][0];
    expect(JSON.parse(reviewedRequestBody(sent))).toEqual(preview);
    expect(sent.history[0].content).toBe("舊信箱 [電子郵件]");
    expect(result.current.messages[0].content).toBe(old.content);
    expect(result.current.messages[1]).toMatchObject({ role: "user", content: "[已隱藏資訊1] [電子郵件] 已確認問題" });
    expect(result.current.messages[1]).not.toHaveProperty("replyContext");
    expect(result.current.caseFacts).toMatchObject({ summary: "[已隱藏資訊1] [電子郵件]" });
    expect(localStorage.getItem(STORAGE_KEY)).toContain("history@example.com");
    expect(localStorage.getItem(STORAGE_KEY)).not.toMatch(/new@example\.com|summary@example\.com|extra@example\.com|合成甲公司/);
  });

  it.each(["switch", "delete", "clear", "clear-all", "summary", "new", "unmount"] as const)("invalidates a pending review when its owning conversation changes: %s", async change => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([{ id: "owner", createdAt: 1, schemaVersion: 4, caseFacts: emptySummaryContext(), messages: [] }, { id: "other", createdAt: 2, messages: [{ id: "other-u", role: "user", content: "別的問題", timestamp: 2 }] }]));
    const { result, unmount } = renderHook(() => useConversation("owner"));
    await act(async () => { await Promise.resolve(); });
    let pending!: ReturnType<typeof result.current.sendMessage>;
    act(() => { pending = result.current.sendMessage("尚未確認的問題"); });
    const id = getPrivacyReview()!.id;
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
    expect(getPrivacyReview()).toBeNull();
    expect(confirmPrivacyReview(id)).toBe(false);
    expect(sendChat).not.toHaveBeenCalled();
    expect(localStorage.getItem(STORAGE_KEY)).not.toContain("尚未確認的問題");
  });

  it("does not send a confirmed snapshot after negotiation changes while the review was open", async () => {
    const { result } = renderHook(() => useConversation("connection"));
    await act(async () => { await Promise.resolve(); });
    let pending!: ReturnType<typeof result.current.sendMessage>;
    act(() => { pending = result.current.sendMessage("待確認問題"); });
    vi.mocked(checkHealth).mockResolvedValue({ ...health, capabilities: { chat_contract_versions: [1, 2, 3] } });
    await act(async () => { await result.current.reconnectBackend(); });
    await act(async () => { confirmPrivacyReview(getPrivacyReview()!.id); expect(await pending).toBe(false); });
    expect(sendChat).not.toHaveBeenCalled();
    expect(result.current.messages).toEqual([]);
    expect(result.current.error).toContain("服務連線已變更");
  });

  it("reuses the exact approved object for automatic retries without reopening review", async () => {
    vi.useFakeTimers();
    vi.mocked(sendChat).mockRejectedValueOnce(new ApiError(503, "合成暫時錯誤", undefined, true)).mockResolvedValueOnce(response());
    const { result } = renderHook(() => useConversation("retry"));
    await act(async () => { await Promise.resolve(); });
    let pending!: ReturnType<typeof result.current.sendMessage>;
    act(() => { pending = result.current.sendMessage("test@example.com"); });
    await act(async () => { confirmPrivacyReview(getPrivacyReview()!.id); await Promise.resolve(); });
    expect(getPrivacyReview()).toBeNull();
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
});
