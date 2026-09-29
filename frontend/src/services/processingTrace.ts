import { CHAT_PROGRESS_PHASES, type ChatProgress } from "./api";
import { isRecord } from "./caseFacts";

export type ProcessingPhase = ChatProgress["phase"] | "connecting" | "retrying";
export interface ProcessingStep { phase: ProcessingPhase; elapsed_ms: number; attempt: number }
export interface ProcessingTrace {
  steps: ProcessingStep[];
  duration_ms: number;
  outcome: "running" | "complete" | "cancelled" | "error";
}

export const MAX_PROCESSING_STEPS = 40;
const phases: readonly string[] = [...CHAT_PROGRESS_PHASES, "connecting", "retrying"];
const validMilliseconds = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= Number.MAX_SAFE_INTEGER;

/** Fixed phase identifiers and timing only: never model reasoning, queries or case text. */
export function appendProcessingStep(trace: ProcessingTrace, phase: ProcessingPhase, elapsedMs: number, attempt: number): ProcessingTrace {
  if (trace.outcome !== "running" || !phases.includes(phase) || !validMilliseconds(elapsedMs) || !Number.isSafeInteger(attempt) || attempt < 0) return trace;
  const last = trace.steps.at(-1);
  const elapsed_ms = Math.max(trace.duration_ms, Math.round(elapsedMs));
  const steps = last?.phase === phase && last.attempt === attempt
    ? trace.steps
    : [...trace.steps, { phase, elapsed_ms, attempt }].slice(-MAX_PROCESSING_STEPS);
  return { ...trace, steps, duration_ms: elapsed_ms };
}

/** Project persisted records onto a small allowlist; old messages get no invented trace. */
export function sanitizeProcessingTrace(value: unknown): ProcessingTrace | undefined {
  if (!isRecord(value) || typeof value.outcome !== "string" || !["complete", "cancelled", "error"].includes(value.outcome)
    || !validMilliseconds(value.duration_ms) || !Array.isArray(value.steps)
    || value.steps.length === 0 || value.steps.length > MAX_PROCESSING_STEPS) return;
  const steps: ProcessingStep[] = [];
  for (const item of value.steps) {
    if (!isRecord(item) || typeof item.phase !== "string" || !phases.includes(item.phase) || !validMilliseconds(item.elapsed_ms)
      || item.elapsed_ms > value.duration_ms || !Number.isSafeInteger(item.attempt) || Number(item.attempt) < 0
      || (steps.length > 0 && (item.elapsed_ms < steps.at(-1)!.elapsed_ms || Number(item.attempt) < steps.at(-1)!.attempt))) return;
    steps.push({ phase: item.phase as ProcessingPhase, elapsed_ms: item.elapsed_ms, attempt: Number(item.attempt) });
  }
  return { outcome: value.outcome as ProcessingTrace["outcome"], duration_ms: value.duration_ms, steps };
}
