export const FACT_KEYS = ["subject_role", "other_role", "relationship", "work_related", "internship_related", "education_related", "behavior", "ongoing", "event_time", "age_group", "city", "desired_help"] as const;
export type FactKey = typeof FACT_KEYS[number];
export type FactStatus = "provided" | "unknown" | "declined";
export interface CaseFact { status: FactStatus; value?: string | string[] | null }
export interface LegacyCaseContext { schema_version: 1 | 2; revision: number; facts: Partial<Record<FactKey, CaseFact>> }
export interface SummaryCaseContext { schema_version: 3; revision: number; facts: Partial<Record<FactKey, never>>; summary: string; summary_origin: "model" | "user" | "migration" }
export type CaseContext = LegacyCaseContext | SummaryCaseContext;
export interface FactUpdate extends CaseFact { fact_key: FactKey; evidence: string; kind: "explicit" | "confirmation" }
interface ClarificationFields { question_id: string; context_revision?: number; reason: string; question: string; options: Array<{ label: string; value: string }>; selection_mode?: "single" | "multiple"; max_selections?: number; validation_token?: string; context_scope?: "personal" | "scenario" }
export type Clarification = ClarificationFields & ({ fact_key: FactKey } | { fact_key?: never });
interface ClarificationAnswerFields extends CaseFact { question_id: string; context_revision?: number; selection_mode?: "single" | "multiple"; max_selections?: number; allowed_values?: string[]; validation_token?: string; context_scope?: "personal" | "scenario" }
export type ClarificationAnswer = ClarificationAnswerFields & ({ fact_key: FactKey } | { fact_key?: never });
export interface SummaryUpdate { base_revision: number; summary: string; evidence: string[] }
export interface ClarificationDraft { selectedValues: string[]; other: string; includeOther: boolean; special: "unknown" | "declined" | null }
export const MAX_FACT_VALUE_LENGTH = 300;
export const MAX_FACT_VALUES = 4;
export const MAX_SUMMARY_LENGTH = 4000;
export const MULTI_FACT_KEYS: readonly FactKey[] = ["subject_role", "other_role", "relationship", "behavior", "desired_help"];
export const emptyCaseContext = (): LegacyCaseContext => ({ schema_version: 1, revision: 0, facts: {} });
export const emptySummaryContext = (): SummaryCaseContext => ({ schema_version: 3, revision: 0, facts: {}, summary: "", summary_origin: "model" });
export const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
export const isFactKey = (value: unknown): value is FactKey => typeof value === "string" && (FACT_KEYS as readonly string[]).includes(value);
export const boundedText = (value: unknown, limit = MAX_FACT_VALUE_LENGTH): value is string => typeof value === "string" && value.trim().length > 0 && value.length <= limit;
const onlyKeys = (value: Record<string, unknown>, allowed: readonly string[]) => Object.keys(value).every(key => allowed.includes(key));
export function parseFact(value: unknown): CaseFact | null {
  if (!isRecord(value)) return null;
  if (value.status === "unknown" || value.status === "declined") return value.value == null ? { status: value.status } : null;
  if (value.status !== "provided") return null;
  if (boundedText(value.value)) return { status: "provided", value: value.value.trim() };
  if (Array.isArray(value.value) && value.value.length > 0 && value.value.length <= MAX_FACT_VALUES && value.value.every(item => boundedText(item))) {
    const values = value.value.map(item => (item as string).trim());
    if (new Set(values).size === values.length) return { status: "provided", value: values };
  }
  return null;
}
export function normalizeCaseContext(value: unknown): CaseContext {
  if (isRecord(value) && value.schema_version === 3) return isCaseContext(value) && value.schema_version === 3 ? { ...value, facts: {} } : emptySummaryContext();
  if (!isRecord(value) || (value.schema_version !== 1 && value.schema_version !== 2) || !Number.isSafeInteger(value.revision) || Number(value.revision) < 0 || !isRecord(value.facts)) return emptyCaseContext();
  const facts: CaseContext["facts"] = {};
  for (const key of FACT_KEYS) {
    const fact = parseFact(value.facts[key]);
    if (fact && !(value.schema_version === 1 && Array.isArray(fact.value))) facts[key] = fact;
  }
  return { schema_version: value.schema_version as 1 | 2, revision: Number(value.revision), facts };
}
/** Validate persisted facts before migration, so failed conversion never erases data. */
export function isCaseContext(value: unknown): value is CaseContext {
  if (isRecord(value) && value.schema_version === 3) return onlyKeys(value, ["schema_version", "revision", "facts", "summary", "summary_origin"])
    && Number.isSafeInteger(value.revision) && Number(value.revision) >= 0 && isRecord(value.facts) && Object.keys(value.facts).length === 0
    && typeof value.summary === "string" && value.summary.length <= MAX_SUMMARY_LENGTH
    && ["model", "user", "migration"].includes(String(value.summary_origin));
  return isRecord(value) && (value.schema_version === 1 || value.schema_version === 2)
    && onlyKeys(value, ["schema_version", "revision", "facts"])
    && Number.isSafeInteger(value.revision) && Number(value.revision) >= 0 && isRecord(value.facts)
    && Object.entries(value.facts).every(([key, fact]) => isFactKey(key) && isRecord(fact) && onlyKeys(fact, ["status", "value"])
      && parseFact(fact) !== null && !(value.schema_version === 1 && Array.isArray(fact.value)));
}
export function hasCaseContext(context: CaseContext): boolean {
  return context.revision > 0 || (context.schema_version === 3 ? context.summary.trim().length > 0 : Object.keys(context.facts).length > 0);
}
const MIGRATION_LABELS: Record<FactKey, string> = {
  subject_role: "您的角色", other_role: "對方的角色", relationship: "雙方關係", work_related: "與工作的關聯", internship_related: "與實習的關聯", education_related: "與教育活動的關聯",
  behavior: "已描述的行為", ongoing: "是否持續發生", event_time: "事件大約時間", age_group: "年齡區間", city: "縣市", desired_help: "希望獲得的協助",
};
/** Full readable legacy text for explicit editing; never truncate or infer from old chat text. */
export function toSummaryText(context: CaseContext): string {
  if (context.schema_version === 3) return context.summary;
  return FACT_KEYS.flatMap(key => {
    const fact = context.facts[key];
    if (!fact) return [];
    if (fact.status === "declined") return [`您暫不提供${MIGRATION_LABELS[key]}。`];
    if (fact.status === "unknown") return [`${MIGRATION_LABELS[key]}尚不確定。`];
    const value = factText(fact.value).trim();
    return value ? [`${MIGRATION_LABELS[key]}：${value}。`] : [];
  }).join("\n");
}
/** Deterministic migration of confirmed facts only; oversized text needs user editing. */
export function toSummaryContext(context: CaseContext): SummaryCaseContext {
  if (context.schema_version === 3) return { ...context, facts: {} };
  const summary = toSummaryText(context);
  if (summary.length > MAX_SUMMARY_LENGTH) throw new RangeError("舊摘要超過可轉換長度，原始資料仍保留。");
  return { schema_version: 3, revision: context.revision, facts: {}, summary, summary_origin: "migration" };
}
export function isSummaryUpdate(value: unknown): value is SummaryUpdate {
  return isRecord(value) && onlyKeys(value, ["base_revision", "summary", "evidence"])
    && Number.isSafeInteger(value.base_revision) && Number(value.base_revision) >= 0
    && typeof value.summary === "string" && value.summary.length <= MAX_SUMMARY_LENGTH
    && Array.isArray(value.evidence) && value.evidence.length <= 30 && value.evidence.every(item => boundedText(item, 2000));
}
export function applySummaryUpdate(context: SummaryCaseContext, update: SummaryUpdate | null): SummaryCaseContext {
  if (!update || !isSummaryUpdate(update) || update.base_revision !== context.revision || update.summary === context.summary) return context;
  return { schema_version: 3, revision: context.revision + 1, facts: {}, summary: update.summary, summary_origin: "model" };
}
export function factText(value: CaseFact["value"]): string { return Array.isArray(value) ? value.join("、") : value ?? ""; }
export function isClarificationDraft(value: unknown): value is ClarificationDraft {
  return isRecord(value) && onlyKeys(value, ["selectedValues", "other", "includeOther", "special"])
    && Array.isArray(value.selectedValues) && value.selectedValues.length <= MAX_FACT_VALUES && value.selectedValues.every(item => boundedText(item))
    && new Set(value.selectedValues).size === value.selectedValues.length
    && typeof value.other === "string" && value.other.length <= MAX_FACT_VALUE_LENGTH && typeof value.includeOther === "boolean"
    && (value.special === null || value.special === "unknown" || value.special === "declined")
    && (value.special === null || (value.selectedValues.length === 0 && !value.includeOther));
}
export function isFactUpdate(value: unknown): value is FactUpdate {
  return isRecord(value) && onlyKeys(value, ["fact_key", "status", "value", "evidence", "kind"]) && isFactKey(value.fact_key) && parseFact(value) !== null && boundedText(value.evidence, 500) && (value.kind === "explicit" || value.kind === "confirmation");
}
export function isClarification(value: unknown, contractVersion?: 2 | 3 | 4): value is Clarification {
  return isRecord(value) && onlyKeys(value, ["question_id", "fact_key", "context_revision", "reason", "question", "options", "selection_mode", "max_selections", "validation_token", "context_scope"]) && boundedText(value.question_id, 80) && /^[a-zA-Z0-9_.:-]+$/.test(value.question_id)
    && (contractVersion === 4 ? !("fact_key" in value) : contractVersion === 2 || contractVersion === 3 ? isFactKey(value.fact_key) : value.fact_key === undefined || isFactKey(value.fact_key))
    && (value.context_revision === undefined ? contractVersion !== 4 : Number.isSafeInteger(value.context_revision) && Number(value.context_revision) >= 0)
    && boundedText(value.reason, 1000) && boundedText(value.question, 1000)
    && Array.isArray(value.options) && value.options.length <= 8 && value.options.every(option => isRecord(option) && onlyKeys(option, ["label", "value"]) && boundedText(option.label, 200) && boundedText(option.value))
    && (value.selection_mode === undefined || value.selection_mode === "single" || value.selection_mode === "multiple")
    && (value.max_selections === undefined || (Number.isSafeInteger(value.max_selections) && Number(value.max_selections) >= 1 && Number(value.max_selections) <= MAX_FACT_VALUES))
    && (value.selection_mode !== "single" || value.max_selections === 1)
    && (value.selection_mode !== "multiple" || (Number(value.max_selections) >= 2 && Number(value.max_selections) <= MAX_FACT_VALUES))
    && (value.validation_token === undefined || boundedText(value.validation_token, 8192))
    && (value.context_scope === undefined || value.context_scope === "personal" || value.context_scope === "scenario");
}
export function sameFact(a: CaseFact | undefined, b: CaseFact | undefined): boolean {
  if (a?.status !== b?.status) return false;
  if (Array.isArray(a?.value) && Array.isArray(b?.value)) return a.value.length === b.value.length && a.value.every(value => (b.value as string[]).includes(value));
  return a?.value === b?.value;
}
/** Only explicit, non-conflicting updates become facts. Nothing from a preview enters this function. */
export function applyFactUpdates(context: CaseContext, updates: FactUpdate[]): { context: CaseContext; pending: FactUpdate[] } {
  if (context.schema_version === 3) return { context, pending: [] };
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
