import { afterEach, describe, expect, it, vi } from "vitest";
import { runAdminChatTest, sendChat, type ChatRequest } from "./api";
import { approvePrivacyDraft, createPrivacyDraft, removePrivacyClarification, reviewedRequestBody, updatePrivacyField } from "./clientPrivacy";

const base = (message = "請說明一般求助方向"): ChatRequest => ({ message, history: [], use_rag: true, contract_version: 4 });
const response = { reply: "收到", session_id: "synthetic", anonymized: false, rag_used: { status: false, sources: [] }, suggested_replies: [], action_buttons: [], interaction_mode: "answer", clarifying_questions: [], contract_version: 4, context_revision: 0, summary_update: null, clarification: null, analysis: [], execution: { route: "direct_support", model_calls: 1 } };
const tokenFor = (values: string[], extra: Record<string, unknown> = {}) => {
  const data = { question_id: "open.1", selection_mode: "single", max_selections: 1, contract_version: 4, context_revision: 0, context_scope: "personal", allowed_values: values, ...extra };
  const binary = Array.from(new TextEncoder().encode(JSON.stringify(data)), byte => String.fromCharCode(byte)).join("");
  return `${btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")}.${"a".repeat(64)}`;
};
const withAnswer = (values: string[], token = tokenFor(values)): ChatRequest => ({ ...base(), clarification_answer: { question_id: "open.1", status: "provided", value: values[0], context_revision: 0, selection_mode: "single", max_selections: 1, context_scope: "personal", allowed_values: values, validation_token: token } });
afterEach(() => vi.unstubAllGlobals());

describe("browser-only payload privacy", () => {
  it.each([
    "0912-345-678", "+886 912 345 678", "０９１２３４５６７８", "0912\u200b345678", "(02) 2345-6789", "02-2345-6789",
    "sample@example.test", "A123456789", "a123456789", "AB12345678", "4111 1111 1111 1111", "2001:db8::1", "192.168.1.7",
  ])("removes format-identifiable content locally: %s", value => {
    const draft = createPrivacyDraft(base(`聯絡方式 ${value}`));
    expect(draft.request.message).not.toContain(value);
    expect(draft.findings.length).toBeGreaterThan(0);
    expect(JSON.stringify(draft.findings)).not.toContain(value);
  });
  it("preserves legal references, dates, duration, general roles and emergency numbers", () => {
    const message = "期間20260930-20261001，事件在2026年9月30日，性騷擾防治法第32條之1，二年／五年。小節1.2.3.4。主管、公司、學校、台北市。110、113、119。";
    expect(createPrivacyDraft(base(message)).request.message).toBe(message);
    expect(createPrivacyDraft(base("卡號 20260930-20261001")).request.message).not.toContain("20260930");
  });
  it("checks both history roles and summary, strips unknown payload fields without modifying the original", () => {
    const request = { ...base("sample@example.test"), privateExtra: "private-extra", history: [
      { role: "user", content: "電話 0912345678", original: "private-extra" },
      { role: "assistant", content: "姓名：合成甲，請問" },
    ], case_context: { schema_version: 3, revision: 2, facts: {}, summary_origin: "user", summary: "地址：測試市測試路1號。" } } as ChatRequest;
    const before = JSON.stringify(request);
    const draft = createPrivacyDraft(request);
    const body = reviewedRequestBody(approvePrivacyDraft(draft));
    for (const secret of ["sample@example.test", "0912345678", "合成甲", "測試市測試路1號", "private-extra"]) expect(body).not.toContain(secret);
    expect(JSON.stringify(request)).toBe(before);
    expect(draft.fields.map(field => field.path.join("."))).toEqual(["message", "history.0.content", "history.1.content", "case_context.summary"]);
    expect(draft.request.case_context?.revision).toBe(2);
  });
  it("redacts every legacy fact value, including arrays", () => {
    const draft = createPrivacyDraft({ ...base(), contract_version: 3, case_context: { schema_version: 2, revision: 1, facts: { subject_role: { status: "provided", value: ["合成甲", "sample@example.test"] }, city: { status: "provided", value: "合成甲住在台北市" } } } }, ["合成甲"]);
    const body = reviewedRequestBody(approvePrivacyDraft(draft));
    expect(body).not.toContain("合成甲"); expect(body).not.toContain("sample@example.test");
    expect(body).toContain("台北市"); expect(body).toContain("[已隱藏資訊1]");
  });
  it("uses the same local alias throughout new text, history and a regeneration summary", () => {
    const draft = createPrivacyDraft({ ...base("合成甲在虛構公司遇到不受歡迎的言詞"), regenerate_from_summary: true,
      history: [{ role: "assistant", content: "合成甲" }], case_context: { schema_version: 3, revision: 3, facts: {}, summary: "虛構公司的合成甲", summary_origin: "user" } }, ["合成甲", "虛構公司"]);
    const body = reviewedRequestBody(approvePrivacyDraft(draft));
    expect(body).not.toContain("合成甲"); expect(body).not.toContain("虛構公司");
    expect(body.match(/已隱藏資訊1/g)).toHaveLength(3);
    expect(body).not.toContain("hiddenTerms");
  });
  it("rechecks edits and never approves a mutable copy of a review", () => {
    const draft = createPrivacyDraft(base());
    const edited = updatePrivacyField(draft, ["message"], "請聯絡 sample@example.test");
    expect(edited.request.message).toBe("請聯絡 [電子郵件]");
    const request = approvePrivacyDraft(edited);
    expect(Object.isFrozen(request)).toBe(true); expect(Object.isFrozen(request.history)).toBe(true);
    expect(() => { request.message = "sample@example.test"; }).toThrow();
    expect(() => reviewedRequestBody({ ...request })).toThrow("隱私檢查");
    expect(() => approvePrivacyDraft({ ...edited })).toThrow();
    expect(() => updatePrivacyField(draft, ["__proto__", "private"], "text")).toThrow();
  });
  it.each(["data:image/png;base64,synthetic", "", "data:image/svg+xml;base64,PHN2Zy8+", "https://example.test/image.png"])("rejects invalid image data before review or upload", image_base64 => {
    expect(() => createPrivacyDraft({ ...base(), image_base64 })).toThrow();
  });
  it("keeps validated original image data in the immutable prepared request", () => {
    const image_base64 = "data:image/png;base64,iVBORw0KGgo=";
    const draft = createPrivacyDraft({ ...base("sample@example.test"), image_base64 });
    expect(draft.warnings.join(" ")).toContain("原圖");
    const request = approvePrivacyDraft(updatePrivacyField(draft, ["message"], "請說明圖片 sample@example.test"));
    expect(JSON.parse(reviewedRequestBody(request))).toMatchObject({ image_base64, message: "請說明圖片 [電子郵件]" });
  });
  it("shows residual risk even when no pattern matched", () => {
    expect(createPrivacyDraft(base("某公司發生的罕見事件")).warnings.join(" ")).toContain("再識別風險");
  });
  it("keeps an oversized summary in review instead of silently storing an empty context", () => {
    const draft = createPrivacyDraft({ ...base(), case_context: { schema_version: 3, revision: 3, facts: {}, summary: "原摘要", summary_origin: "user" } });
    const edited = updatePrivacyField(draft, ["case_context", "summary"], "字".repeat(4001));
    expect(() => approvePrivacyDraft(edited)).toThrow("4,000");
    expect(draft.request.case_context).toMatchObject({ summary: "原摘要", revision: 3 });
    expect(approvePrivacyDraft(updatePrivacyField(edited, ["case_context", "summary"], "已修正摘要")).case_context).toMatchObject({ summary: "已修正摘要", revision: 3 });
  });
});

