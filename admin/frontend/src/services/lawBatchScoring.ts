import type { ApplicableLawName } from "./api";
import type { BatchRow } from "./applicableLawBatch";

export const LAW_MATCH_RULE = "預測與參考答案命中任一法規即算正確；忽略順序及額外法規。";
const aliases: Record<string, ApplicableLawName> = {
  性工法: "性工法", 性別平等工作法: "性工法",
  性平法: "性平法", 性教法: "性平法", 性別平等教育法: "性平法",
  性騷法: "性騷法", 性騷擾防治法: "性騷法",
};
export interface ReferenceAnswer { raw: string; laws: ApplicableLawName[]; status: "valid" | "missing" | "invalid" | "unselected" }
export function parseReferenceAnswer(raw?: string): ReferenceAnswer {
  if (raw === undefined) return { raw: "", laws: [], status: "unselected" };
  if (!raw.trim()) return { raw, laws: [], status: "missing" };
  const parts = raw.split(/[,，、;；/／\r\n]+/u).map(part => part.trim()).filter(Boolean);
  if (!parts.length || parts.some(part => !Object.hasOwn(aliases, part))) return { raw, laws: [], status: "invalid" };
  return { raw, laws: [...new Set(parts.map(part => aliases[part]))], status: "valid" };
}
export type ScorableLawRow = Pick<BatchRow, 'reference' | 'status' | 'laws'>;
export function evaluateLawRow(row: ScorableLawRow) {
  const reference = row.reference ?? parseReferenceAnswer();
  const matches = row.status === "predicted" ? reference.laws.filter(law => row.laws.includes(law)) : [];
  const labels = { unselected: "未設定參考答案", missing: "參考答案空白", invalid: "參考答案無法識別" };
  const result = reference.status !== "valid" ? labels[reference.status]
    : row.status === "pending" || row.status === "running" ? "待評分"
    : row.status === "error" ? "失敗"
    : row.status === "insufficient_facts" ? "無法評估：資訊不足"
    : row.status === "no_target_law" ? "無法評估：不適用三法"
    : matches.length ? "正確" : "錯誤";
  return { reference, matches, result, correct: matches.length > 0 };
}
export interface BatchMetric { label: string; numerator: number; denominator: number; value: number | null }
export function formatBatchRate(metric: BatchMetric): string {
  return metric.value === null ? "—" : `${(metric.value * 100).toFixed(2)}%`;
}
export function scoreLawBatch(rows: ScorableLawRow[]) {
  const evaluated = rows.map(evaluateLawRow);
  const N = rows.length, R = evaluated.filter(row => row.reference.status === "valid").length;
  const A = rows.filter(row => row.status === "predicted" && row.laws.length > 0).length;
  const AR = rows.filter((row, index) => row.status === "predicted" && row.laws.length > 0 && evaluated[index].reference.status === "valid").length;
  const C = evaluated.filter(row => row.correct).length;
  const F = rows.filter(row => row.status === "error").length;
  const insufficient = rows.filter(row => row.status === "insufficient_facts").length;
  const noTarget = rows.filter(row => row.status === "no_target_law").length;
  const U = insufficient + noTarget;
  const pending = rows.filter(row => row.status === "pending" || row.status === "running").length;
  const referenceMissing = evaluated.filter(row => row.reference.status === "missing").length;
  const referenceInvalid = evaluated.filter(row => row.reference.status === "invalid").length;
  const metrics: BatchMetric[] = ([
    ["總正確率", C, R], ["回答正確率", C, AR], ["失敗率", F, N], ["無法評估率", U, N], ["回答率", A, N],
  ] as const).map(([label, numerator, denominator]) => ({ label, numerator, denominator, value: denominator ? numerator / denominator : null }));
  return { N, R, A, AR, C, F, U, insufficient, noTarget, pending, referenceMissing, referenceInvalid, metrics };
}
