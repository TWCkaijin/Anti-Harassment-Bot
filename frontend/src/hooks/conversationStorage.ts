import type { ConversationMessage, ConversationSession } from "./useConversation";
import { isClarification, isRecord, normalizeCaseContext } from "../services/caseFacts";
import { sanitizeProcessingTrace } from "../services/processingTrace";

export const STORAGE_KEY = "harass_bot_conversations";
export type StorageIssue = "unavailable" | "invalid" | "future" | null;

export function loadConversationStorage(): { sessions: ConversationSession[]; issue: StorageIssue; writable: boolean } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { sessions: [], issue: null, writable: true };
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return { sessions: [], issue: "invalid", writable: false };
    if (parsed.some(item => isRecord(item) && item.schemaVersion !== undefined && item.schemaVersion !== 2)) {
      return { sessions: [], issue: "future", writable: false };
    }
    let invalid = false;
    const sessions: ConversationSession[] = [];
    for (const item of parsed.slice(-10)) {
      if (!isRecord(item) || typeof item.id !== "string" || !Array.isArray(item.messages) || typeof item.createdAt !== "number") { invalid = true; continue; }
      const messages: ConversationMessage[] = [];
      for (const message of item.messages.slice(-100)) {
        if (!isRecord(message) || message.isStreaming) continue;
        if (typeof message.id !== "string" || !["user", "assistant"].includes(String(message.role)) || typeof message.content !== "string" || typeof message.timestamp !== "number") { invalid = true; continue; }
        const safeMessage = { ...message } as unknown as ConversationMessage;
        delete safeMessage.streamingGuidance;
        const trace = safeMessage.role === "assistant" ? sanitizeProcessingTrace(safeMessage.processingTrace) : undefined;
        if (trace) safeMessage.processingTrace = trace;
        else delete safeMessage.processingTrace;
        if (!isClarification(safeMessage.clarification)) delete safeMessage.clarification;
        if (!Array.isArray(safeMessage.answerSections) || !safeMessage.answerSections.every(section => isRecord(section) && ["direction", "basis", "next_steps"].includes(String(section.kind)) && typeof section.text === "string" && Array.isArray(section.source_ids) && section.source_ids.every(id => typeof id === "string"))) delete safeMessage.answerSections;
        if (!isRecord(safeMessage.ragUsed) || !Array.isArray(safeMessage.ragUsed.sources) || !safeMessage.ragUsed.sources.every(source => typeof source === "string" || (isRecord(source) && typeof source.label === "string"))) delete safeMessage.ragUsed;
        messages.push(safeMessage);
      }
      const caseFacts = normalizeCaseContext(item.caseFacts);
      if (messages.length || Object.keys(caseFacts.facts).length) sessions.push({ id: item.id, createdAt: item.createdAt, messages, schemaVersion: 2, caseFacts, ...(typeof item.title === "string" ? { title: item.title } : {}) });
    }
    // Keep readable records in memory, but never silently overwrite a damaged file.
    return { sessions, issue: invalid ? "invalid" : null, writable: !invalid };
  } catch { return { sessions: [], issue: "unavailable", writable: false }; }
}

export function saveConversationStorage(sessions: ConversationSession[]): boolean {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions.map(session => ({ ...session, schemaVersion: 2, caseFacts: normalizeCaseContext(session.caseFacts), messages: session.messages.map(message => {
      const { processingTrace, ...rest } = message;
      const trace = message.role === "assistant" ? sanitizeProcessingTrace(processingTrace) : undefined;
      return { ...rest, ...(trace ? { processingTrace: trace } : {}) };
    }) }))));
    return true;
  } catch { return false; }
}
