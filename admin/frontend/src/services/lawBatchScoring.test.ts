import { describe, expect, it } from "vitest";
import { prepareLawBatch, type BatchRow, type BatchStatus } from "./applicableLawBatch";
import { evaluateLawRow, formatBatchRate, parseReferenceAnswer, scoreLawBatch } from "./lawBatchScoring";
import { batchConfig } from "../test/scenarioFixture";

const row = (status: BatchStatus, laws: string[] = [], reference?: string): BatchRow => ({
  ...prepareLawBatch([{ row: 2, text: "合成情境", reference }], batchConfig, []).rows[0], status, laws,
});
describe("local reference evaluation", () => {
  it("accepts full names, old education alias and supported separators, without guessing prose", () => {
    expect(parseReferenceAnswer("性教法、性工法；性別平等教育法/性騷法\n性工法").laws).toEqual(["性平法", "性工法", "性騷法"]);
    expect(parseReferenceAnswer("不適用性工法").status).toBe("invalid");
    expect(parseReferenceAnswer("性工法、未知法").status).toBe("invalid");
    expect(parseReferenceAnswer("").status).toBe("missing");
    expect(parseReferenceAnswer().status).toBe("unselected");
  });
  it("uses any intersection, ignoring extra predictions and order", () => {
    expect(evaluateLawRow(row("predicted", ["性騷法", "性工法"], "性工法、性平法"))).toMatchObject({ correct: true, matches: ["性工法"] });
    expect(evaluateLawRow(row("predicted", ["性騷法"], "性工法、性平法")).correct).toBe(false);
  });
  it("computes explicit denominators, counts and retry replacement", () => {
    const rows = [row("predicted", ["性工法"], "性工法"), row("predicted", ["性騷法"], "性工法"), row("insufficient_facts", [], "性平法"), row("no_target_law", [], "性工法"), row("error", [], "性工法"), row("predicted", ["性工法"], ""), row("pending", [], "未知")];
    const stats = scoreLawBatch(rows);
    expect(stats).toMatchObject({ N: 7, R: 5, A: 3, AR: 2, C: 1, F: 1, U: 2, pending: 1, referenceMissing: 1, referenceInvalid: 1 });
    expect(stats.metrics.map(m => [m.numerator, m.denominator])).toEqual([[1, 5], [1, 2], [1, 7], [2, 7], [3, 7]]);
    expect(formatBatchRate(stats.metrics[1])).toBe("50.00%");
    rows[4] = row("predicted", ["性工法"], "性工法");
    expect(scoreLawBatch(rows)).toMatchObject({ N: 7, C: 2, F: 0, AR: 3 });
  });
  it("has no zero-denominator percentage and never puts reference answers in model drafts", () => {
    expect(scoreLawBatch([]).metrics.every(m => formatBatchRate(m) === "—")).toBe(true);
    const prepared = row("pending", [], "性平法");
    expect(JSON.stringify(prepared.draft)).not.toContain("性平法");
    expect(prepared.reference?.laws).toEqual(["性平法"]);
  });
});
