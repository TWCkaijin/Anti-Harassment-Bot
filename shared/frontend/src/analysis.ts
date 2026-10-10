import { isRecord } from "./caseFacts";

export interface AnalysisEntry {
  stage: "understanding" | "sufficiency" | "sources" | "answer";
  summary: string;
  facts: string[];
  source_labels: string[];
  limitations: string[];
}

const textList = (value: unknown): value is string[] => Array.isArray(value) && value.length <= 30
  && value.every(item => typeof item === "string" && item.trim().length > 0 && item.length <= 2000);
export function isAnalysisEntry(value: unknown): value is AnalysisEntry {
  return isRecord(value) && Object.keys(value).every(key => ["stage", "summary", "facts", "source_labels", "limitations"].includes(key))
    && ["understanding", "sufficiency", "sources", "answer"].includes(String(value.stage))
    && typeof value.summary === "string" && value.summary.trim().length > 0 && value.summary.length <= 2000
    && textList(value.facts) && textList(value.source_labels) && textList(value.limitations);
}
export function sanitizeAnalysis(value: unknown): AnalysisEntry[] | undefined {
  if (!Array.isArray(value) || value.length > 4 || !value.every(isAnalysisEntry)) return undefined;
  return value.map(entry => ({ stage: entry.stage, summary: entry.summary, facts: [...entry.facts], source_labels: [...entry.source_labels], limitations: [...entry.limitations] }));
}
