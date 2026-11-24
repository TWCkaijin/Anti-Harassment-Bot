import fixtureData from "./fixtures.json";
import { ENTITY_TYPES, ORT_VERSION, RULES_VERSION, TINY_MODEL, TINY_REVISION, type Entity, type EntityType } from "./config";
import { MaskingError, previewMask } from "./extraction";
import type { BrowserMaskingRuntime } from "./runtime";
import type { EntitySpan, LoadMetrics } from "./types";
import type { BasicCheckReport } from "./selfCheck";

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
export interface EntityTypeScores {
  expected: number; detected: number; truePositive: number; falsePositive: number; falseNegative: number;
  precision: number | null; recall: number | null; f1: number | null;
}
export type PerTypeScores = Record<EntityType, EntityTypeScores>;
export interface EvaluationReport {
  schemaVersion: 2; createdAt: string; userAgent: string;
  method: "rules-tiny";
  model: string; modelRevision: string; libraryRevision: string | null; runtimeVersion: string | null; promptVersion: string | null; rulesVersion: string | null;
  coldLoad: LoadMetrics | null; cachedLoad: LoadMetrics | null;
  basicCheck: BasicCheckReport | null;
  scores: { all: Scores; development: Scores; holdout: Scores };
  perType: { all: PerTypeScores; development: PerTypeScores; holdout: PerTypeScores };
  gatePassed: boolean; cases: CaseResult[];
  limitations: string[];
}

/** Exact (source span, type) matches; a wrong type is a miss and an extra prediction. */
export function scoreEntityTypes(cases: CaseResult[]): PerTypeScores {
  const output = {} as PerTypeScores;
  for (const type of ENTITY_TYPES) {
    let truePositive = 0, falsePositive = 0, falseNegative = 0;
    for (const item of cases) {
      const expected = new Set(item.expected.filter(entity => entity.type === type).map(entity => entity.text));
      const detected = new Set(item.detected.filter(entity => entity.type === type).map(entity => entity.text));
      truePositive += [...expected].filter(text => detected.has(text)).length;
      falsePositive += [...detected].filter(text => !expected.has(text)).length;
      falseNegative += [...expected].filter(text => !detected.has(text)).length;
    }
    const expected = truePositive + falseNegative, detected = truePositive + falsePositive;
    const precision = detected ? truePositive / detected : null;
    const recall = expected ? truePositive / expected : null;
    output[type] = {
      expected, detected, truePositive, falsePositive, falseNegative, precision, recall,
      f1: precision !== null && recall !== null && precision + recall > 0 ? 2 * precision * recall / (precision + recall) : null,
    };
  }
  return output;
}

/** Every type must have annotated and predicted spans in each evaluation split. */
export function meetsPerTypeThreshold(scores: PerTypeScores): boolean {
  return ENTITY_TYPES.every(type => scores[type].expected > 0 && scores[type].detected > 0
    && scores[type].precision !== null && scores[type].precision >= 0.95
    && scores[type].recall !== null && scores[type].recall >= 0.95);
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

export function createReport(cases: CaseResult[], coldLoad: LoadMetrics | null, cachedLoad: LoadMetrics | null, basicCheck: BasicCheckReport | null = null): EvaluationReport {
  const development = scoreCases(cases.filter(item => item.split === "development"));
  const holdout = scoreCases(cases.filter(item => item.split === "holdout"));
  const all = scoreCases(cases);
  const perType = {
    all: scoreEntityTypes(cases),
    development: scoreEntityTypes(cases.filter(item => item.split === "development")),
    holdout: scoreEntityTypes(cases.filter(item => item.split === "holdout")),
  };
  return {
    schemaVersion: 2, createdAt: new Date().toISOString(), userAgent: navigator.userAgent,
    method: "rules-tiny", model: TINY_MODEL, modelRevision: TINY_REVISION, libraryRevision: ORT_VERSION,
    runtimeVersion: ORT_VERSION, promptVersion: null, rulesVersion: RULES_VERSION,
    coldLoad, cachedLoad, basicCheck, scores: { all, development, holdout }, perType,
    gatePassed: development.cases === 80 && holdout.cases === 40 && all.passed && development.passed && holdout.passed
      && meetsPerTypeThreshold(perType.development) && meetsPerTypeThreshold(perType.holdout),
    cases,
    limitations: [
      "僅使用合成繁體中文案例；含模板變化，不代表真實使用者分布或零漏判保證。",
      "40 則保留驗證案例仍出自同一套合成資料設計，尚缺獨立蒐集且經人工標注的真實情境保留集。",
      "gatePassed 只代表固定合成案例達到門檻；啟用正式聊天仍需獨立人工標注保留集驗證。",
      "精確率與召回率以每案例不重複的完整敏感片段比對；部分匹配同時計為誤遮及漏遮，類型差異另保留於原始結果。",
      "下載量為讀取的資源內容位元組，不含 TLS/HTTP 開銷；瀏覽器 HTTP 快取仍可能回傳完整內容。",
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
    let spans: EntitySpan[] = [];
    try { const detection = await runtime.detect(fixture.text); detected = detection.entities; spans = detection.spans; }
    catch (failure) { error = failure instanceof MaskingError ? failure.code : "evaluation_failed"; }
    if (signal.aborted) break;
    const result: CaseResult = {
      id: fixture.id, split, category: fixture.category, characters: Array.from(fixture.text).length, source: fixture.text,
      expected: fixture.expected, detected, error, elapsedMs: performance.now() - start,
      masked: error ? null : previewMask(fixture.text, spans),
      missing: fixture.expected.filter(expected => !detected.some(item => item.text === expected.text)).map(item => item.text),
      unexpected: detected.filter(item => !fixture.expected.some(expected => item.text === expected.text)).map(item => item.text),
    };
    results.push(result); onCase(result);
    // A terminated worker needs an explicit reload. Do not invent results for remaining cases.
    if (["worker_failed", "model_timeout", "model_not_ready"].includes(error ?? "")) break;
  }
  return results;
}
