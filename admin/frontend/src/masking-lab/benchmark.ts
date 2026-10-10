import fixtureData from "./fixtures.json";
import { LIBRARY_REVISION, MODEL_ID, MODEL_REVISION, PROMPT_VERSION, RUNTIME_VERSION, type Entity } from "./config";
import { MaskingError, previewMask } from "./extraction";
import type { BrowserMaskingRuntime } from "./runtime";
import type { LoadMetrics } from "./types";

export type Split = "development" | "holdout";
export interface Fixture { id: string; split: Split; category: string; text: string; expected: Entity[] }
export const FIXTURES = fixtureData as Fixture[];
export interface CaseResult {
  id: string; split: Split; category: string; characters: number; source: string;
  expected: Entity[]; detected: Entity[]; masked: string | null;
  missing: string[]; unexpected: string[]; elapsedMs: number; error: string | null;
}
export interface Scores {
  cases: number; successfulChecks: number; truePositive: number; falsePositive: number; falseNegative: number;
  precision: number | null; recall: number; successRate: number; latencyCases: number; p95Ms: number | null; passed: boolean;
}
export interface EvaluationReport {
  schemaVersion: 1; createdAt: string; userAgent: string;
  model: string; modelRevision: string; libraryRevision: string; runtimeVersion: string; promptVersion: string;
  coldLoad: LoadMetrics | null; cachedLoad: LoadMetrics | null;
  scores: { all: Scores; development: Scores; holdout: Scores };
  gatePassed: boolean; cases: CaseResult[];
  limitations: string[];
}

export function scoreCases(cases: CaseResult[]): Scores {
  let truePositive = 0, falsePositive = 0, falseNegative = 0;
  for (const item of cases) {
    const expected = new Set(item.expected.map(entity => entity.text));
    const detected = new Set(item.detected.map(entity => entity.text));
    truePositive += [...expected].filter(text => detected.has(text)).length;
    falsePositive += [...detected].filter(text => !expected.has(text)).length;
    falseNegative += [...expected].filter(text => !detected.has(text)).length;
  }
  const successfulChecks = cases.filter(item => !item.error).length;
  // Latency covers all attempted 200–500-code-point cases, including failures.
  const latencies = cases.filter(item => item.characters >= 200 && item.characters <= 500).map(item => item.elapsedMs).sort((a, b) => a - b);
  const precision = truePositive + falsePositive ? truePositive / (truePositive + falsePositive) : null;
  const recall = truePositive + falseNegative ? truePositive / (truePositive + falseNegative) : 1;
  const p95Ms = latencies.length ? latencies[Math.ceil(latencies.length * 0.95) - 1] : null;
  return {
    cases: cases.length, successfulChecks, truePositive, falsePositive, falseNegative,
    precision, recall, successRate: cases.length ? successfulChecks / cases.length : 0, latencyCases: latencies.length, p95Ms,
    passed: cases.length > 0 && successfulChecks === cases.length && precision !== null && precision >= 0.95 && recall >= 0.95 && p95Ms !== null && p95Ms <= 5000,
  };
}

export function createReport(cases: CaseResult[], coldLoad: LoadMetrics | null, cachedLoad: LoadMetrics | null): EvaluationReport {
  const development = scoreCases(cases.filter(item => item.split === "development"));
  const holdout = scoreCases(cases.filter(item => item.split === "holdout"));
  const all = scoreCases(cases);
  return {
    schemaVersion: 1, createdAt: new Date().toISOString(), userAgent: navigator.userAgent,
    model: MODEL_ID, modelRevision: MODEL_REVISION, libraryRevision: LIBRARY_REVISION,
    runtimeVersion: RUNTIME_VERSION, promptVersion: PROMPT_VERSION,
    coldLoad, cachedLoad, scores: { all, development, holdout },
    gatePassed: development.cases === 80 && holdout.cases === 40 && all.passed && development.passed && holdout.passed,
    cases,
    limitations: [
      "僅使用合成繁體中文案例；含模板變化，不代表真實使用者分布或零漏判保證。",
      "精確率與召回率以每案例不重複的完整敏感片段比對；部分匹配同時計為誤遮及漏遮，類型差異另保留於原始結果。",
      "下載量為 fetch 回應的解壓後內容位元組，不含 TLS/HTTP 開銷；IndexedDB 命中不產生 fetch。",
      "結果只適用於記錄的裝置與瀏覽器；正式聊天尚未接入此驗證頁。",
    ],
  };
}

export async function evaluateSplit(runtime: BrowserMaskingRuntime, split: Split, signal: AbortSignal, onCase: (result: CaseResult) => void): Promise<CaseResult[]> {
  const results: CaseResult[] = [];
  for (const fixture of FIXTURES.filter(item => item.split === split)) {
    if (signal.aborted) break;
    const start = performance.now();
    let detected: Entity[] = [], error: string | null = null;
    try { detected = (await runtime.detect(fixture.text)).entities; }
    catch (failure) { error = failure instanceof MaskingError ? failure.code : "evaluation_failed"; }
    if (signal.aborted) break;
    const result: CaseResult = {
      id: fixture.id, split, category: fixture.category, characters: Array.from(fixture.text).length, source: fixture.text,
      expected: fixture.expected, detected, error, elapsedMs: performance.now() - start,
      masked: error ? null : previewMask(fixture.text, detected),
      missing: fixture.expected.filter(expected => !detected.some(item => item.text === expected.text)).map(item => item.text),
      unexpected: detected.filter(item => !fixture.expected.some(expected => item.text === expected.text)).map(item => item.text),
    };
    results.push(result); onCase(result);
    // A terminated worker needs an explicit reload. Do not invent results for remaining cases.
    if (["worker_failed", "model_timeout", "model_not_ready"].includes(error ?? "")) break;
  }
  return results;
}
