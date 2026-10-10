import { isRecord } from "./caseFacts";

export interface ReasoningEntry { text: string; kind: "text" | "summary"; stage: "understanding" | "answer" }
export const MAX_REASONING_CHARACTERS = 128_000;
export function isReasoningEntry(value: unknown): value is ReasoningEntry {
  return isRecord(value) && Object.keys(value).every(key => ["text", "kind", "stage"].includes(key))
    && typeof value.text === "string" && value.text.length > 0 && value.text.length <= 32_000
    && (value.kind === "text" || value.kind === "summary") && (value.stage === "understanding" || value.stage === "answer");
}
/** SSE reasoning is additive and only retained in the current page's memory. */
export function appendReasoning(entries: ReasoningEntry[], entry: ReasoningEntry): ReasoningEntry[] {
  if (!isReasoningEntry(entry)) return entries;
  const remaining = MAX_REASONING_CHARACTERS - entries.reduce((count, item) => count + item.text.length, 0);
  if (remaining <= 0) return entries;
  const text = entry.text.slice(0, remaining);
  const last = entries.at(-1);
  return last?.kind === entry.kind && last.stage === entry.stage
    ? [...entries.slice(0, -1), { ...last, text: last.text + text }]
    : [...entries, { ...entry, text }];
}
