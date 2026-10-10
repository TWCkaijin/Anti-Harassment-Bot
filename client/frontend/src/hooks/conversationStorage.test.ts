import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadConversationStorage, recoverConversationMigration, saveConversationStorage, STORAGE_KEY } from "./conversationStorage";
import { emptySummaryContext, toSummaryContext, toSummaryText, type LegacyCaseContext } from "../services/caseFacts";
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
    expect(loadConversationStorage()).toMatchObject({ writable: true, sessions: [{ ...session, schemaVersion: 4, caseFacts: { schema_version: 3, revision: 0, facts: {}, summary: "", summary_origin: "migration" } }] });
  });
  it("preserves older raw data if a newer schema or damaged record exists", () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([{ ...session, schemaVersion: 5 }]));
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
  it("migrates confirmed scalar facts to readable text and preserves a legacy backup", () => {
    const caseFacts = { schema_version: 1, revision: 4, facts: { subject_role: { status: "provided", value: "學生" }, city: { status: "declined" } } };
    localStorage.setItem(STORAGE_KEY, JSON.stringify([{ ...session, schemaVersion: 2, caseFacts }]));
    expect(loadConversationStorage()).toMatchObject({ writable: true, issue: null, sessions: [{ schemaVersion: 4, caseFacts: toSummaryContext(caseFacts as LegacyCaseContext), legacyCaseFacts: caseFacts }] });
  });
  it("round-trips arrays, scenario questions, unsent drafts and public analysis", () => {
    const caseFacts = { schema_version: 2 as const, revision: 4, facts: { subject_role: { status: "provided" as const, value: ["學生", "兼職員工"] } } };
    const clarification = { question_id: "scenario.other_role.4", fact_key: "other_role" as const, reason: "釐清情境", question: "朋友的同事有哪些角色？", options: [{ label: "主管", value: "主管" }], selection_mode: "multiple" as const, max_selections: 2, validation_token: "signed-token", context_scope: "scenario" as const };
    const clarificationDraft = { selectedValues: ["主管"], other: "客戶", includeOther: true, special: null };
    const analysis = [{ stage: "understanding" as const, summary: "已整理提供的身分", facts: ["學生、兼職員工"], source_labels: [], limitations: [] }];
    const record = { ...session, schemaVersion: 3 as const, caseFacts, messages: [{ ...session.messages[0], role: "assistant" as const, clarification, clarificationDraft, analysis }] };
    expect(saveConversationStorage([record])).toBe(true);
    expect(loadConversationStorage()).toMatchObject({ writable: true, issue: null, sessions: [{ ...record, schemaVersion: 4, caseFacts: toSummaryContext(caseFacts), legacyCaseFacts: caseFacts }] });
  });
  it("leaves raw records intact and blocks rewriting malformed or future nested facts", () => {
    for (const caseFacts of [
      { schema_version: 2, revision: 0, facts: { behavior: { status: "provided", value: ["言語", "言語"] } } },
      { schema_version: 3, revision: 0, facts: {} },
    ]) {
      const raw = JSON.stringify([{ ...session, schemaVersion: 3, caseFacts }]);
      localStorage.setItem(STORAGE_KEY, raw);
      expect(loadConversationStorage().writable).toBe(false);
      expect(localStorage.getItem(STORAGE_KEY)).toBe(raw);
    }
  });
  it("preserves manual edit boundaries while stripping any saved reasoning on both load and save", () => {
    const record = { ...session, schemaVersion: 4 as const, summaryEditedAfterMessageId: "m", summaryEditedAt: 2, caseFacts: { schema_version: 3 as const, revision: 2, facts: {}, summary: "使用者修正後的摘要。", summary_origin: "user" as const }, messages: [{ ...session.messages[0], role: "assistant" as const, reasoning: [{ text: "EPHEMERAL_PROVIDER_TEXT", kind: "summary" as const, stage: "answer" as const }] }] };
    localStorage.setItem(STORAGE_KEY, JSON.stringify([record]));
    const loaded = loadConversationStorage();
    expect(loaded.sessions[0].messages[0]).not.toHaveProperty("reasoning");
    expect(loaded.sessions[0]).toMatchObject({ caseFacts: record.caseFacts, summaryEditedAfterMessageId: "m", summaryEditedAt: 2 });
    expect(saveConversationStorage([record])).toBe(true);
    expect(localStorage.getItem(STORAGE_KEY)).not.toContain("EPHEMERAL_PROVIDER_TEXT");
  });
  it("keeps overlong legacy migrations read-only with their original arrays intact", () => {
    const fact = { status: "provided", value: ["a".repeat(300), "b".repeat(300), "c".repeat(300), "d".repeat(300)] };
    const caseFacts = { schema_version: 2, revision: 1, facts: { subject_role: fact, other_role: fact, relationship: fact, behavior: fact } };
    const raw = JSON.stringify([{ ...session, schemaVersion: 3, caseFacts }]);
    localStorage.setItem(STORAGE_KEY, raw);
    expect(loadConversationStorage()).toMatchObject({ writable: false, issue: "migration", migrationSnapshot: raw, sessions: [{ caseFacts, legacyCaseFacts: caseFacts }] });
    expect(localStorage.getItem(STORAGE_KEY)).toBe(raw);
  });
  it("recovers a shortened summary atomically with its full legacy backup", () => {
    const fact = { status: "provided" as const, value: ["a".repeat(300), "b".repeat(300), "c".repeat(300), "d".repeat(300)] };
    const caseFacts: LegacyCaseContext = { schema_version: 2, revision: 1, facts: { subject_role: fact, other_role: fact, relationship: fact, behavior: fact } };
    expect(toSummaryText(caseFacts).length).toBeGreaterThan(4000);
    expect(toSummaryText(caseFacts)).toContain("d".repeat(300));
    expect(() => toSummaryContext(caseFacts)).toThrow(RangeError);
    const raw = JSON.stringify([{ ...session, schemaVersion: 3, caseFacts }]);
    localStorage.setItem(STORAGE_KEY, raw);
    const loaded = loadConversationStorage();
    const write = vi.spyOn(localStorage, "setItem");
    expect(recoverConversationMigration(loaded.sessions, loaded.migrationSnapshot!)).toBe("pending");
    expect(write).not.toHaveBeenCalled();
    const fixed = [{ ...loaded.sessions[0], caseFacts: { ...emptySummaryContext(), revision: 2, summary: "使用者縮短後的完整意思。", summary_origin: "user" as const } }];
    expect(recoverConversationMigration(fixed, loaded.migrationSnapshot!)).toBe("saved");
    expect(write).toHaveBeenCalledOnce();
    expect(loadConversationStorage()).toMatchObject({ writable: true, issue: null, sessions: [{ caseFacts: fixed[0].caseFacts, legacyCaseFacts: caseFacts }] });
  });
  it("never calls a damaged mixed file migration-only, including records beyond the normal retention window", () => {
    const fact = { status: "provided", value: ["a".repeat(300), "b".repeat(300), "c".repeat(300), "d".repeat(300)] };
    const caseFacts = { schema_version: 2, revision: 1, facts: { subject_role: fact, other_role: fact, relationship: fact, behavior: fact } };
    const raw = JSON.stringify([{ id: 123 }, ...Array.from({ length: 11 }, (_, i) => ({ ...session, id: `record-${i}`, schemaVersion: 3, caseFacts }))]);
    localStorage.setItem(STORAGE_KEY, raw);
    const loaded = loadConversationStorage();
    expect(loaded).toMatchObject({ writable: false, issue: "invalid" });
    expect(loaded).not.toHaveProperty("migrationSnapshot");
    expect(loaded.sessions).toHaveLength(11);
    expect(localStorage.getItem(STORAGE_KEY)).toBe(raw);
  });
  it("refuses migration recovery if another tab changed the original snapshot", () => {
    localStorage.setItem(STORAGE_KEY, "newer-data");
    const write = vi.spyOn(localStorage, "setItem");
    expect(recoverConversationMigration([{ ...session, caseFacts: emptySummaryContext() }], "older-data")).toBe("invalid");
    expect(write).not.toHaveBeenCalled();
    expect(localStorage.getItem(STORAGE_KEY)).toBe("newer-data");
  });
});
