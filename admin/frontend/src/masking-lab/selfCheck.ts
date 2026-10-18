import type { Entity } from "./config";
import { MaskingError, previewMask } from "./extraction";
import type { BrowserMaskingRuntime } from "./runtime";
import type { ExtractionRound } from "./pipeline";

/** Synthetic controls, separate from both the demonstrations and scored fixtures. */
export const BASIC_CHECKS: { id: string; source: string; expected: Entity[] }[] = [
  { id: "basic-name-phone", source: "我叫許映辰，電話是0910-246-802。", expected: [{ text: "許映辰", type: "person" }, { text: "0910-246-802", type: "contact" }] },
  { id: "basic-email", source: "我的電子郵件是masking.check@example.com。", expected: [{ text: "masking.check@example.com", type: "contact" }] },
  { id: "basic-negative", source: "我和主管是同事，想知道如何記錄事件。", expected: [] },
];
export interface BasicCheckCase {
  id: string; source: string; expected: Entity[]; detected: Entity[];
  masked: string | null; error: string | null;
  rounds?: ExtractionRound[];
}
export interface BasicCheckReport { cases: BasicCheckCase[]; matched: number; expected: number; unexpected: number; passed: boolean }

/** This only proves some extraction works; it never changes benchmark scores. */
export async function runBasicChecks(runtime: BrowserMaskingRuntime, signal: AbortSignal): Promise<BasicCheckReport> {
  const cases: BasicCheckCase[] = [];
  for (const check of BASIC_CHECKS) {
    if (signal.aborted) break;
    let detected: Entity[] = [], error: string | null = null;
    let rounds: ExtractionRound[];
    try { const result = await runtime.detect(check.source); detected = result.entities; rounds = result.rounds ?? []; }
    catch (failure) { error = failure instanceof MaskingError ? failure.code : "model_execution_failed"; rounds = failure instanceof MaskingError ? failure.rounds : []; }
    if (signal.aborted) break;
    cases.push({ ...check, detected, rounds, error, masked: error ? null : previewMask(check.source, detected) });
    if (error) break;
  }
  const matched = cases.reduce((sum, item) => sum + item.expected.filter(entity => item.detected.some(found => found.text === entity.text)).length, 0);
  const expected = BASIC_CHECKS.reduce((sum, item) => sum + item.expected.length, 0);
  const unexpected = cases.reduce((sum, item) => sum + item.detected.filter(entity => !item.expected.some(wanted => wanted.text === entity.text)).length, 0);
  return { cases, matched, expected, unexpected, passed: !signal.aborted && cases.length === BASIC_CHECKS.length && cases.every(item => !item.error) && matched > 0 };
}
