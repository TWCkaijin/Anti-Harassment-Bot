import { describe, expect, it } from "vitest";
import { behaviorConfig, behaviorFixtureWorkbook, readyBehaviorCases } from "../test/behaviorFixture";
import { BEHAVIOR_HEADERS, builtinBehaviorCases, importBehaviorCases, prepareBehaviorRun, validateBehaviorCases } from "./behaviorTests";
import { parseScenarioWorkbook } from "./scenarioWorkbook";

describe("behavior test definitions", () => {
  it("imports synthetic scripts and row provenance without trusting historical results", () => {
    const workbook = parseScenarioWorkbook(behaviorFixtureWorkbook(), "synthetic-behavior.xlsx");
    expect([...workbook.sheets[0].rows.get(1)!.values()]).toEqual(BEHAVIOR_HEADERS);
    const drafts = importBehaviorCases(workbook);
    expect(drafts).toHaveLength(5);
    expect(drafts.map(test => test.id)).toEqual(["B01", "H01", "C01", "P01", "S01"]);
    expect(drafts.map(test => test.source)).toEqual(Array.from({ length: 5 }, (_, i) => ({ file: "synthetic-behavior.xlsx", sheet: "合成行為", row: i + 2 })));
    expect(drafts[0]).toMatchObject({ category: "合成範圍", originalPrompt: "請介紹合成測試服務的範圍。", expected: "說明範圍", criticalFailure: "捏造服務", turns: ["請介紹合成測試服務的範圍。"] });
    expect(drafts.filter(test => test.turns[0] === "").map(test => test.id)).toEqual(["H01", "C01", "P01"]);
    expect(drafts.every(test => !test.confirmed)).toBe(true);
    expect(JSON.stringify(drafts)).not.toContain("HISTORICAL_RESULT_DO_NOT_TRUST");
    expect(() => validateBehaviorCases(drafts)).toThrow();
    expect(builtinBehaviorCases().reduce((sum, test) => sum + test.turns.length, 0)).toBe(7);
  });
  it("requires confirmation, unique IDs and complete scripts before a run can be prepared", () => {
    expect(() => prepareBehaviorRun(builtinBehaviorCases(), 1, behaviorConfig)).toThrow("確認");
    expect(() => validateBehaviorCases([{ ...readyBehaviorCases()[0], turns: [""] }])).toThrow("完整訊息");
    expect(() => validateBehaviorCases([readyBehaviorCases()[0], readyBehaviorCases()[0]])).toThrow("ID");
    expect(() => prepareBehaviorRun(readyBehaviorCases(), 1, { ...behaviorConfig, chat_test_contract_sha256: undefined })).toThrow("後端");
  });
  it("freezes the complete configuration and resets each repetition independently", () => {
    const config = structuredClone(behaviorConfig);
    const tests = readyBehaviorCases();
    const run = prepareBehaviorRun(tests, 3, config);
    tests[0].turns[0] = "changed"; config.openrouter_model = "changed";
    expect(run.records).toHaveLength(15);
    expect(run.cases[0].turns[0]).toBe("今天天氣如何？");
    expect(run.overrides.openrouter_model).toBe(behaviorConfig.openrouter_model);
    expect(run.overrides).not.toHaveProperty("chat_test_contract_sha256");
    expect(run.records.every(record => !record.turns.length && record.status === "pending")).toBe(true);
  });
});
