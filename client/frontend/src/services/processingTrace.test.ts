import { describe, expect, it } from "vitest";
import { appendProcessingStep, MAX_PROCESSING_STEPS, sanitizeProcessingTrace, type ProcessingTrace } from "./processingTrace";

const running = (): ProcessingTrace => ({ steps: [], outcome: "running", duration_ms: 0 });
describe("observable processing trace", () => {
  it("deduplicates adjacent events but retains a genuine return to the model and another attempt", () => {
    let trace = running();
    for (const [phase, ms, attempt] of [["waiting_model", 10, 0], ["waiting_model", 20, 0], ["retrieving", 30, 0], ["waiting_model", 40, 0], ["waiting_model", 50, 1]] as const) trace = appendProcessingStep(trace, phase, ms, attempt);
    expect(trace.steps).toEqual([
      { phase: "waiting_model", elapsed_ms: 10, attempt: 0 }, { phase: "retrieving", elapsed_ms: 30, attempt: 0 },
      { phase: "waiting_model", elapsed_ms: 40, attempt: 0 }, { phase: "waiting_model", elapsed_ms: 50, attempt: 1 },
    ]);
  });
  it("bounds the recorded list and does not append after a terminal outcome", () => {
    let trace = running();
    for (let i = 0; i < 100; i++) trace = appendProcessingStep(trace, i % 2 ? "waiting_model" : "retrieving", i, 0);
    expect(trace.steps).toHaveLength(MAX_PROCESSING_STEPS);
    trace = { ...trace, outcome: "cancelled" };
    expect(appendProcessingStep(trace, "generating", 200, 0)).toBe(trace);
  });
  it("projects local records to phase/time/attempt only, never arbitrary reasoning or queries", () => {
    const trace = sanitizeProcessingTrace({ outcome: "complete", duration_ms: 20, reasoning: "PRIVATE", steps: [{ phase: "retrieving", elapsed_ms: 10, attempt: 0, query: "PRIVATE" }] });
    expect(trace).toEqual({ outcome: "complete", duration_ms: 20, steps: [{ phase: "retrieving", elapsed_ms: 10, attempt: 0 }] });
    expect(JSON.stringify(trace)).not.toContain("PRIVATE");
  });
  it("rejects invalid clocks, unknown phases, unfinished or oversized persisted traces", () => {
    const good = { outcome: "complete", duration_ms: 20, steps: [{ phase: "retrieving", elapsed_ms: 10, attempt: 0 }] };
    for (const invalid of [
      { ...good, outcome: "running" }, { ...good, duration_ms: Infinity },
      { ...good, outcome: ["complete"] },
      { ...good, steps: [{ phase: ["retrieving"], elapsed_ms: 10, attempt: 0 }] },
      { ...good, steps: [{ phase: "private reasoning", elapsed_ms: 10, attempt: 0 }] },
      { ...good, steps: [{ phase: "retrieving", elapsed_ms: 21, attempt: 0 }] },
      { ...good, steps: [{ phase: "retrieving", elapsed_ms: 10, attempt: -1 }] },
      { ...good, steps: Array(41).fill(good.steps[0]) },
    ]) expect(sanitizeProcessingTrace(invalid)).toBeUndefined();
  });
});
