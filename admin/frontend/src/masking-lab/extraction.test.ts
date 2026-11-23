import { describe, expect, it } from "vitest";
import { FIXTURES, createReport, meetsPerTypeThreshold, scoreCases, scoreEntityTypes, type CaseResult } from "./benchmark";
import { ENTITY_TYPES } from "./config";
describe("evaluation evidence", () => {
  it("has 120 source-grounded synthetic cases with a separate 40-case holdout", () => {
    expect(FIXTURES).toHaveLength(120);
    expect(FIXTURES.filter(item => item.split === "holdout")).toHaveLength(40);
    expect(new Set(FIXTURES.map(item => item.id)).size).toBe(120);
    expect(FIXTURES.every(item => item.expected.every(entity => item.text.includes(entity.text)))).toBe(true);
    expect(FIXTURES.filter(item => item.text.length >= 200 && item.text.length <= 500).length).toBeGreaterThanOrEqual(40);
  });
  it("counts partial spans as both missing and unexpected, and errors cannot pass", () => {
    const result: CaseResult = { id: "test", split: "development", category: "person", characters: 250, source: "", expected: [{ text: "林沐青", type: "person" }], detected: [{ text: "林", type: "person" }], masked: null, missing: ["林沐青"], unexpected: ["林"], elapsedMs: 20, error: null };
    expect(scoreCases([result])).toMatchObject({ precision: 0, recall: 0, falseNegative: 1, falsePositive: 1, passed: false });
    expect(scoreCases([{ ...result, detected: result.expected, error: "model_execution_failed" }]).passed).toBe(false);
    expect(scoreCases([{ ...result, detected: [] }])).toMatchObject({ precision: null, recall: 0, passed: false });
    expect(scoreCases([]).passed).toBe(false);
  });
  it("reports exact per-type results and counts a wrong type on both sides", () => {
    const result: CaseResult = { id: "wrong-type", split: "holdout", category: "mixed", characters: 250, source: "合成甲在虛構公司", expected: [{ text: "合成甲", type: "person" }, { text: "虛構公司", type: "organization" }], detected: [{ text: "合成甲", type: "organization" }, { text: "虛構公司", type: "organization" }], masked: null, missing: [], unexpected: [], elapsedMs: 20, error: null };
    const scores = scoreEntityTypes([result]);
    expect(scores.person).toMatchObject({ expected: 1, detected: 0, truePositive: 0, falseNegative: 1, recall: 0 });
    expect(scores.organization).toMatchObject({ expected: 1, detected: 2, truePositive: 1, falsePositive: 1, precision: 0.5, recall: 1 });
    expect(scores.address).toMatchObject({ expected: 0, detected: 0, precision: null, recall: null });
  });
  it("fails the per-type gate when aggregate scores pass but one type is below 95%", () => {
    const cases: CaseResult[] = ENTITY_TYPES.flatMap(type => Array.from({ length: 24 }, (_, index) => {
      const entity = { text: `${type}-${index}`, type };
      return { id: entity.text, split: index < 16 ? "development" as const : "holdout" as const, category: type, characters: 250,
        source: entity.text, expected: [entity], detected: [type === "person" && index < 2 ? { ...entity, type: "organization" as const } : entity],
        masked: "[已隱藏]", missing: [], unexpected: [], elapsedMs: 10, error: null };
    }));
    expect(scoreCases(cases).passed).toBe(true);
    expect(scoreCases(cases.filter(item => item.split === "development")).passed).toBe(true);
    expect(scoreCases(cases.filter(item => item.split === "holdout")).passed).toBe(true);
    expect(meetsPerTypeThreshold(scoreEntityTypes(cases.filter(item => item.split === "development")))).toBe(false);
    expect(createReport(cases, null, null).gatePassed).toBe(false);
  });
});