describe("signed clarification privacy", () => {
  it("shows all decoded signed options before preserving a clean token", () => {
    const request = withAnswer(["主管", "同事"]);
    const draft = createPrivacyDraft(request);
    expect(draft.request.clarification_answer?.validation_token).toBe(request.clarification_answer?.validation_token);
    const field = draft.fields.find(field => field.path[0] === "clarification_answer");
    expect(field?.text).toContain("同事"); expect(field?.readOnly).toBe(true);
    expect(field?.text).not.toContain(request.clarification_answer?.validation_token);
    expect(removePrivacyClarification(draft).request.clarification_answer).toBeUndefined();
  });
  it("drops the whole token when an unselected signed option contains PII", () => {
    const request = withAnswer(["主管", "sample@example.test"]);
    delete request.clarification_answer!.allowed_values;
    const draft = createPrivacyDraft(request);
    expect(draft.request.clarification_answer).toBeUndefined();
    expect(draft.warnings.join(" ")).toContain("已移除");
    expect(reviewedRequestBody(approvePrivacyDraft(draft))).not.toContain("validation_token");
  });
  it("drops human-selected hidden terms in signed options and unsafe freeform answers", () => {
    expect(createPrivacyDraft(withAnswer(["主管", "合成甲"]), ["合成甲"]).request.clarification_answer).toBeUndefined();
    const request = withAnswer(["主管"]); request.clarification_answer!.value = "0912345678";
    expect(createPrivacyDraft(request).request.clarification_answer).toBeUndefined();
  });
  it("checks original signed strings before JSON escapes newlines and quoted hidden terms", () => {
    expect(createPrivacyDraft(withAnswer(["主管", "0912\n345\n678"])).request.clarification_answer).toBeUndefined();
    const term = 'Synthetic "Alpha" Co.';
    expect(createPrivacyDraft(withAnswer(["主管", term]), [term]).request.clarification_answer).toBeUndefined();
  });
  it.each(["opaque-original-name", tokenFor(["主管"], { original: "private" }), tokenFor(["主管"], { question_id: "other.1" })])("never sends opaque or mismatched signed content", token => {
    expect(createPrivacyDraft(withAnswer(["主管"], token)).request.clarification_answer).toBeUndefined();
  });
});

describe("mandatory transport boundary", () => {
  it("sends zero requests without a prepared snapshot for chat and admin, even with backend PII off", async () => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    const request = createPrivacyDraft(base("sample@example.test")).request;
    await expect(sendChat(request)).rejects.toMatchObject({ code: "review_required" });
    await expect(runAdminChatTest("token", request, { enable_anonymization: false })).rejects.toMatchObject({ code: "review_required" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("serializes only the approved immutable text into chat and both admin PII runs", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(response), { headers: { "Content-Type": "application/json" } }))
      .mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ response, diagnostics: {} }), { headers: { "Content-Type": "application/json" } })));
    vi.stubGlobal("fetch", fetchMock);
    const approved = approvePrivacyDraft(createPrivacyDraft({ ...base("合成甲 0912345678"), history: [{ role: "assistant", content: "合成甲 sample@example.test" }] }, ["合成甲"]));
    await sendChat(approved);
    await runAdminChatTest("token", approved, { enable_anonymization: true });
    await runAdminChatTest("token", approved, { enable_anonymization: false });
    const bodies = fetchMock.mock.calls.map(call => JSON.parse(call[1].body as string));
    expect(bodies[0]).toEqual({ ...approved, stream: true });
    expect(bodies[1].request).toEqual(approved); expect(bodies[2].request).toEqual(approved);
    for (const secret of ["合成甲", "0912345678", "sample@example.test"]) expect(JSON.stringify(bodies)).not.toContain(secret);
  });
});
