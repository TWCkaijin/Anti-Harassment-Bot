import { expect, it } from "vitest";
import { previewMask } from "./extraction";
import { detectRuleSpans, prioritizeRules } from "./rules";

it("preserves source offsets after full-width characters and astral Unicode", () => {
  const source = "🙂寄到臺中市霧丘區棠光路８８巷６號。手機０９１０－２４６－８０２；撥１１３。";
  const spans = detectRuleSpans(source);
  expect(spans.map(span => source.slice(span.start, span.end))).toEqual([
    "０９１０－２４６－８０２", "臺中市霧丘區棠光路８８巷６號",
  ]);
  expect(previewMask(source, spans)).toBe("🙂寄到[地址1]。手機[聯絡資訊1]；撥１１３。");
});

it("keeps model extensions, suppresses redundant predictions and masks only the detected occurrence", () => {
  const rules = [{ start: 0, end: 3, type: "organization" }] as const;
  const spans = prioritizeRules([...rules], [
    { start: 0, end: 2, type: "person" },
    { start: 2, end: 4, type: "organization" },
  ]);
  expect(spans).toEqual([...rules, { start: 2, end: 4, type: "organization" }]);
  expect(previewMask("霧丘學校、霧丘學校", spans)).toBe("[單位1]、霧丘學校");
});
