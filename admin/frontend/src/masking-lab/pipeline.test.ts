import { describe, expect, it, vi } from "vitest";
import { extractInStages, parseClassification, parseSpans, type Generate } from "./pipeline";
import { MaskingError } from "./extraction";

describe("separate location and classification", () => {
  it("classifies only grounded candidates, one at a time, with independent context", async () => {
    const generate = vi.fn<Generate>()
      .mockResolvedValueOnce('{"spans":["許映辰","0910-246-802","許映辰"]}')
      .mockResolvedValueOnce('{"type":"person"}')
      .mockResolvedValueOnce('{"type":"contact"}');
    const result = await extractInStages("許映辰的電話是0910-246-802。", generate);
    expect(result.entities).toEqual([{ text: "許映辰", type: "person" }, { text: "0910-246-802", type: "contact" }]);
    expect(result.rounds.map(round => round.stage)).toEqual(["locate", "classify", "classify"]);
    expect(generate).toHaveBeenCalledTimes(3);
    const locateMessages = generate.mock.calls[0][0];
    expect(locateMessages.map(message => message.role)).toEqual(["system", "user"]);
    expect(JSON.parse(locateMessages[1].content as string)).toEqual({ text: "許映辰的電話是0910-246-802。" });
    expect(JSON.stringify(locateMessages)).not.toContain("DEMO115888");
    for (const [index, candidate] of ["許映辰", "0910-246-802"].entries()) {
      const messages = generate.mock.calls[index + 1][0];
      expect(messages).toHaveLength(2);
      expect(JSON.parse(messages[1].content as string).candidate).toBe(candidate);
    }
  });
  it("keeps valid empty location output empty without inventing candidates", async () => {
    const generate = vi.fn<Generate>().mockResolvedValueOnce('{"spans":[]}');
    expect((await extractInStages("我想了解處理流程。", generate)).entities).toEqual([]);
    expect(generate).toHaveBeenCalledTimes(1);
  });
  it("allows classification to reject a generic term", async () => {
    const generate = vi.fn<Generate>().mockResolvedValueOnce('{"spans":["主管"]}').mockResolvedValueOnce('{"type":"none"}');
    expect((await extractInStages("主管與同事", generate)).entities).toEqual([]);
  });
  it("fails on fabricated location output before attempting classification", async () => {
    const generate = vi.fn<Generate>().mockResolvedValueOnce('{"spans":["假造姓名"]}');
    await expect(extractInStages("主管與同事", generate)).rejects.toMatchObject({ code: "invalid_entity_span", rounds: [{ stage: "locate", raw: '{"spans":["假造姓名"]}' }] });
    expect(generate).toHaveBeenCalledTimes(1);
  });
  it("does not turn a failed second round into a successful empty check", async () => {
    const generate = vi.fn<Generate>().mockResolvedValueOnce('{"spans":["許映辰"]}').mockResolvedValueOnce('{"type":"invalid"}');
    await expect(extractInStages("我是許映辰", generate)).rejects.toMatchObject({ code: "invalid_classification" });
  });
  it("preserves incomplete model output for inspection but rejects the check", async () => {
    const error = new MaskingError("incomplete_output"); error.generatedOutput = '{"type":';
    const generate = vi.fn<Generate>().mockResolvedValueOnce('{"spans":["許映辰"]}').mockRejectedValueOnce(error);
    await expect(extractInStages("許映辰", generate)).rejects.toMatchObject({ code: "incomplete_output", rounds: [
      { stage: "locate", raw: '{"spans":["許映辰"]}' },
      { stage: "classify", candidate: "許映辰", raw: '{"type":' },
    ] });
  });
  it("accepts only exact JSON schemas and the known empty thinking prefix", () => {
    expect(parseSpans('<think>\n\n</think>\n\n{"spans":["許映辰"]}', "許映辰")).toEqual(["許映辰"]);
    expect(() => parseSpans('{"spans":[""]}', "原文")).toThrow("invalid_entity_span");
    expect(() => parseSpans('{"spans":[],"explanation":""}', "原文")).toThrow("invalid_spans");
    expect(() => parseClassification('{"type":"person","text":"姓名"}')).toThrow("invalid_classification");
    expect(() => parseClassification('{"type":')).toThrow("invalid_json");
  });
});
