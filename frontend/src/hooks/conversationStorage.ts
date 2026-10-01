import type { ConversationMessage, ConversationSession } from "./useConversation";
import { hasCaseContext, isCaseContext, isClarification, isClarificationDraft, isFactUpdate, isRecord, normalizeCaseContext, toSummaryContext, type CaseContext, type LegacyCaseContext } from "../services/caseFacts";
import { sanitizeProcessingTrace } from "../services/processingTrace";
import { sanitizeAnalysis } from "../services/analysis";

export const STORAGE_KEY = "harass_bot_conversations";
export type StorageIssue = "unavailable" | "invalid" | "future" | "migration" | null;

export function loadConversationStorage(): { sessions: ConversationSession[]; issue: StorageIssue; writable: boolean; migrationSnapshot?: string } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { sessions: [], issue: null, writable: true };
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return { sessions: [], issue: "invalid", writable: false };
    if (parsed.some(item => isRecord(item) && ((item.schemaVersion !== undefined && ![2, 3, 4].includes(Number(item.schemaVersion)))
      || (isRecord(item.caseFacts) && ![1, 2, 3].includes(Number(item.caseFacts.schema_version)))))) {
      return { sessions: [], issue: "future", writable: false };
    }
    let invalid = false;
    let migrationPending = false;
    const sessions: ConversationSession[] = [];
    const seenIds = new Set<string>();
    // Validate every record before deciding recovery is migration-only. Keep all
    // readable data during recovery so normal retention cannot hide a pending edit.
    for (const item of parsed) {
      if (!isRecord(item) || typeof item.id !== "string" || !Array.isArray(item.messages) || typeof item.createdAt !== "number") { invalid = true; continue; }
      if (seenIds.has(item.id) || (item.schemaVersion !== undefined && !Number.isSafeInteger(item.schemaVersion))) invalid = true;
      seenIds.add(item.id);
      const messages: ConversationMessage[] = [];
      for (const message of item.messages) {
        if (!isRecord(message) || message.isStreaming) continue;
        if (typeof message.id !== "string" || !["user", "assistant"].includes(String(message.role)) || typeof message.content !== "string" || typeof message.timestamp !== "number") { invalid = true; continue; }
        const safeMessage = { ...message } as unknown as ConversationMessage;
        delete safeMessage.streamingGuidance;
        delete safeMessage.reasoning;
        const trace = safeMessage.role === "assistant" ? sanitizeProcessingTrace(safeMessage.processingTrace) : undefined;
        if (trace) safeMessage.processingTrace = trace;
        else delete safeMessage.processingTrace;
        if (!isClarification(safeMessage.clarification, safeMessage.contractVersion)) delete safeMessage.clarification;
        if (!isClarificationDraft(safeMessage.clarificationDraft)) delete safeMessage.clarificationDraft;
        const analysis = sanitizeAnalysis(safeMessage.analysis);
        if (analysis) safeMessage.analysis = analysis;
        else delete safeMessage.analysis;
        if (!Array.isArray(safeMessage.answerSections) || !safeMessage.answerSections.every(section => isRecord(section) && ["direction", "basis", "next_steps"].includes(String(section.kind)) && typeof section.text === "string" && Array.isArray(section.source_ids) && section.source_ids.every(id => typeof id === "string"))) delete safeMessage.answerSections;
        if (!isRecord(safeMessage.ragUsed) || !Array.isArray(safeMessage.ragUsed.sources) || !safeMessage.ragUsed.sources.every(source => typeof source === "string" || (isRecord(source) && typeof source.label === "string"))) delete safeMessage.ragUsed;
        messages.push(safeMessage);
      }
      if (item.caseFacts !== undefined && !isCaseContext(item.caseFacts)) invalid = true;
      const originalContext = normalizeCaseContext(item.caseFacts);
      let caseFacts: CaseContext = originalContext;
      try { caseFacts = toSummaryContext(originalContext); } catch (failure) {
        if (failure instanceof RangeError && isCaseContext(item.caseFacts)) migrationPending = true;
        else invalid = true;
      }
      let legacyCaseFacts: LegacyCaseContext | undefined;
      if (item.legacyCaseFacts !== undefined) {
        if (isCaseContext(item.legacyCaseFacts) && item.legacyCaseFacts.schema_version !== 3) legacyCaseFacts = item.legacyCaseFacts;
        else invalid = true;
      } else if (isCaseContext(item.caseFacts) && item.caseFacts.schema_version !== 3) legacyCaseFacts = item.caseFacts;
      const pendingFacts = Array.isArray(item.pendingFacts) && item.pendingFacts.every(isFactUpdate) ? item.pendingFacts : [];
      if (item.pendingFacts !== undefined && (!Array.isArray(item.pendingFacts) || !item.pendingFacts.every(isFactUpdate))) invalid = true;
      const hasEditBoundary = typeof item.summaryEditedAfterMessageId === "string" && item.summaryEditedAfterMessageId.length <= 200;
      if (item.summaryEditedAfterMessageId !== undefined && !hasEditBoundary) invalid = true;
      const hasEditTime = typeof item.summaryEditedAt === "number" && Number.isFinite(item.summaryEditedAt) && item.summaryEditedAt >= 0;
      if (item.summaryEditedAt !== undefined && !hasEditTime) invalid = true;
      if (messages.length || hasCaseContext(caseFacts)) sessions.push({ id: item.id, createdAt: item.createdAt, messages, schemaVersion: 4, caseFacts,
        ...(legacyCaseFacts ? { legacyCaseFacts } : {}),
        ...(hasEditBoundary ? { summaryEditedAfterMessageId: item.summaryEditedAfterMessageId as string } : {}),
        ...(hasEditTime ? { summaryEditedAt: item.summaryEditedAt as number } : {}),
        ...(pendingFacts.length ? { pendingFacts } : {}), ...(typeof item.title === "string" ? { title: item.title } : {}) });
    }
    // Keep readable records in memory, but never silently overwrite a damaged file.
    const issue = invalid ? "invalid" : migrationPending ? "migration" : null;
    return { sessions: issue ? sessions : sessions.slice(-10).map(session => ({ ...session, messages: session.messages.slice(-100) })), issue, writable: issue === null,
      ...(issue === "migration" ? { migrationSnapshot: raw } : {}) };
  } catch { return { sessions: [], issue: "unavailable", writable: false }; }
}

