import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { behaviorConfig, readyBehaviorCases } from "../test/behaviorFixture";
import { BEHAVIOR_HEADERS, builtinBehaviorCases, importBehaviorCases, prepareBehaviorRun, validateBehaviorCases } from "./behaviorTests";
import { parseScenarioWorkbook } from "./scenarioWorkbook";

describe("behavior test definitions", () => {
  it("preserves the original workbook IDs, descriptions and row provenance while expanding three shorthand scripts", () => {
    const workbook = parseScenarioWorkbook(readFileSync(resolve("..", "resource", "測試記錄.xlsx")), "測試記錄.xlsx");
    expect([...workbook.sheets[0].rows.get(1)!.values()]).toEqual(BEHAVIOR_HEADERS);
    const drafts = importBehaviorCases(workbook), builtins = builtinBehaviorCases();
    expect(drafts).toHaveLength(5);
    expect(drafts.map(test => [test.id, test.originalPrompt, test.source])).toEqual(builtins.map(test => [test.id, test.originalPrompt, test.source]));
    expect(drafts.filter(test => test.turns[0] === "").map(test => test.id)).toEqual(["H01", "C01", "P01"]);
    expect(drafts.every(test => !test.confirmed)).toBe(true);
    expect(() => validateBehaviorCases(drafts)).toThrow();
    expect(builtins.reduce((sum, test) => sum + test.turns.length, 0)).toBe(7);
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
