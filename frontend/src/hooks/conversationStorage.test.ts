import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadConversationStorage, saveConversationStorage, STORAGE_KEY } from "./conversationStorage";
const session = { id: "old", createdAt: 1, messages: [{ id: "m", role: "user" as const, content: "我是學生", timestamp: 1 }] };
beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());
describe("local conversation migration", () => {
  it("retains only safe completed process metadata on save and restore", () => {
    const processingTrace = { outcome: "complete" as const, duration_ms: 30, reasoning: "SECRET", steps: [{ phase: "retrieving" as const, elapsed_ms: 10, attempt: 0, query: "SECRET" }] };
    saveConversationStorage([{ ...session, messages: [{ ...session.messages[0], role: "assistant", processingTrace }] }]);
    expect(localStorage.getItem(STORAGE_KEY)).not.toContain("SECRET");
    expect(loadConversationStorage().sessions[0].messages[0].processingTrace).toEqual({ outcome: "complete", duration_ms: 30, steps: [{ phase: "retrieving", elapsed_ms: 10, attempt: 0 }] });
    localStorage.setItem(STORAGE_KEY, JSON.stringify([{ ...session, messages: [{ ...session.messages[0], role: "assistant", processingTrace: { ...processingTrace, outcome: "running" } }] }]));
    expect(loadConversationStorage().sessions[0].messages[0]).not.toHaveProperty("processingTrace");
  });
  it("migrates old chats without inferring facts from their text", () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([session]));
    expect(loadConversationStorage()).toMatchObject({ writable: true, sessions: [{ ...session, schemaVersion: 2, caseFacts: { schema_version: 1, revision: 0, facts: {} } }] });
  });
  it("preserves older raw data if a newer schema or damaged record exists", () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([{ ...session, schemaVersion: 3 }]));
    expect(loadConversationStorage()).toMatchObject({ writable: false, issue: "future" });
    localStorage.setItem(STORAGE_KEY, JSON.stringify([session, { id: 123 }]));
    expect(loadConversationStorage()).toMatchObject({ writable: false, issue: "invalid", sessions: [{ id: "old" }] });
  });
  it("does not erase records or throw when storage is full", () => {
    localStorage.setItem(STORAGE_KEY, "original");
    vi.spyOn(localStorage, "setItem").mockImplementation(() => { throw new DOMException("Full", "QuotaExceededError"); });
    expect(saveConversationStorage([session])).toBe(false);
    expect(localStorage.getItem(STORAGE_KEY)).toBe("original");
  });
});
