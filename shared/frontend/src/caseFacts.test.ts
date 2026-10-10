import { describe, expect, it } from "vitest";
import { applyFactUpdates, applySummaryUpdate, emptyCaseContext, emptySummaryContext, isCaseContext, isClarification, normalizeCaseContext, parseFact, sameFact, toSummaryContext, type FactUpdate } from "./caseFacts";
describe("necessary case facts", () => {
  it("keeps bounded allowed keys and separates unanswered, unknown and declined", () => {
    expect(normalizeCaseContext({ schema_version: 1, revision: 4, facts: { name: { status: "provided", value: "secret" }, city: { status: "declined", value: null }, relationship: { status: "unknown" }, other_role: { status: "provided", value: "x".repeat(301) } } })).toEqual({ schema_version: 1, revision: 4, facts: { city: { status: "declined" }, relationship: { status: "unknown" } } });
  });
  it("applies only new explicit facts and asks before conflicts or guesses", () => {
    const result = applyFactUpdates({ schema_version: 1, revision: 2, facts: { city: { status: "declined" }, work_related: { status: "unknown" }, other_role: { status: "provided", value: "主管" } } }, [
      { fact_key: "subject_role", status: "provided", value: "學生", evidence: "我是學生", kind: "explicit" },
      { fact_key: "city", status: "provided", value: "臺北", evidence: "臺北", kind: "explicit" },
      { fact_key: "work_related", status: "provided", value: "是", evidence: "工作", kind: "explicit" },
      { fact_key: "other_role", status: "provided", value: "同事", evidence: "同事", kind: "explicit" },
      { fact_key: "behavior", status: "provided", value: "言語", evidence: "說話", kind: "confirmation" },
    ]);
    expect(result.context.revision).toBe(3);
    expect(result.context.facts.subject_role?.value).toBe("學生");
    expect(result.context.facts.city?.status).toBe("declined");
    expect(result.context.facts.work_related?.status).toBe("unknown");
    expect(result.context.facts.other_role?.value).toBe("主管");
    expect(result.pending).toHaveLength(4);
  });
  it("ignores malformed facts and treats duplicate explicit updates as idempotent", () => {
    const update: FactUpdate = { fact_key: "behavior", status: "provided", value: "言語", evidence: "言語", kind: "explicit" };
    const first = applyFactUpdates(emptyCaseContext(), [update]).context;
    expect(applyFactUpdates(first, [update]).context).toBe(first);
    expect(applyFactUpdates(first, [{ ...update, fact_key: "name" } as unknown as FactUpdate]).context).toBe(first);
  });
});

describe("v3 fact and selection contracts", () => {
  it("preserves distinct multi-values and compares them independent of selection order", () => {
    const fact = { status: "provided" as const, value: ["學生", "兼職員工"] };
    expect(parseFact(fact)).toEqual(fact);
    expect(sameFact(fact, { ...fact, value: ["兼職員工", "學生"] })).toBe(true);
    const context = { schema_version: 2, revision: 3, facts: { subject_role: fact } };
    expect(isCaseContext(context)).toBe(true);
    expect(normalizeCaseContext(context)).toEqual(context);
    expect(isCaseContext({ ...context, schema_version: 1 })).toBe(false);
  });
  it.each([[], ["學生", "學生"], ["學生", " 學生 "], ["1", "2", "3", "4", "5"], ["x".repeat(301)], [null]].map(value => ({ value })))("rejects malformed array values $value", ({ value }) => {
    expect(parseFact({ status: "provided", value })).toBeNull();
  });
  it("validates selection limits and preserves the signed scenario scope", () => {
    const question = { question_id: "scenario.role.1", fact_key: "subject_role", reason: "釐清情境", question: "這位朋友有哪些身分？", options: [{ label: "學生", value: "學生" }], selection_mode: "multiple", max_selections: 2, validation_token: "signed", context_scope: "scenario" };
    expect(isClarification(question)).toBe(true);
    expect(isClarification({ ...question, selection_mode: "single" })).toBe(false);
    expect(isClarification({ ...question, max_selections: 5 })).toBe(false);
    expect(isClarification({ ...question, context_scope: "unrecognized" })).toBe(false);
  });
});
describe("text summary migration", () => {
  it("preserves distinct array values and unknown and declined wording without inferring missing facts", () => {
    const summary = toSummaryContext({ schema_version: 2, revision: 7, facts: { subject_role: { status: "provided", value: ["學生", "兼職員工"] }, city: { status: "declined" }, relationship: { status: "unknown" } } });
    expect(summary).toEqual({ schema_version: 3, revision: 7, facts: {}, summary: "您的角色：學生、兼職員工。\n雙方關係尚不確定。\n您暫不提供縣市。", summary_origin: "migration" });
    expect(isCaseContext(summary)).toBe(true);
    expect(isCaseContext({ ...summary, facts: { city: { status: "declined" } } })).toBe(false);
    expect(isCaseContext({ ...summary, summary: "x".repeat(4001) })).toBe(false);
  });
  it("advances revision only for a changed summary based on the current revision", () => {
    const current = emptySummaryContext();
    expect(applySummaryUpdate(current, { base_revision: 1, summary: "stale", evidence: [] })).toBe(current);
    expect(applySummaryUpdate(current, { base_revision: 0, summary: "", evidence: [] })).toBe(current);
    expect(applySummaryUpdate(current, { base_revision: 0, summary: "已確認的情境。", evidence: ["合成輸入"] })).toEqual({ ...current, revision: 1, summary: "已確認的情境。" });
  });
});
