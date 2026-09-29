import { describe, expect, it } from "vitest";
import { applyFactUpdates, emptyCaseContext, normalizeCaseContext, type FactUpdate } from "./caseFacts";
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