function serializeConversationStorage(sessions: ConversationSession[]): string {
    if (sessions.some(session => (session.caseFacts !== undefined && !isCaseContext(session.caseFacts))
      || (session.legacyCaseFacts !== undefined && (!isCaseContext(session.legacyCaseFacts) || ![1, 2].includes(session.legacyCaseFacts.schema_version))))) throw new TypeError("Invalid conversation context");
    return JSON.stringify(sessions.map(session => {
      const context = normalizeCaseContext(session.caseFacts);
      const legacyCaseFacts = session.legacyCaseFacts ?? (session.caseFacts !== undefined && context.schema_version !== 3 ? context : undefined);
      return { ...session, schemaVersion: 4, caseFacts: toSummaryContext(context), ...(legacyCaseFacts ? { legacyCaseFacts } : {}), messages: session.messages.map(message => {
      const safeMessage = { ...message };
      delete safeMessage.reasoning;
      const { processingTrace, analysis, clarificationDraft, ...rest } = safeMessage;
      const trace = message.role === "assistant" ? sanitizeProcessingTrace(processingTrace) : undefined;
      const safeAnalysis = message.role === "assistant" ? sanitizeAnalysis(analysis) : undefined;
      return { ...rest, ...(trace ? { processingTrace: trace } : {}), ...(safeAnalysis ? { analysis: safeAnalysis } : {}), ...(isClarificationDraft(clarificationDraft) ? { clarificationDraft } : {}) };
    }) };
    }));
}

export function saveConversationStorage(sessions: ConversationSession[]): boolean {
  try {
    localStorage.setItem(STORAGE_KEY, serializeConversationStorage(sessions));
    return true;
  } catch { return false; }
}

/** Called only after an explicit edit to a migration-only snapshot. One atomic write or no write. */
export function recoverConversationMigration(sessions: ConversationSession[], expectedRaw: string): "saved" | "pending" | "invalid" | "unavailable" {
  try { if (localStorage.getItem(STORAGE_KEY) !== expectedRaw) return "invalid"; }
  catch { return "unavailable"; }
  let serialized: string;
  try { serialized = serializeConversationStorage(sessions); }
  catch (failure) { return failure instanceof RangeError ? "pending" : "invalid"; }
  try {
    if (localStorage.getItem(STORAGE_KEY) !== expectedRaw) return "invalid";
    localStorage.setItem(STORAGE_KEY, serialized);
    return "saved";
  } catch { return "unavailable"; }
}
