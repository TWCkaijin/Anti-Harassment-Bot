export const FACT_KEYS = ["subject_role", "other_role", "relationship", "work_related", "internship_related", "education_related", "behavior", "ongoing", "event_time", "age_group", "city", "desired_help"] as const;
export type FactKey = typeof FACT_KEYS[number];
export type FactStatus = "provided" | "unknown" | "declined";
export interface CaseFact { status: FactStatus; value?: string }
export interface CaseContext { schema_version: 1; revision: number; facts: Partial<Record<FactKey, CaseFact>> }
export interface FactUpdate extends CaseFact { fact_key: FactKey; evidence: string; kind: "explicit" | "confirmation" }
export interface Clarification { question_id: string; fact_key: FactKey; reason: string; question: string; options: Array<{ label: string; value: string }> }
export interface ClarificationAnswer extends CaseFact { question_id: string; fact_key: FactKey }
export const MAX_FACT_VALUE_LENGTH = 300;
export const emptyCaseContext = (): CaseContext => ({ schema_version: 1, revision: 0, facts: {} });
export const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
export const isFactKey = (value: unknown): value is FactKey => typeof value === "string" && (FACT_KEYS as readonly string[]).includes(value);
export const boundedText = (value: unknown, limit = MAX_FACT_VALUE_LENGTH): value is string => typeof value === "string" && value.trim().length > 0 && value.length <= limit;
const onlyKeys = (value: Record<string, unknown>, allowed: readonly string[]) => Object.keys(value).every(key => allowed.includes(key));
export function parseFact(value: unknown): CaseFact | null {
  if (!isRecord(value)) return null;
  if (value.status === "unknown" || value.status === "declined") return value.value == null ? { status: value.status } : null;
  return value.status === "provided" && boundedText(value.value) ? { status: "provided", value: value.value.trim() } : null;
}
export function normalizeCaseContext(value: unknown): CaseContext {
  if (!isRecord(value) || value.schema_version !== 1 || !Number.isSafeInteger(value.revision) || Number(value.revision) < 0 || !isRecord(value.facts)) return emptyCaseContext();
  const facts: CaseContext["facts"] = {};
  for (const key of FACT_KEYS) {
    const fact = parseFact(value.facts[key]);
    if (fact) facts[key] = fact;
  }
  return { schema_version: 1, revision: Number(value.revision), facts };
}
export function isFactUpdate(value: unknown): value is FactUpdate {
  return isRecord(value) && onlyKeys(value, ["fact_key", "status", "value", "evidence", "kind"]) && isFactKey(value.fact_key) && parseFact(value) !== null && boundedText(value.evidence, 500) && (value.kind === "explicit" || value.kind === "confirmation");
}
export function isClarification(value: unknown): value is Clarification {
  return isRecord(value) && onlyKeys(value, ["question_id", "fact_key", "reason", "question", "options"]) && boundedText(value.question_id, 80) && /^[a-zA-Z0-9_.:-]+$/.test(value.question_id) && isFactKey(value.fact_key) && boundedText(value.reason, 1000) && boundedText(value.question, 1000)
    && Array.isArray(value.options) && value.options.length <= 8 && value.options.every(option => isRecord(option) && onlyKeys(option, ["label", "value"]) && boundedText(option.label, 200) && boundedText(option.value));
}
export function sameFact(a: CaseFact | undefined, b: CaseFact | undefined): boolean { return a?.status === b?.status && a?.value === b?.value; }
/** Only explicit, non-conflicting updates become facts. Nothing from a preview enters this function. */
export function applyFactUpdates(context: CaseContext, updates: FactUpdate[]): { context: CaseContext; pending: FactUpdate[] } {
  const facts = { ...context.facts };
  const pending: FactUpdate[] = [];
  let changed = false;
  for (const update of updates) {
    if (!isFactUpdate(update)) continue;
    const current = facts[update.fact_key];
    if (sameFact(current, update)) continue;
    if (update.kind === "confirmation" || current) {
      pending.push({ fact_key: update.fact_key, ...parseFact(update)!, evidence: update.evidence, kind: update.kind });
      continue;
    }
    facts[update.fact_key] = parseFact(update)!;
    changed = true;
  }
  return { context: changed ? { ...context, revision: context.revision + 1, facts } : context, pending };
}
