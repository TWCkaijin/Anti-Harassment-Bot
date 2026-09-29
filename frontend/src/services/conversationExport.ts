import type { ConversationSession } from "../hooks/useConversation";
import { FACT_KEYS, normalizeCaseContext, type FactKey } from "./caseFacts";
interface Labels { caseSummary: string; caseSummaryEmpty: string; caseFactUnknown: string; caseFactDeclined: string; caseFactLabels: Record<FactKey, string> }
/** Shared by individual and all-conversation text exports. Proposed facts stay unconfirmed. */
export function formatConversationText(session: ConversationSession, labels: Labels): string {
  const context = normalizeCaseContext(session.caseFacts);
  const rows = FACT_KEYS.filter(key => context.facts[key]).map(key => {
    const fact = context.facts[key]!;
    const value = fact.status === "provided" ? fact.value : fact.status === "unknown" ? labels.caseFactUnknown : labels.caseFactDeclined;
    return `${labels.caseFactLabels[key]} [${fact.status}]: ${value}`;
  });
  const summary = `${labels.caseSummary} (schema_version: ${context.schema_version}, revision: ${context.revision})\n${rows.length ? rows.join("\n") : labels.caseSummaryEmpty}`;
  const messages = session.messages.map(message => `[${message.role === "user" ? "User" : "AI"}]: ${message.content}`).join("\n\n");
  return `${summary}\n\n${messages}`.trimEnd();
}
