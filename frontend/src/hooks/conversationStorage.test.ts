import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadConversationStorage, saveConversationStorage, STORAGE_KEY } from "./conversationStorage";
const session = { id: "old", createdAt: 1, messages: [{ id: "m", role: "user" as const, content: "我是學生", timestamp: 1 }] };
beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());
describe("local conversation migration", () => {
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
