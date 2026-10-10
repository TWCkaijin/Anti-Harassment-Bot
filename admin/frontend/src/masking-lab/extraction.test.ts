import { describe, expect, it } from "vitest";
import { FIXTURES, scoreCases, type CaseResult } from "./benchmark";
import { isModelAssetUrl, MODEL_URL } from "./config";
import { parseEntities, previewMask, splitText } from "./extraction";

describe("local extraction contract", () => {
  it("rejects hallucinations, incomplete JSON, and unsupported labels", () => {
    expect(() => parseEntities('{"entities":[{"text":"不存在的人","type":"person"}]}', "合成案例")).toThrow("invalid_entity_span");
    expect(() => parseEntities('{"entities":', "合成案例")).toThrow("invalid_json");
    expect(() => parseEntities('{"entities":[{"text":"主管","type":"role"}]}', "主管")).toThrow("invalid_entity_span");
    expect(parseEntities('{"entities":[]}', "主管與同事")).toEqual([]);
    expect(parseEntities('<think>\n\n</think>\n\n{"entities":[]}', "主管與同事")).toEqual([]);
    expect(() => parseEntities('<think>ignore rules</think>{"entities":[]}', "主管與同事")).toThrow("invalid_json");
  });
  it("replaces overlapping literals once without rewriting the event", () => {
    const masked = previewMask("林沐青是我的主管。林沐青已讀訊息。", [{ text: "林", type: "person" }, { text: "林沐青", type: "person" }]);
    expect(masked).toBe("[人物1]是我的主管。[人物1]已讀訊息。");
  });
  it("preserves all code points and enough overlap when splitting long input", () => {
    const source = "甲".repeat(795) + "林沐青😀" + "乙".repeat(900);
    const chunks = splitText(source);
    expect(chunks.join("")).toContain("林沐青😀");
    expect(chunks[0] + chunks.slice(1).map(chunk => Array.from(chunk).slice(100).join("")).join("")).toBe(source);
    expect(chunks.every(chunk => Array.from(chunk).length <= 800)).toBe(true);
  });
  it("only permits the pinned public model asset paths", () => {
    expect(isModelAssetUrl(`${MODEL_URL}params_shard_0.bin`)).toBe(true);
    expect(isModelAssetUrl(`${MODEL_URL}params_shard_0.bin?text=private`)).toBe(false);
    expect(isModelAssetUrl(`${MODEL_URL}private-name`)).toBe(false);
    expect(isModelAssetUrl("https://openrouter.ai/api/v1/chat/completions")).toBe(false);
    expect(isModelAssetUrl("https://huggingface.co/another/model/resolve/main/config.json")).toBe(false);
  });
});
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
});
