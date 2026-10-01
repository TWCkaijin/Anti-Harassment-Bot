import { afterEach, describe, expect, it } from "vitest";
import type { ChatRequest } from "./api";
import { cancelPrivacyReview, confirmPrivacyReview, getPrivacyReview, requestPrivacyReview } from "./privacyReview";
import { reviewedRequestBody } from "./clientPrivacy";

const input = (): ChatRequest => ({ message: "synthetic@example.test", history: [{ role: "assistant", content: "0912345678" }], use_rag: true, contract_version: 4 });
afterEach(() => { while (getPrivacyReview()) cancelPrivacyReview(getPrivacyReview()!.id); });

describe("admin-controlled manual privacy review", () => {
  it("defaults to manual confirmation when the setting is missing", async () => {
    const pending = requestPrivacyReview(input());
    expect(getPrivacyReview()?.draft.request.message).toBe("[電子郵件]");
    confirmPrivacyReview(getPrivacyReview()!.id);
    const prepared = await pending;
    expect(JSON.parse(reviewedRequestBody(prepared!)).message).toBe("[電子郵件]");
  });
  it("skips the dialog but still masks all text and prepares an immutable request when disabled", async () => {
    const prepared = await requestPrivacyReview(input(), undefined, { enabled: false });
    expect(getPrivacyReview()).toBeNull();
    expect(Object.isFrozen(prepared)).toBe(true);
    const body = reviewedRequestBody(prepared!);
    expect(body).not.toContain("synthetic@example.test");
    expect(body).not.toContain("0912345678");
    expect(JSON.parse(body).history[0].content).toBe("[手機號碼]");
  });
  it("requires review again on the next call after re-enabling", async () => {
    await requestPrivacyReview(input(), undefined, { enabled: false });
    const pending = requestPrivacyReview(input(), undefined, { enabled: true });
    expect(getPrivacyReview()).not.toBeNull();
    cancelPrivacyReview(getPrivacyReview()!.id);
    await expect(pending).resolves.toBeNull();
  });
  it("does not allow malformed images or invalid lengths through the automatic path", async () => {
    await expect(requestPrivacyReview({ ...input(), image_base64: "data:image/png;base64,invalid" }, undefined, { enabled: false })).rejects.toThrow();
    await expect(requestPrivacyReview({ ...input(), message: "字".repeat(2001) }, undefined, { enabled: false })).rejects.toThrow("2,000");
    expect(getPrivacyReview()).toBeNull();
  });
  it("keeps cancellation effective when review is disabled", async () => {
    const controller = new AbortController(); controller.abort();
    await expect(requestPrivacyReview(input(), controller.signal, { enabled: false })).resolves.toBeNull();
    expect(getPrivacyReview()).toBeNull();
  });
});
